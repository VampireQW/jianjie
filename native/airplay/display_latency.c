/* Limit only the receiver's D3D11 device, never system/driver settings. */
#include <d3d11.h>
#include <dxgi.h>

static HRESULT jj_limit_display_queue(ID3D11Device *device, UINT *previous, UINT *actual) {
    IDXGIDevice1 *dxgi = NULL;
    if (!device || !previous || !actual) return E_POINTER;
    *previous = *actual = 0;
    HRESULT result = device->lpVtbl->QueryInterface(device, &IID_IDXGIDevice1, (void **) &dxgi);
    if (FAILED(result)) return result;
    dxgi->lpVtbl->GetMaximumFrameLatency(dxgi, previous);
    result = dxgi->lpVtbl->SetMaximumFrameLatency(dxgi, 1);
    if (SUCCEEDED(result)) result = dxgi->lpVtbl->GetMaximumFrameLatency(dxgi, actual);
    dxgi->lpVtbl->Release(dxgi);
    return SUCCEEDED(result) && *actual != 1 ? E_FAIL : result;
}

#ifndef JJ_DISPLAY_LATENCY_NATIVE_PROBE
typedef struct { ID3D11Device *device; } JJDisplayLatency;

static void jj_display_latency_free(gpointer data, GClosure *closure) {
    JJDisplayLatency *state = data;
    if (state->device) state->device->lpVtbl->Release(state->device);
    g_free(state);
}

static void jj_display_present(GstElement *sink, GstObject *gst_device,
                               gpointer render_target, gpointer data) {
    JJDisplayLatency *state = data;
    ID3D11RenderTargetView *view = render_target;
    ID3D11Device *device = NULL;
    if (!view) return;
    /* GetDevice returns a reference. Retain it until replacement/closure teardown. */
    view->lpVtbl->GetDevice(view, &device);
    if (!device) return;
    if (state->device == device) {
        device->lpVtbl->Release(device);
        return;
    }
    if (state->device) state->device->lpVtbl->Release(state->device);
    state->device = device;
    UINT previous = 0, actual = 0;
    HRESULT result = jj_limit_display_queue(device, &previous, &actual);
    printf("JJ_AIRPLAY_DISPLAY_QUEUE %u %u %u\n", SUCCEEDED(result) ? 1u : 0u, previous, actual);
    /* A driver that rejects the limit keeps normal video rendering. */
}

static void jj_display_latency_install(GstElement *sink) {
    if (!g_object_class_find_property(G_OBJECT_GET_CLASS(sink), "emit-present") ||
        !g_signal_lookup("present", G_OBJECT_TYPE(sink))) return;
    JJDisplayLatency *state = g_new0(JJDisplayLatency, 1);
    g_signal_connect_data(sink, "present", G_CALLBACK(jj_display_present), state,
                          jj_display_latency_free, 0);
    g_object_set(sink, "emit-present", TRUE, NULL);
}
#endif
