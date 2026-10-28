"""Exercise the actual patched receiver renderer, not a decode-only pipeline."""
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'build/iphone-video-r288-research'
FIXTURE=ROOT/'build/iphone-video-r287-research/unrestricted.au'
sys.path.insert(0,str(ROOT))
from iphone_mirror import decoder_preferences


@unittest.skipUnless(FIXTURE.is_file(),'first generate the synthetic fixtures with test_iphone_decoder_latency.py')
class RendererPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(exist_ok=True)
        build=runpy.run_path(str(ROOT/'native/airplay/build.py'))
        patch=runpy.run_path(str(ROOT/'native/airplay/windows_video.py'))
        upstream,sdk=build['UPSTREAM'],build['SDK']
        source=patch['patch_renderer']((upstream/'renderers/video_renderer.c').read_text())
        if os.environ.get('JJ_TEST_WITHOUT_UPLOAD')=='1':
            source=source.replace('"d3d11upload ! "','""')
        (WORK/'video_renderer.c').write_text(source,encoding='utf-8')
        flags=['-target','x86_64-windows-gnu','-O2','-g0','-DNDEBUG','-DWIN32','-D_WIN32_WINNT=0x0A00','-DWINSOCK2','-DSTANDALONE']
        for directory in (WORK,upstream,upstream/'renderers',upstream/'lib',sdk/'include',sdk/'include/glib-2.0',sdk/'lib/glib-2.0/include',sdk/'include/gstreamer-1.0'):
            flags+=['-I',str(directory)]
        cls.env=os.environ.copy()
        cls.env['ZIG_GLOBAL_CACHE_DIR']=str(ROOT/'build/native-toolchain/cache')
        cls.env['ZIG_LOCAL_CACHE_DIR']=str(WORK/'zig-cache')
        libraries=['gstapp-1.0','gstvideo-1.0','gstbase-1.0','gstreamer-1.0','gobject-2.0','glib-2.0','winpthread']
        command=[str(build['ZIG']),'cc',*flags,str(ROOT/'tests/native/iphone_renderer_probe.c'),str(upstream/'lib/logger.c'),*[str(sdk/'lib'/('lib'+name+'.dll.a')) for name in libraries],'-luser32','-lws2_32','-o',str(WORK/'renderer_probe.exe')]
        compile_result=subprocess.run(command,env=cls.env,capture_output=True,text=True,timeout=180)
        (WORK/'compile.log').write_text(compile_result.stdout+compile_result.stderr,encoding='utf-8')
        if compile_result.returncode:raise AssertionError(compile_result.stderr)
        runtime=ROOT/'build/airplay-research/runtime'
        cls.env['PATH']=str(runtime)+os.pathsep+cls.env['PATH']
        cls.env['GST_PLUGIN_PATH_1_0']=cls.env['GST_PLUGIN_SYSTEM_PATH_1_0']=str(runtime/'lib/gstreamer-1.0')
        cls.env['GST_REGISTRY_1_0']=str(WORK/'registry.bin');cls.env['GST_REGISTRY_FORK']='no'
        cls.results={}

    def run_renderer(self,software):
        env=dict(self.env,GST_PLUGIN_FEATURE_RANK=decoder_preferences(software))
        result=subprocess.run([str(WORK/'renderer_probe.exe'),str(FIXTURE)],env=env,capture_output=True,text=True,timeout=20)
        name='software' if software else 'hardware'
        (WORK/(name+'.log')).write_text(result.stdout+result.stderr,encoding='utf-8')
        self.results[name]={'exitCode':result.returncode,'output':result.stdout,'error':result.stderr}
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('JJ_AIRPLAY_FIRST_FRAME_MS ',result.stdout)
        self.assertRegex(result.stdout,r'PRESENTED \d+ 1 498 1080 ')
        self.assertIn('JJ_AIRPLAY_DECODER '+('avdec_h264' if software else 'd3d11h264dec'),result.stdout)

    def test_software_decode_upload_convert_and_real_sink(self):
        self.run_renderer(True)

    def test_hardware_decode_still_reaches_real_sink(self):
        self.run_renderer(False)

    @classmethod
    def tearDownClass(cls):
        (WORK/'production-renderer-results.json').write_text(json.dumps(cls.results,indent=2),encoding='utf-8')


if __name__=='__main__':unittest.main(verbosity=2)
