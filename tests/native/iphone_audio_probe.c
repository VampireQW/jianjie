/* Run only against private test pipelines; all PCM samples are silent. */
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <gst/base/gstbasesink.h>
#include <string.h>

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    gst_init(NULL, NULL);
    GError *error = NULL;
    gchar *description = g_strdup_printf(
        "appsrc name=source is-live=true format=time caps=\"audio/x-raw,format=S16LE,rate=44100,channels=2,layout=interleaved\" "
        "! queue ! audioconvert ! audioresample quality=10 ! volume ! level ! %s name=output", argv[2]);
    GstElement *pipeline = gst_parse_launch(description, &error);
    g_free(description);
    if (!pipeline || error) {
        g_printerr("Parse failed: %s\n", error ? error->message : "no pipeline");
        return 3;
    }
    GstElement *sink = gst_bin_get_by_name(GST_BIN(pipeline), "output");
    GstElement *injected = gst_bin_get_by_name(GST_BIN(pipeline), "injected");
    gchar *device = NULL;
    if (g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "device"))
        g_object_get(sink, "device", &device, NULL);
    if (g_strcmp0(device ? device : "", argv[1]) || injected) return 4;
    g_free(device);
    if (argc > 3 && !strcmp(argv[3], "--parse-only")) {
        g_print("DEVICE_STRING_ROUNDTRIP_OK\n");
        gst_object_unref(sink);
        gst_object_unref(pipeline);
        return 0;
    }
    GstElement *source = gst_bin_get_by_name(GST_BIN(pipeline), "source");
    GstBus *bus = gst_element_get_bus(pipeline);
    if (gst_element_set_state(pipeline, GST_STATE_PLAYING) == GST_STATE_CHANGE_FAILURE) return 5;
    for (int i = 0; i < 20; i++) {
        GstBuffer *buffer = gst_buffer_new_allocate(NULL, 441 * 4, NULL);
        gst_buffer_memset(buffer, 0, 0, 441 * 4);
        GST_BUFFER_PTS(buffer) = i * 10 * GST_MSECOND;
        GST_BUFFER_DURATION(buffer) = 10 * GST_MSECOND;
        if (gst_app_src_push_buffer(GST_APP_SRC(source), buffer) != GST_FLOW_OK) return 6;
    }
    gst_app_src_end_of_stream(GST_APP_SRC(source));
    GstMessage *message = gst_bus_timed_pop_filtered(bus, 5 * GST_SECOND, GST_MESSAGE_ERROR | GST_MESSAGE_EOS);
    int result = 0;
    if (!message || GST_MESSAGE_TYPE(message) != GST_MESSAGE_EOS) {
        if (message) {
            gst_message_parse_error(message, &error, NULL);
            g_printerr("Playback failed: %s\n", error->message);
        }
        result = 7;
    } else {
        GstElement *actual = sink;
        if (GST_IS_BIN(sink)) {
            GstIterator *iterator = gst_bin_iterate_sinks(GST_BIN(sink));
            GValue value = G_VALUE_INIT;
            if (gst_iterator_next(iterator, &value) != GST_ITERATOR_OK) return 9;
            actual = GST_ELEMENT(g_value_dup_object(&value));
            g_value_unset(&value);
            gst_iterator_free(iterator);
        }
        const gchar *factory = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(gst_element_get_factory(actual)));
        if (strcmp(factory, "wasapi2sink")) return 10;
        GstStructure *stats = gst_base_sink_get_stats(GST_BASE_SINK(actual));
        guint64 rendered = 0;
        gst_structure_get_uint64(stats, "rendered", &rendered);
        g_print("SILENT_PLAYBACK_EOS rendered=%" G_GUINT64_FORMAT " sink=%s\n", rendered, factory);
        gst_structure_free(stats);
        if (actual != sink) gst_object_unref(actual);
        if (!rendered) result = 8;
    }
    if (message) gst_message_unref(message);
    gst_element_set_state(pipeline, GST_STATE_NULL);
    gst_object_unref(source);
    gst_object_unref(sink);
    gst_object_unref(bus);
    gst_object_unref(pipeline);
    return result;
}
