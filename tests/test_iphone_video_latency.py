"""Use actual GStreamer queues and synthetic frames, never phone content."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from iphone_mirror import MirrorSession
APP = QApplication.instance() or QApplication([])

HARNESS = r'''
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <stdio.h>
#include <string.h>
/* VIDEO_STATS */
void jj_get_video_size(int *w, int *h) { *w=640; *h=360; }
static double total_age;
static guint measured;
static gpointer produce(gpointer object) {
    GstAppSrc *source=GST_APP_SRC(object);
    gint64 started=g_get_monotonic_time();
    for(guint frame=0;frame<180;frame++) {
        gint64 wait=started+(gint64)frame*1000000/60-g_get_monotonic_time();
        if(wait>0)g_usleep((gulong)wait);
        GstBuffer *buffer=gst_buffer_new_allocate(NULL,768,NULL);
        gst_buffer_memset(buffer,0,0,768);
        GST_BUFFER_PTS(buffer)=(guint64)frame*GST_SECOND/60;
        GST_BUFFER_DURATION(buffer)=GST_SECOND/60;
        if(gst_app_src_push_buffer(source,buffer)!=GST_FLOW_OK)break;
    }
    gst_app_src_end_of_stream(source);
    return NULL;
}
static GstPadProbeReturn ingress(GstPad *pad, GstPadProbeInfo *info, gpointer unused) {
    jj_video_ingress(GST_PAD_PROBE_INFO_BUFFER(info));
    return GST_PAD_PROBE_OK;
}
static GstPadProbeReturn output(GstPad *pad, GstPadProbeInfo *info, gpointer pipeline) {
    GstBuffer *buffer=GST_PAD_PROBE_INFO_BUFFER(info);
    gint64 now=g_get_monotonic_time();
    g_mutex_lock(&jj_stats_lock);
    for (guint n=0;n<256;n++) {
        JJVideoStamp *s=&jj_stamps[(jj_stamp_index+255-n)%256];
        if(s->arrived&&s->pts==GST_BUFFER_PTS(buffer)) {
            total_age+=(now-s->arrived)/1000.0; measured++; break;
        }
    }
    g_mutex_unlock(&jj_stats_lock);
    jj_video_frame(buffer,GST_ELEMENT(pipeline));
    return GST_PAD_PROBE_OK;
}
int main(int argc,char **argv) {
    gst_init(&argc,&argv);
    for(int limit=2;limit>=1;--limit) {
        gchar *launch=g_strdup_printf("appsrc name=source is-live=true format=time caps=video/x-raw,format=RGB,width=16,height=16,framerate=60/1 ! queue name=jj_decoded_queue max-size-buffers=%d max-size-bytes=0 max-size-time=0 leaky=downstream ! identity sleep-time=40000 ! fakesink name=out sync=false",limit);
        GError *error=NULL;
        GstElement *pipeline=gst_parse_launch(launch,&error);
        g_free(launch);
        if(error||!pipeline) {fprintf(stderr,"pipeline error: %s\n",error?error->message:"unknown");return 2;}
        jj_stats_reset(TRUE); total_age=0; measured=0;
        GstElement *queue=gst_bin_get_by_name(GST_BIN(pipeline),"jj_decoded_queue");
        GstElement *sink=gst_bin_get_by_name(GST_BIN(pipeline),"out");
        GstPad *in=gst_element_get_static_pad(queue,"sink");
        GstPad *out=gst_element_get_static_pad(sink,"sink");
        gst_pad_add_probe(in,GST_PAD_PROBE_TYPE_BUFFER,ingress,NULL,NULL);
        gst_pad_add_probe(out,GST_PAD_PROBE_TYPE_BUFFER,output,pipeline,NULL);
        gst_object_unref(in);gst_object_unref(out);gst_object_unref(queue);gst_object_unref(sink);
        gst_element_set_state(pipeline,GST_STATE_PLAYING);
        GstElement *source=gst_bin_get_by_name(GST_BIN(pipeline),"source");
        GThread *producer=g_thread_new("synthetic-frames",produce,source);
        GstBus *bus=gst_element_get_bus(pipeline);
        GstMessage *message=gst_bus_timed_pop_filtered(bus,12*GST_SECOND,GST_MESSAGE_EOS|GST_MESSAGE_ERROR);
        gboolean ok=message&&GST_MESSAGE_TYPE(message)==GST_MESSAGE_EOS;
        gst_element_set_state(pipeline,GST_STATE_NULL);
        g_thread_join(producer);gst_object_unref(source);
        if(message)gst_message_unref(message);
        gst_object_unref(bus);gst_object_unref(pipeline);
        if(!ok||!measured) {fprintf(stderr,"pipeline did not finish\n");return 3;}
        printf("BENCH %d %u %.3f\n",limit,measured,total_age/measured);
    }
    return 0;
}
'''


class VideoLatencyTests(unittest.TestCase):
    def test_streaming_state_waits_for_first_decoded_frame(self):
        session = MirrorSession()
        def update(state, detail):
            session.state, session.detail = state, detail
        with patch.object(session, '_state', side_effect=update):
            session._output(0, 'JJ_AIRPLAY_CONNECTED')
            session._output(0, 'JJ_AIRPLAY_STREAMING')
            self.assertEqual(session.state, 'connected')
            session._output(0, 'JJ_AIRPLAY_FIRST_FRAME_MS nan')
            self.assertEqual(session.state, 'connected')
            session._output(0, 'JJ_AIRPLAY_FIRST_FRAME_MS 18.5')
            self.assertEqual(session.state, 'streaming')
            self.assertEqual(session.video['codecToFirstSinkMs'], 18.5)
            session._output(0, 'JJ_AIRPLAY_STREAMING')
            self.assertEqual(session.state, 'streaming')

    def test_stats_parser_preserves_unknown_age_and_rejects_invalid_numbers(self):
        session = MirrorSession()
        with patch.object(session, '_state'):
            session._output(0, 'JJ_AIRPLAY_VIDEO_STATS 60 -1 -1 18 0 1 0 d3d11h264dec 498 1080')
            self.assertIsNone(session.video['receiverToSinkAvgMs'])
            session._output(0, 'JJ_AIRPLAY_VIDEO_STATS 59.9 3.2 7.1 18 0 0 120 d3d11h264dec 498 1080')
            self.assertEqual(session.video['receiverToSinkAvgMs'], 3.2)
            self.assertEqual(session.video['width'], 498)
            previous = dict(session.video)
            session._output(0, 'JJ_AIRPLAY_VIDEO_STATS nan 0 0 0 0 0 1 fake 0 0')
            self.assertEqual(session.video, previous)

    def test_actual_raw_queue_keeps_newest_frame_with_less_backlog(self):
        build = runpy.run_path(str(ROOT/'native/airplay/build.py'))
        work=ROOT/'build/iphone-video-r286-tests'
        work.mkdir(exist_ok=True)
        sdk=build['SDK']
        source=work/'queues.c'
        source.write_text(HARNESS.replace('/* VIDEO_STATS */',(ROOT/'native/airplay/video_stats.c').read_text(encoding='utf-8')),encoding='utf-8')
        command=[str(build['ZIG']),'cc','-target','x86_64-windows-gnu','-O2','-g0','-D_WIN32_WINNT=0x0a00']
        for directory in (sdk/'include',sdk/'include/glib-2.0',sdk/'lib/glib-2.0/include',sdk/'include/gstreamer-1.0'):
            command+=['-I',str(directory)]
        command += [str(source),*[str(sdk/'lib'/('lib'+name+'.dll.a')) for name in ('gstreamer-1.0','gstapp-1.0','gobject-2.0','glib-2.0')],'-o',str(work/'queues.exe')]
        env=os.environ.copy()
        env['ZIG_GLOBAL_CACHE_DIR']=str(ROOT/'build/native-toolchain/cache')
        env['ZIG_LOCAL_CACHE_DIR']=str(work/'zig-cache')
        temporary=work/'tmp';temporary.mkdir(exist_ok=True)
        env['TEMP']=env['TMP']=str(temporary)
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        runtime=ROOT/'build/airplay-research/runtime'
        env['PATH']=str(runtime)+os.pathsep+env['PATH']
        env['GST_PLUGIN_PATH_1_0']=env['GST_PLUGIN_SYSTEM_PATH_1_0']=str(runtime/'lib/gstreamer-1.0')
        env['GST_REGISTRY_1_0']=str(ROOT/'build/iphone-video-r286-inspect-registry.bin')
        env['GST_REGISTRY_FORK']='no'
        result=subprocess.run([str(work/'queues.exe')],env=env,capture_output=True,text=True,timeout=35)
        (work/'queues-output.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        rows={}
        for line in result.stdout.splitlines():
            if line.startswith('BENCH '):
                _,limit,count,age=line.split();rows[int(limit)]=dict(frames=int(count),meanAgeMs=float(age))
        self.assertEqual(set(rows),{1,2})
        self.assertLess(rows[1]['meanAgeMs'],rows[2]['meanAgeMs']-4)
        (work/'queue-comparison.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
        print(json.dumps(rows))


if __name__=='__main__':
    unittest.main(verbosity=2)
