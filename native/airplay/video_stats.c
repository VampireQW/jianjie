/* Bounded receive-to-sink telemetry. No frame bytes, pixels or input are logged. */
typedef struct { GstClockTime pts; gint64 arrived; } JJVideoStamp;
static GMutex jj_stats_lock;
static JJVideoStamp jj_stamps[256];
static guint jj_stamp_index;
static gboolean jj_low_latency;
static guint64 jj_frames, jj_matches;
static gint64 jj_interval_start, jj_last_frame, jj_age_sum, jj_age_max, jj_gap_max;
static gchar jj_decoder_name[64] = "unknown";
void jj_get_video_size(int *width, int *height);

static void jj_stats_reset(gboolean low_latency) {
    g_mutex_lock(&jj_stats_lock);
    jj_low_latency = low_latency;
    jj_frames = jj_matches = 0;
    jj_interval_start = jj_last_frame = jj_age_sum = jj_age_max = jj_gap_max = 0;
    jj_stamp_index = 0;
    memset(jj_stamps, 0, sizeof(jj_stamps));
    g_strlcpy(jj_decoder_name, "unknown", sizeof(jj_decoder_name));
    g_mutex_unlock(&jj_stats_lock);
}

static void jj_video_ingress(GstBuffer *buffer) {
    if (!GST_CLOCK_TIME_IS_VALID(GST_BUFFER_PTS(buffer))) return;
    g_mutex_lock(&jj_stats_lock);
    jj_stamps[jj_stamp_index].pts = GST_BUFFER_PTS(buffer);
    jj_stamps[jj_stamp_index].arrived = g_get_monotonic_time();
    jj_stamp_index = (jj_stamp_index + 1) % 256;
    g_mutex_unlock(&jj_stats_lock);
}

static void jj_video_configure_decoder(GstElement *element) {
    GstElementFactory *factory = gst_element_get_factory(element);
    if (!factory) return;
    const gchar *kind = gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS);
    if (!kind || !strstr(kind, "Decoder") || !strstr(kind, "Video")) return;
    const gchar *name = gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory));
    if (jj_low_latency) {
        GObjectClass *klass = G_OBJECT_GET_CLASS(element);
        if (g_str_has_prefix(name, "avdec_") && g_object_class_find_property(klass, "max-threads"))
            g_object_set(element, "max-threads", 1, NULL);
        if (g_object_class_find_property(klass, "max-display-delay"))
            g_object_set(element, "max-display-delay", 0, NULL);
    }
    g_mutex_lock(&jj_stats_lock);
    g_strlcpy(jj_decoder_name, name, sizeof(jj_decoder_name));
    g_mutex_unlock(&jj_stats_lock);
    printf("JJ_AIRPLAY_DECODER %s\n", name);
}

static void jj_video_scan_decoders(GstElement *pipeline) {
    GstIterator *iterator = gst_bin_iterate_recurse(GST_BIN(pipeline));
    GValue value = G_VALUE_INIT;
    gboolean done = FALSE;
    while (!done) {
        switch (gst_iterator_next(iterator, &value)) {
        case GST_ITERATOR_OK:
            jj_video_configure_decoder(GST_ELEMENT(g_value_get_object(&value)));
            g_value_reset(&value); break;
        case GST_ITERATOR_RESYNC: gst_iterator_resync(iterator); break;
        default: done = TRUE; break;
        }
    }
    if (G_IS_VALUE(&value)) g_value_unset(&value);
    gst_iterator_free(iterator);
}

static guint jj_queue_depth(GstElement *pipeline, const gchar *name) {
    GstElement *queue = gst_bin_get_by_name(GST_BIN(pipeline), name);
    if (!queue) return 0;
    guint depth = 0;
    g_object_get(queue, "current-level-buffers", &depth, NULL);
    gst_object_unref(queue);
    return depth;
}

static void jj_video_frame(GstBuffer *buffer, GstElement *pipeline) {
    gint64 now = g_get_monotonic_time(), interval, age_sum = 0, age_max = 0, gap_max = 0;
    guint64 frames = 0, matches = 0;
    gchar decoder[64];
    g_mutex_lock(&jj_stats_lock);
    if (!jj_interval_start) jj_interval_start = now;
    else ++jj_frames;
    if (jj_last_frame) jj_gap_max = MAX(jj_gap_max, now - jj_last_frame);
    jj_last_frame = now;
    GstClockTime pts = GST_BUFFER_PTS(buffer);
    if (GST_CLOCK_TIME_IS_VALID(pts)) {
        for (guint n = 0; n < 256; ++n) {
            JJVideoStamp *stamp = &jj_stamps[(jj_stamp_index + 255 - n) % 256];
            if (stamp->arrived && stamp->pts == pts) {
                gint64 age = MAX((gint64) 0, now - stamp->arrived);
                jj_age_sum += age; jj_age_max = MAX(jj_age_max, age); ++jj_matches;
                break;
            }
        }
    }
    interval = now - jj_interval_start;
    if (interval >= 2000000) {
        frames = jj_frames; matches = jj_matches;
        age_sum = jj_age_sum; age_max = jj_age_max; gap_max = jj_gap_max;
        g_strlcpy(decoder, jj_decoder_name, sizeof(decoder));
        jj_frames = jj_matches = 0;
        jj_age_sum = jj_age_max = jj_gap_max = 0;
        jj_interval_start = now;
    }
    g_mutex_unlock(&jj_stats_lock);
    if (frames) {
        int width = 0, height = 0;
        jj_get_video_size(&width, &height);
        printf("JJ_AIRPLAY_VIDEO_STATS %.3f %.3f %.3f %.3f %u %u %llu %s %d %d\n",
            (double) frames * 1000000.0 / interval,
            matches ? (double) age_sum / matches / 1000.0 : -1.0,
            matches ? (double) age_max / 1000.0 : -1.0, (double) gap_max / 1000.0,
            jj_queue_depth(pipeline, "jj_encoded_queue"), jj_queue_depth(pipeline, "jj_decoded_queue"),
            (unsigned long long) matches, decoder, width, height);
    }
}
