"""Windows window branding and bounded live-video queues for the pinned receiver."""

BRANDING = r'''
static HWND jj_video_window = NULL;
static HICON jj_icon_small = NULL;
static HICON jj_icon_large = NULL;
static const wchar_t *jj_window_title = L"\u7b80\u622a\u6295\u5c4f";
static const wchar_t *jj_original_proc_key = L"JianJie.OriginalVideoProc";
static const wchar_t *jj_fitted_key = L"JianJie.VideoWindowFitted";
static const wchar_t *jj_presented_key = L"JianJie.VideoWindowPresented";
static const wchar_t *jj_topmost_key = L"JianJie.TemporaryVideoTopmost";
static HWND jj_temporarily_topmost = NULL;
static ULONGLONG jj_topmost_until = 0;
extern "C" void jj_get_video_size(int *width, int *height);

static bool jj_fit_window(HWND window, WINDOWPOS *position) {
    if (GetPropW(window, jj_fitted_key) || IsZoomed(window)) return false;
    int video_width = 0, video_height = 0;
    jj_get_video_size(&video_width, &video_height);
    if (video_width <= 0 || video_height <= 0) return false;
    MONITORINFO monitor = {0};
    monitor.cbSize = sizeof(monitor);
    if (!GetMonitorInfoW(MonitorFromWindow(window, MONITOR_DEFAULTTONEAREST), &monitor))
        return false;
    DWORD style = (DWORD) GetWindowLongPtrW(window, GWL_STYLE);
    DWORD extended = (DWORD) GetWindowLongPtrW(window, GWL_EXSTYLE);
    if (!(style & WS_CAPTION)) return false;
    RECT frame = {0, 0, 100, 100};
    typedef BOOL (WINAPI *AdjustForDpi)(LPRECT, DWORD, BOOL, DWORD, UINT);
    typedef UINT (WINAPI *WindowDpi)(HWND);
    HMODULE user = GetModuleHandleW(L"user32.dll");
    AdjustForDpi adjust = (AdjustForDpi) GetProcAddress(user, "AdjustWindowRectExForDpi");
    WindowDpi dpi = (WindowDpi) GetProcAddress(user, "GetDpiForWindow");
    if (adjust && dpi) adjust(&frame, style, FALSE, extended, dpi(window));
    else AdjustWindowRectEx(&frame, style, FALSE, extended);
    int border_width = frame.right - frame.left - 100;
    int border_height = frame.bottom - frame.top - 100;
    int work_width = monitor.rcWork.right - monitor.rcWork.left;
    int work_height = monitor.rcWork.bottom - monitor.rcWork.top;
    if (work_width < 160 || work_height < 160)
        return false;
    double width_scale = (work_width * 0.95 - border_width) / video_width;
    double height_scale = (work_height * 0.95 - border_height) / video_height;
    double scale = width_scale < height_scale ? width_scale : height_scale;
    position->cx = (int) (video_width * scale) + border_width;
    position->cy = (int) (video_height * scale) + border_height;
    position->x = monitor.rcWork.left + (work_width - position->cx) / 2;
    position->y = monitor.rcWork.top + (work_height - position->cy) / 2;
    position->flags &= ~(SWP_NOMOVE | SWP_NOSIZE);
    SetPropW(window, jj_fitted_key, (HANDLE) 1);
    printf("JJ_AIRPLAY_WINDOW_FITTED %d %d\n", position->cx, position->cy);
    return true;
}

static LRESULT CALLBACK jj_video_proc(HWND window, UINT message, WPARAM word, LPARAM data) {
    WNDPROC original = (WNDPROC) GetPropW(window, jj_original_proc_key);
    /* GStreamer's internal class uses DefWindowProcA. Keep caption messages Unicode. */
    if (message == WM_SETTEXT || message == WM_GETTEXT || message == WM_GETTEXTLENGTH ||
        message == WM_NCPAINT || message == WM_NCACTIVATE)
        return DefWindowProcW(window, message, word, data);
    if (message == WM_WINDOWPOSCHANGING && data) {
        WINDOWPOS *position = (WINDOWPOS *) data;
        if (!(position->flags & SWP_NOSIZE)) jj_fit_window(window, position);
    }
    LRESULT result = original ? CallWindowProcA(original, window, message, word, data)
                              : DefWindowProcW(window, message, word, data);
    if (message == WM_NCDESTROY) {
        RemovePropW(window, jj_original_proc_key);
        RemovePropW(window, jj_fitted_key);
        RemovePropW(window, jj_presented_key);
        RemovePropW(window, jj_topmost_key);
    }
    return result;
}

static void jj_prepare_window(HWND window) {
    if (!GetPropW(window, jj_original_proc_key)) {
        WNDPROC original = (WNDPROC) GetWindowLongPtrA(window, GWLP_WNDPROC);
        if (original && SetPropW(window, jj_original_proc_key, (HANDLE) original)) {
            SetLastError(0);
            if (!SetWindowLongPtrW(window, GWLP_WNDPROC, (LONG_PTR) jj_video_proc) && GetLastError())
                RemovePropW(window, jj_original_proc_key);
        }
    }
    if (jj_icon_small) SendMessageTimeoutW(window, WM_SETICON, ICON_SMALL,
        (LPARAM) jj_icon_small, SMTO_ABORTIFHUNG, 100, NULL);
    if (jj_icon_large) SendMessageTimeoutW(window, WM_SETICON, ICON_BIG,
        (LPARAM) jj_icon_large, SMTO_ABORTIFHUNG, 100, NULL);
    SetWindowTextW(window, jj_window_title);
    if (GetWindowTextLengthW(window) == (int) wcslen(jj_window_title))
        puts("JJ_AIRPLAY_WINDOW_BRANDED");
    if (IsWindowVisible(window) && !GetPropW(window, jj_fitted_key)) {
        RECT rectangle;
        if (GetWindowRect(window, &rectangle)) {
            WINDOWPOS position = {window, NULL, rectangle.left, rectangle.top,
                rectangle.right - rectangle.left, rectangle.bottom - rectangle.top, SWP_NOZORDER | SWP_NOACTIVATE};
            if (jj_fit_window(window, &position))
                SetWindowPos(window, NULL, position.x, position.y, position.cx, position.cy, position.flags);
        }
    }
}

static void jj_present_window_once(HWND window) {
    if (!IsWindowVisible(window) || !GetPropW(window, jj_fitted_key) ||
        GetPropW(window, jj_presented_key)) return;
    bool was_topmost = ((DWORD) GetWindowLongPtrW(window, GWL_EXSTYLE) & WS_EX_TOPMOST) != 0;
    if (!SetWindowPos(window, was_topmost ? HWND_TOP : HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)) return;
    SetPropW(window, jj_presented_key, (HANDLE) 1);
    if (!was_topmost) {
        SetPropW(window, jj_topmost_key, (HANDLE) 1);
        jj_temporarily_topmost = window;
        jj_topmost_until = GetTickCount64() + 2000;
    }
    if (IsIconic(window)) ShowWindowAsync(window, SW_RESTORE);
    SetForegroundWindow(window);
    printf("JJ_AIRPLAY_WINDOW_PRESENTED %d\n", GetForegroundWindow() == window ? 1 : 0);
}

static void jj_release_temporary_topmost() {
    if (!jj_topmost_until || (jj_video_seen && GetTickCount64() < jj_topmost_until)) return;
    DWORD owner = 0;
    if (IsWindow(jj_temporarily_topmost)) GetWindowThreadProcessId(jj_temporarily_topmost, &owner);
    if (owner == GetCurrentProcessId() && GetPropW(jj_temporarily_topmost, jj_topmost_key)) {
        SetWindowPos(jj_temporarily_topmost, HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        RemovePropW(jj_temporarily_topmost, jj_topmost_key);
    }
    jj_temporarily_topmost = NULL;
    jj_topmost_until = 0;
}

static BOOL CALLBACK jj_find_video_window(HWND window, LPARAM) {
    DWORD pid = 0;
    GetWindowThreadProcessId(window, &pid);
    if (pid != GetCurrentProcessId()) return TRUE;
    wchar_t title[256] = {0}, classname[128] = {0};
    GetWindowTextW(window, title, 256);
    GetClassNameW(window, classname, 128);
    if (_wcsicmp(classname, L"GSTD3D11") == 0 || wcsstr(title, L"Direct3D11") ||
        wcscmp(title, jj_window_title) == 0) {
        jj_video_window = window;
        jj_prepare_window(window);
        return FALSE;
    }
    return TRUE;
}

static void jj_brand_window() {
    jj_release_temporary_topmost();
    DWORD owner = 0;
    if (IsWindow(jj_video_window)) GetWindowThreadProcessId(jj_video_window, &owner);
    if (!jj_video_seen) {
        if (owner == GetCurrentProcessId()) RemovePropW(jj_video_window, jj_presented_key);
        return;
    }
    if (owner != GetCurrentProcessId()) {
        jj_video_window = NULL;
        EnumWindows(jj_find_video_window, 0);
    } else {
        wchar_t title[256] = {0};
        GetWindowTextW(jj_video_window, title, 256);
        if (wcscmp(title, jj_window_title) != 0 ||
            (IsWindowVisible(jj_video_window) && !GetPropW(jj_video_window, jj_fitted_key)))
            jj_prepare_window(jj_video_window);
    }
    if (jj_video_window) jj_present_window_once(jj_video_window);
}
'''

TELEMETRY = r'''
static gint jj_first_frame_seen = 0;
static gint64 jj_codec_start_usec = 0;
static GMutex jj_video_size_lock;
static int jj_frame_width = 0, jj_frame_height = 0;
void jj_get_video_size(int *width, int *height) {
    g_mutex_lock(&jj_video_size_lock);
    *width = jj_frame_width;
    *height = jj_frame_height;
    g_mutex_unlock(&jj_video_size_lock);
}
static GstPadProbeReturn jj_video_caps(GstPad *pad, GstPadProbeInfo *info, gpointer unused) {
    if (GST_PAD_PROBE_INFO_TYPE(info) & GST_PAD_PROBE_TYPE_BUFFER) {
        if (g_atomic_int_compare_and_exchange(&jj_first_frame_seen, 0, 1) && jj_codec_start_usec)
            printf("JJ_AIRPLAY_FIRST_FRAME_MS %.3f\n", (g_get_monotonic_time() - jj_codec_start_usec) / 1000.0);
        return GST_PAD_PROBE_OK;
    }
    GstEvent *event = GST_PAD_PROBE_INFO_EVENT(info);
    if (event && GST_EVENT_TYPE(event) == GST_EVENT_CAPS) {
        GstCaps *caps = NULL;
        gst_event_parse_caps(event, &caps);
        if (caps && !gst_caps_is_empty(caps)) {
            const GstStructure *value = gst_caps_get_structure(caps, 0);
            gint width = 0, height = 0;
            if (gst_structure_get_int(value, "width", &width) &&
                gst_structure_get_int(value, "height", &height) && width > 0 && height > 0) {
                g_mutex_lock(&jj_video_size_lock);
                jj_frame_width = width;
                jj_frame_height = height;
                g_mutex_unlock(&jj_video_size_lock);
                printf("JJ_AIRPLAY_VIDEO_SIZE %d %d\n", width, height);
            }
        }
    }
    return GST_PAD_PROBE_OK;
}

static void jj_decoder_added(GstBin *pipeline, GstBin *sub, GstElement *element, gpointer unused) {
    GstElementFactory *factory = gst_element_get_factory(element);
    if (!factory) return;
    const gchar *kind = gst_element_factory_get_metadata(factory, GST_ELEMENT_METADATA_KLASS);
    if (kind && strstr(kind, "Decoder") && strstr(kind, "Video"))
        printf("JJ_AIRPLAY_DECODER %s\n", gst_plugin_feature_get_name(GST_PLUGIN_FEATURE(factory)));
}
'''


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError('Pinned video source changed: ' + before[:65])
    return text.replace(before, after, 1)


def patch_main(text):
    text = replace_once(text, '#include <shellapi.h>', '#include <shellapi.h>\n#include <shobjidl.h>\n#include <atomic>')
    text = replace_once(text, 'static bool jj_video_seen = false;', 'static std::atomic<bool> jj_video_seen(false);')
    text = replace_once(text, 'static gboolean jj_stop_watch(gpointer) {',
                        BRANDING + '\nstatic gboolean jj_stop_watch(gpointer) {\n    jj_brand_window();')
    text = replace_once(text, '    setvbuf(stdout, NULL, _IONBF, 0);', r'''
    SetCurrentProcessExplicitAppUserModelID(L"JianJie.ScreenMirroring");
    typedef BOOL (WINAPI *SetDpiContext)(HANDLE);
    SetDpiContext jj_set_dpi = (SetDpiContext) GetProcAddress(GetModuleHandleW(L"user32.dll"), "SetProcessDpiAwarenessContext");
    if (jj_set_dpi) jj_set_dpi((HANDLE) (LONG_PTR) -4);
    HINSTANCE jj_module = GetModuleHandleW(NULL);
    jj_icon_small = (HICON) LoadImageW(jj_module, MAKEINTRESOURCEW(101), IMAGE_ICON,
        GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), LR_DEFAULTCOLOR);
    jj_icon_large = (HICON) LoadImageW(jj_module, MAKEINTRESOURCEW(101), IMAGE_ICON,
        GetSystemMetrics(SM_CXICON), GetSystemMetrics(SM_CYICON), LR_DEFAULTCOLOR);
    setvbuf(stdout, NULL, _IONBF, 0);''')
    return text


def patch_renderer(text):
    text = replace_once(text, '#include <gst/gst.h>', '#include <stdio.h>\n#include <gst/gst.h>')
    text = replace_once(text, 'static uintptr_t external_window_handle = 0;',
                        TELEMETRY + '\nstatic uintptr_t external_window_handle = 0;')
    text = replace_once(text, '    logger = render_logger;', '''    logger = render_logger;
    g_mutex_lock(&jj_video_size_lock);
    jj_frame_width = jj_frame_height = 0;
    g_mutex_unlock(&jj_video_size_lock);''')
    text = replace_once(text, 'g_string_append(launch, "queue ! ");',
                        'g_string_append(launch, "queue max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! ");')
    text = replace_once(text, '                    g_string_append(launch, decoder);', '''                    g_string_append(launch, decoder);
                    g_string_append(launch, " ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0");
                    if (!video_sync) g_string_append(launch, " leaky=downstream");''')
    text = replace_once(text, '                g_string_append(launch, "videoscale ! ");', '''                if (strcmp(converter, "d3d11convert") || strcmp(videosink, "d3d11videosink"))
                    g_string_append(launch, "videoscale ! ");''')
    text = replace_once(text, '                g_string_append(launch, videosink_options);', '''                g_string_append(launch, videosink_options);
                if (!video_sync && !strcmp(videosink, "d3d11videosink"))
                    g_string_append(launch, " processing-deadline=0");''')
    text = replace_once(text,
                        '            g_object_set(renderer_type[i]->appsrc, "caps", caps, "stream-type", 0, "is-live", TRUE, "format", GST_FORMAT_TIME, NULL);',
                        '''            g_object_set(renderer_type[i]->appsrc, "caps", caps, "stream-type", 0, "is-live", TRUE, "format", GST_FORMAT_TIME, NULL);
            if (!jpeg_pipeline) {
                /* Encoded H.264 frames retain their dependencies. Drop only decoded frames. */
                g_object_set(renderer_type[i]->appsrc, "block", TRUE, "max-buffers", (guint64) 6,
                    "max-bytes", (guint64) (2 * 1024 * 1024), "emit-signals", FALSE, NULL);
                g_signal_connect(renderer_type[i]->pipeline, "deep-element-added", G_CALLBACK(jj_decoder_added), NULL);
                gchar *sink_name = g_strdup_printf("%s_%s", videosink, renderer_type[i]->codec);
                GstElement *sink = gst_bin_get_by_name(GST_BIN(renderer_type[i]->pipeline), sink_name);
                g_free(sink_name);
                if (sink) {
                    GstPad *pad = gst_element_get_static_pad(sink, "sink");
                    if (pad) {
                        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM | GST_PAD_PROBE_TYPE_BUFFER, jj_video_caps, NULL, NULL);
                        gst_object_unref(pad);
                    }
                    gst_object_unref(sink);
                }
            }''')
    text = replace_once(text,
                        '        gst_element_get_state(renderer_type[i]->pipeline, &state, NULL, 1000 * GST_MSECOND);',
                        '''        /* Live appsrc cannot preroll until the phone supplies its first buffer. */
        gst_element_get_state(renderer_type[i]->pipeline, &state, NULL, sync ? 1000 * GST_MSECOND : 0);''')
    text = replace_once(text, '    renderer = renderer_used;\n    gst_element_set_state',
                        '''    renderer = renderer_used;
    g_atomic_int_set(&jj_first_frame_seen, 0);
    jj_codec_start_usec = g_get_monotonic_time();
    gst_element_set_state''')
    text = replace_once(text,
                        'gst_element_get_state(renderer->pipeline, &old_state, &new_state, 100 * GST_MSECOND)',
                        'gst_element_get_state(renderer->pipeline, &old_state, &new_state, sync ? 100 * GST_MSECOND : 0)')
    return text
