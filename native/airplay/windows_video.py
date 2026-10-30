"""Windows receiver window, explicit foreground input, and bounded video queues."""

CONTROL_INPUT = r'''
#include <imm.h>
static const wchar_t *jj_input_key = L"JianJie.ForegroundInput";
static const UINT_PTR jj_input_timer_id = 0x4a4a3008;
struct JJMouseReport {
    unsigned buttons;
    long long dx, dy, wheel;
};
struct JJInputState {
    HWND window;
    HANDLE ready;
    bool captured, raw_mouse, shortcut_latched;
    unsigned pending_modifiers;
    UINT_PTR timer;
    int cursor_hides;
    unsigned buttons, blocked_buttons;
    bool down[256], blocked_keys[256], swallowed_keys[256];
    unsigned char last_keys[7];
    bool keys_valid;
    HIMC desktop_ime;
    bool ime_detached;
    LONG wheel_remainder;
    JJMouseReport mouse[64];
    unsigned head, count;
    ULONGLONG next_mouse_tick;
    JJInputState(HWND value) : window(value), ready(NULL), captured(false), raw_mouse(false),
        shortcut_latched(false), pending_modifiers(0),
        timer(0), cursor_hides(0), buttons(0), blocked_buttons(0), keys_valid(false),
        desktop_ime(NULL), ime_detached(false),
        wheel_remainder(0), head(0), count(0), next_mouse_tick(0) {
        memset(down, 0, sizeof(down));
        memset(blocked_keys, 0, sizeof(blocked_keys));
        memset(swallowed_keys, 0, sizeof(swallowed_keys));
        memset(last_keys, 0, sizeof(last_keys));
    }
};

static bool jj_input_foreground(HWND window) {
    HWND root = GetAncestor(window, GA_ROOT);
    return root && GetForegroundWindow() == root && IsWindowVisible(window) && !IsIconic(root);
}

static bool jj_input_ready(JJInputState *state) {
    return state->ready && WaitForSingleObject(state->ready, 0) == WAIT_OBJECT_0;
}

static void jj_input_route_keyboard(JJInputState *state, bool phone) {
    /* HOGP transports physical keys, not Windows IME composition results.
       Detach only this receiver window's IME while controlling the phone;
       preserve the exact context and restore it on every exit path. */
    if (phone && !state->ime_detached) {
        state->desktop_ime = ImmAssociateContext(state->window, NULL);
        state->ime_detached = true;
    } else if (!phone && state->ime_detached) {
        HIMC previous = state->desktop_ime;
        state->desktop_ime = NULL;
        state->ime_detached = false;
        ImmAssociateContext(state->window, previous);
    }
}

static void jj_input_release(JJInputState *state) {
    bool was_captured = state->captured;
    state->captured = false; /* ReleaseCapture may synchronously reenter the window proc. */
    if (state->timer) KillTimer(state->window, state->timer);
    state->timer = 0;
    if (state->raw_mouse) {
        RAWINPUTDEVICE device = {0x01, 0x02, RIDEV_REMOVE, NULL};
        RegisterRawInputDevices(&device, 1, sizeof(device));
        state->raw_mouse = false;
    }
    if (was_captured || GetCapture() == state->window) {
        ClipCursor(NULL);
        if (GetCapture() == state->window) ReleaseCapture();
    }
    while (state->cursor_hides > 0) {
        ShowCursor(TRUE);
        --state->cursor_hides;
    }
    if (was_captured) SetCursor(LoadCursorW(NULL, MAKEINTRESOURCEW(32512)));
    if (state->ready) CloseHandle(state->ready);
    state->ready = NULL;
    state->buttons = state->blocked_buttons = state->head = state->count = 0;
    state->wheel_remainder = 0;
    state->next_mouse_tick = 0;
    state->keys_valid = false;
    state->pending_modifiers = 0;
    memset(state->down, 0, sizeof(state->down));
    memset(state->blocked_keys, 0, sizeof(state->blocked_keys));
    jj_input_route_keyboard(state, false);
    if (was_captured) {
        puts("JJ_AIRPLAY_INPUT release");
        puts("JJ_AIRPLAY_CONTROL capture 0");
    }
}

static unsigned jj_input_usage(WPARAM key, LPARAM data) {
    unsigned scan = ((ULONG_PTR) data >> 16) & 0xff;
    bool extended = (((ULONG_PTR) data >> 24) & 1) != 0;
    if (key >= 'A' && key <= 'Z') return 0x04 + (unsigned) key - 'A';
    if (key >= '1' && key <= '9') return 0x1e + (unsigned) key - '1';
    if (key >= VK_F1 && key <= VK_F12) return 0x3a + (unsigned) key - VK_F1;
    if (key >= VK_F13 && key <= VK_F24) return 0x68 + (unsigned) key - VK_F13;
    if (key >= VK_NUMPAD1 && key <= VK_NUMPAD9) return 0x59 + (unsigned) key - VK_NUMPAD1;
    switch (key) {
    case '0': return 0x27;
    case VK_RETURN: return extended ? 0x58 : 0x28;
    case VK_ESCAPE: return 0x29;
    case VK_BACK: return 0x2a;
    case VK_TAB: return 0x2b;
    case VK_SPACE: return 0x2c;
    case VK_OEM_MINUS: return 0x2d;
    case VK_OEM_PLUS: return 0x2e;
    case VK_OEM_4: return 0x2f;
    case VK_OEM_6: return 0x30;
    case VK_OEM_5: return 0x31;
    case VK_OEM_1: return 0x33;
    case VK_OEM_7: return 0x34;
    case VK_OEM_3: return 0x35;
    case VK_OEM_COMMA: return 0x36;
    case VK_OEM_PERIOD: return 0x37;
    case VK_OEM_2: return 0x38;
    case VK_CAPITAL: return 0x39;
    case VK_SNAPSHOT: return 0x46;
    case VK_SCROLL: return 0x47;
    case VK_PAUSE: return 0x48;
    case VK_INSERT: return 0x49;
    case VK_HOME: return 0x4a;
    case VK_PRIOR: return 0x4b;
    case VK_DELETE: return 0x4c;
    case VK_END: return 0x4d;
    case VK_NEXT: return 0x4e;
    case VK_RIGHT: return 0x4f;
    case VK_LEFT: return 0x50;
    case VK_DOWN: return 0x51;
    case VK_UP: return 0x52;
    case VK_NUMLOCK: return 0x53;
    case VK_DIVIDE: return 0x54;
    case VK_MULTIPLY: return 0x55;
    case VK_SUBTRACT: return 0x56;
    case VK_ADD: return 0x57;
    case VK_NUMPAD0: return 0x62;
    case VK_DECIMAL: return 0x63;
    case VK_OEM_102: return 0x64;
    case VK_APPS: return 0x65;
    case VK_CONTROL: return extended ? 0xe4 : 0xe0;
    case VK_SHIFT: return scan == 0x36 ? 0xe5 : 0xe1;
    case VK_MENU: return extended ? 0xe6 : 0xe2;
    case VK_LCONTROL: return 0xe0;
    case VK_LSHIFT: return 0xe1;
    case VK_LMENU: return 0xe2;
    case VK_LWIN: return 0xe3;
    case VK_RCONTROL: return 0xe4;
    case VK_RSHIFT: return 0xe5;
    case VK_RMENU: return 0xe6;
    case VK_RWIN: return 0xe7;
    default: return 0;
    }
}

static void jj_input_key_report(JJInputState *state, unsigned usage, bool down) {
    if (!usage || usage >= 256 || state->down[usage] == down) return;
    state->down[usage] = down;
    unsigned char report[7] = {0};
    for (unsigned i = 0; i < 8; ++i)
        if (state->down[0xe0 + i]) report[0] |= 1u << i;
    unsigned count = 0;
    for (unsigned i = 4; i < 0xe0; ++i) {
        if (!state->down[i]) continue;
        if (count < 6) report[1 + count] = (unsigned char) i;
        ++count;
    }
    if (count > 6) memset(report + 1, 1, 6); /* HID ErrorRollOver, never invent a key. */
    if (state->keys_valid && memcmp(state->last_keys, report, sizeof(report)) == 0) return;
    memcpy(state->last_keys, report, sizeof(report));
    state->keys_valid = true;
    printf("JJ_AIRPLAY_INPUT key %u %u %u %u %u %u %u\n", (unsigned) report[0],
        (unsigned) report[1], (unsigned) report[2], (unsigned) report[3],
        (unsigned) report[4], (unsigned) report[5], (unsigned) report[6]);
}

static bool jj_input_shortcut_modifiers() {
    bool control = ((GetKeyState(VK_CONTROL) | GetKeyState(VK_LCONTROL) |
                     GetKeyState(VK_RCONTROL)) & 0x8000) != 0;
    bool alt = ((GetKeyState(VK_MENU) | GetKeyState(VK_LMENU) |
                 GetKeyState(VK_RMENU)) & 0x8000) != 0;
    return control && alt;
}

static void jj_input_commit_modifiers(JJInputState *state) {
    unsigned pending = state->pending_modifiers;
    state->pending_modifiers = 0;
    for (unsigned i = 0; i < 8; ++i)
        if (pending & (1u << i)) jj_input_key_report(state, 0xe0 + i, true);
}

static bool jj_input_mouse_record(JJInputState *state, unsigned buttons,
                                  long long dx, long long dy, long long wheel) {
    if (state->count) {
        JJMouseReport &tail = state->mouse[(state->head + state->count - 1) % 64];
        if (tail.buttons == buttons) {
            tail.dx += dx;
            tail.dy += dy;
            tail.wheel += wheel;
            return true;
        }
    }
    /* Preserve down/up edges. A pathological flood exits safely instead of losing an up. */
    if (state->count == 64) return false;
    JJMouseReport &report = state->mouse[(state->head + state->count) % 64];
    report.buttons = buttons;
    report.dx = dx;
    report.dy = dy;
    report.wheel = wheel;
    ++state->count;
    return true;
}

static bool jj_input_raw_mouse(JJInputState *state, const RAWMOUSE &mouse) {
    if (!(mouse.usFlags & MOUSE_MOVE_ABSOLUTE) && (mouse.lLastX || mouse.lLastY) &&
        !jj_input_mouse_record(state, state->buttons, mouse.lLastX, mouse.lLastY, 0)) return false;
    const USHORT edges[6] = {RI_MOUSE_LEFT_BUTTON_DOWN, RI_MOUSE_LEFT_BUTTON_UP,
        RI_MOUSE_RIGHT_BUTTON_DOWN, RI_MOUSE_RIGHT_BUTTON_UP,
        RI_MOUSE_MIDDLE_BUTTON_DOWN, RI_MOUSE_MIDDLE_BUTTON_UP};
    for (unsigned i = 0; i < 6; ++i) {
        if (!(mouse.usButtonFlags & edges[i])) continue;
        unsigned bit = 1u << (i / 2);
        unsigned before = state->buttons;
        if (i & 1) {
            state->buttons &= ~bit;
            state->blocked_buttons &= ~bit;
        } else if (!(state->blocked_buttons & bit)) state->buttons |= bit;
        if (before != state->buttons && !jj_input_mouse_record(state, state->buttons, 0, 0, 0))
            return false;
    }
    if (mouse.usButtonFlags & RI_MOUSE_WHEEL) {
        state->wheel_remainder += (SHORT) mouse.usButtonData;
        LONG wheel = state->wheel_remainder / WHEEL_DELTA;
        state->wheel_remainder %= WHEEL_DELTA;
        if (wheel && !jj_input_mouse_record(state, state->buttons, 0, 0, wheel)) return false;
    }
    return true;
}

static void jj_input_mouse_flush(JJInputState *state) {
    ULONGLONG now = GetTickCount64();
    if (!state->count || now < state->next_mouse_tick) return;
    JJMouseReport &report = state->mouse[state->head];
    printf("JJ_AIRPLAY_INPUT mouse %u %lld %lld %lld\n", report.buttons,
        report.dx, report.dy, report.wheel);
    state->head = (state->head + 1) % 64;
    --state->count;
    state->next_mouse_tick = now + 8; /* Even early/synthetic timers cannot exceed 125 lines/s. */
}

static bool jj_input_begin(JJInputState *state) {
    if (!jj_input_foreground(state->window)) return false;
    wchar_t event_name[512] = {0};
    DWORD length = GetEnvironmentVariableW(L"JIANJIE_AIRPLAY_CONTROL_READY_EVENT", event_name, 512);
    if (!length || length >= 512) return false;
    state->ready = OpenEventW(SYNCHRONIZE, FALSE, event_name);
    if (!jj_input_ready(state)) {
        jj_input_release(state);
        return false;
    }
    RECT client = {0};
    if (!GetClientRect(state->window, &client) || client.right <= client.left || client.bottom <= client.top ||
        !ClientToScreen(state->window, (POINT *) &client.left) ||
        !ClientToScreen(state->window, (POINT *) &client.right)) {
        jj_input_release(state);
        return false;
    }
    RAWINPUTDEVICE device = {0x01, 0x02, RIDEV_NOLEGACY, state->window};
    if (!RegisterRawInputDevices(&device, 1, sizeof(device))) {
        jj_input_release(state);
        return false;
    }
    state->raw_mouse = true;
    SetCapture(state->window);
    if (GetCapture() != state->window || !ClipCursor(&client)) {
        jj_input_release(state);
        return false;
    }
    bool cursor_hidden = false;
    for (unsigned i = 0; i < 32; ++i) {
        int visible = ShowCursor(FALSE);
        ++state->cursor_hides;
        if (visible < 0) { cursor_hidden = true; break; }
    }
    if (!cursor_hidden) { jj_input_release(state); return false; }
    state->timer = SetTimer(state->window, jj_input_timer_id, 10, NULL);
    if (!state->timer || !jj_input_ready(state) || !jj_input_foreground(state->window)) {
        jj_input_release(state);
        return false;
    }
    const int mouse_keys[3] = {VK_LBUTTON, VK_RBUTTON, VK_MBUTTON};
    for (unsigned i = 0; i < 3; ++i)
        if (GetAsyncKeyState(mouse_keys[i]) & 0x8000) state->blocked_buttons |= 1u << i;
    for (unsigned key = 8; key < 256; ++key) {
        if (key == VK_SHIFT || key == VK_CONTROL || key == VK_MENU) continue;
        unsigned usage = jj_input_usage(key, 0);
        if (usage && (GetAsyncKeyState(key) & 0x8000)) state->blocked_keys[usage] = true;
    }
    jj_input_route_keyboard(state, true);
    state->captured = true;
    SetCursor(NULL);
    puts("JJ_AIRPLAY_INPUT release");
    puts("JJ_AIRPLAY_CONTROL capture 1");
    return true;
}

static bool jj_input_message(HWND window, UINT message, WPARAM word, LPARAM data, LRESULT *result) {
    JJInputState *state = (JJInputState *) GetPropW(window, jj_input_key);
    bool key_down = message == WM_KEYDOWN || message == WM_SYSKEYDOWN;
    bool key_up = message == WM_KEYUP || message == WM_SYSKEYUP;
    *result = 0;
    if ((key_down || key_up) && word == 'M' && state && state->shortcut_latched) {
        if (key_up) {
            state->shortcut_latched = false;
            state->blocked_keys[0x10] = state->swallowed_keys[0x10] = false;
        }
        return true;
    }
    bool legacy_shortcut = (key_down || key_up) && word == VK_F8;
    if (legacy_shortcut || (key_down && word == 'M' && jj_input_shortcut_modifiers())) {
        if (key_down && !(((ULONG_PTR) data >> 30) & 1)) {
            if (!state) {
                state = new JJInputState(window);
                if (!SetPropW(window, jj_input_key, (HANDLE) state)) { delete state; state = NULL; }
            }
            if (state && !legacy_shortcut) state->shortcut_latched = true;
            if (state && state->captured) jj_input_release(state);
            else if (!state || !jj_input_begin(state)) puts("JJ_AIRPLAY_CONTROL unavailable");
            if (state && !legacy_shortcut)
                state->blocked_keys[0x10] = state->swallowed_keys[0x10] = true;
        }
        return true;
    }
    if (!state) return false;
    if (message == WM_NCDESTROY) {
        jj_input_release(state);
        RemovePropW(window, jj_input_key);
        delete state;
        return false;
    }
    if (!state->captured && state->swallowed_keys[0x29] &&
        (message == WM_CHAR || message == WM_SYSCHAR) && word == VK_ESCAPE) return true;
    if (!state->captured && state->shortcut_latched &&
        (message == WM_CHAR || message == WM_SYSCHAR) && (word == 'M' || word == 'm' || word == 13))
        return true;
    if (!state->captured && (key_down || key_up)) {
        unsigned usage = jj_input_usage(word, data);
        if (usage && state->swallowed_keys[usage]) {
            /* Do not let an Esc auto-repeat close the renderer after capture exits. */
            bool repeat = (((ULONG_PTR) data >> 30) & 1) != 0;
            if (key_up || !repeat) state->swallowed_keys[usage] = false;
            if (key_up || repeat) return true;
        }
    }
    bool inactive = message == WM_KILLFOCUS || message == WM_CANCELMODE || message == WM_CLOSE ||
        message == WM_DESTROY || message == WM_ENTERSIZEMOVE || message == WM_SIZING ||
        message == WM_MOVING || message == WM_SIZE || message == WM_MOVE ||
        message == WM_DPICHANGED || message == WM_DISPLAYCHANGE ||
        (message == WM_ACTIVATE && LOWORD(word) == WA_INACTIVE) ||
        (message == WM_ACTIVATEAPP && !word) || (message == WM_SHOWWINDOW && !word) ||
        (message == WM_CAPTURECHANGED && (HWND) data != window);
    if (message == WM_WINDOWPOSCHANGING && data) {
        WINDOWPOS *position = (WINDOWPOS *) data;
        inactive = !(position->flags & SWP_NOSIZE) || !(position->flags & SWP_NOMOVE);
    }
    if (state->captured && inactive) jj_input_release(state);
    if (!state->captured) return false;
    bool timer = message == WM_TIMER && word == state->timer;
    if (!jj_input_ready(state) || !jj_input_foreground(window) || GetCapture() != window) {
        jj_input_release(state);
        if (message == WM_INPUT) *result = DefWindowProcW(window, message, word, data);
        return key_down || key_up || message == WM_INPUT || timer;
    }
    if (timer) {
        jj_input_mouse_flush(state);
        return true;
    }
    if (message == WM_INPUT) {
        RAWINPUT raw = {0};
        UINT bytes = sizeof(raw);
        if (GetRawInputData((HRAWINPUT) data, RID_INPUT, &raw, &bytes, sizeof(RAWINPUTHEADER)) != (UINT) -1 &&
            raw.header.dwType == RIM_TYPEMOUSE) {
            jj_input_commit_modifiers(state);
            if (!jj_input_raw_mouse(state, raw.data.mouse)) jj_input_release(state);
        }
        /* Foreground WM_INPUT must reach DefWindowProc for Raw Input buffer cleanup. */
        *result = DefWindowProcW(window, message, word, data);
        return true;
    }
    if (key_down || key_up) {
        if (word == VK_ESCAPE) {
            state->swallowed_keys[0x29] = key_down;
            if (key_down) jj_input_release(state);
            return true;
        }
        if (word == VK_LWIN || word == VK_RWIN ||
            (word == VK_TAB && (state->down[0xe2] || state->down[0xe6] ||
                               (state->pending_modifiers & 0x44) ||
                               (((ULONG_PTR) data >> 29) & 1)))) {
            jj_input_release(state);
            return false; /* Keep Windows and Alt+Tab available to the operating system. */
        }
        unsigned usage = jj_input_usage(word, data);
        if (usage) state->swallowed_keys[usage] = key_down;
        if (usage && state->blocked_keys[usage]) {
            if (key_up) {
                state->blocked_keys[usage] = false;
                /* Renderer saw these modifiers before capture; clear its matching key state. */
                if (usage == 0xe0 || usage == 0xe4 || usage == 0xe2 || usage == 0xe6) return false;
            }
        } else if (usage == 0xe0 || usage == 0xe4 || usage == 0xe2 || usage == 0xe6) {
            unsigned bit = 1u << (usage - 0xe0);
            if (key_down) {
                if (!state->down[usage]) state->pending_modifiers |= bit;
            } else {
                if (state->pending_modifiers & bit) jj_input_commit_modifiers(state);
                jj_input_key_report(state, usage, false);
            }
        } else {
            jj_input_commit_modifiers(state);
            jj_input_key_report(state, usage, key_down);
        }
        return true;
    }
    if (message == WM_SETCURSOR) {
        SetCursor(NULL);
        *result = TRUE;
        return true;
    }
    return message == WM_CHAR || message == WM_SYSCHAR || message == WM_DEADCHAR ||
        message == WM_SYSDEADCHAR || (message >= WM_MOUSEFIRST && message <= WM_MOUSELAST);
}
'''

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
''' + CONTROL_INPUT + r'''

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
    LRESULT input_result = 0;
    if (jj_input_message(window, message, word, data, &input_result)) return input_result;
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
        jj_video_frame(GST_PAD_PROBE_INFO_BUFFER(info), GST_ELEMENT(unused));
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
    jj_video_configure_decoder(element);
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
    from pathlib import Path
    stats = Path(__file__).with_name('video_stats.c').read_text(encoding='utf-8')
    display = Path(__file__).with_name('display_latency.c').read_text(encoding='utf-8')
    text = replace_once(text, '#include <gst/gst.h>', '#include <stdio.h>\n#include <gst/gst.h>')
    text = replace_once(text, 'static uintptr_t external_window_handle = 0;',
                        stats + '\n' + display + '\n' + TELEMETRY + '\nstatic uintptr_t external_window_handle = 0;')
    text = replace_once(text, '    logger = render_logger;', '''    logger = render_logger;
    jj_stats_reset(!video_sync);
    g_mutex_lock(&jj_video_size_lock);
    jj_frame_width = jj_frame_height = 0;
    g_mutex_unlock(&jj_video_size_lock);''')
    text = replace_once(text, 'g_string_append(launch, "queue ! ");',
                        'g_string_append(launch, video_sync ? "queue name=jj_encoded_queue max-size-buffers=3 max-size-bytes=0 max-size-time=0 ! " : "queue name=jj_encoded_queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! ");')
    text = replace_once(text, '                    g_string_append(launch, decoder);', '''                    g_string_append(launch, decoder);
                    g_string_append(launch, video_sync ? " ! queue name=jj_decoded_queue max-size-buffers=2 max-size-bytes=0 max-size-time=0" : " ! queue name=jj_decoded_queue max-size-buffers=1 max-size-bytes=0 max-size-time=0");
                    if (!video_sync) g_string_append(launch, " leaky=downstream");''')
    text = replace_once(text, '                g_string_append(launch, "videoscale ! ");', '''                if (strcmp(converter, "d3d11convert") || strcmp(videosink, "d3d11videosink"))
                    g_string_append(launch, "videoscale ! ");''')
    text = replace_once(text, '                g_string_append(launch, converter);', '''                /* CPU-decoded frames need uploading; GPU frames pass through unchanged. */
                if (!strcmp(converter, "d3d11convert"))
                    g_string_append(launch, "d3d11upload ! ");
                g_string_append(launch, converter);''')
    text = replace_once(text, '                g_string_append(launch, videosink_options);', '''                g_string_append(launch, videosink_options);
                if (!video_sync && !strcmp(videosink, "d3d11videosink"))
                    g_string_append(launch, " processing-deadline=0");''')
    text = replace_once(text,
                        '            g_object_set(renderer_type[i]->appsrc, "caps", caps, "stream-type", 0, "is-live", TRUE, "format", GST_FORMAT_TIME, NULL);',
                        '''            g_object_set(renderer_type[i]->appsrc, "caps", caps, "stream-type", 0, "is-live", TRUE, "format", GST_FORMAT_TIME, NULL);
            if (!jpeg_pipeline) {
                /* Encoded H.264 frames retain their dependencies. Drop only decoded frames. */
                g_object_set(renderer_type[i]->appsrc, "block", TRUE, "max-buffers", (guint64) (video_sync ? 6 : 2),
                    "max-bytes", (guint64) (2 * 1024 * 1024), "emit-signals", FALSE, NULL);
                g_signal_connect(renderer_type[i]->pipeline, "deep-element-added", G_CALLBACK(jj_decoder_added), NULL);
                jj_video_scan_decoders(renderer_type[i]->pipeline);
                gchar *sink_name = g_strdup_printf("%s_%s", videosink, renderer_type[i]->codec);
                GstElement *sink = gst_bin_get_by_name(GST_BIN(renderer_type[i]->pipeline), sink_name);
                g_free(sink_name);
                if (sink) {
                    if (!video_sync && !strcmp(videosink, "d3d11videosink"))
                        jj_display_latency_install(sink);
                    GstPad *pad = gst_element_get_static_pad(sink, "sink");
                    if (pad) {
                        gst_pad_add_probe(pad, GST_PAD_PROBE_TYPE_EVENT_DOWNSTREAM | GST_PAD_PROBE_TYPE_BUFFER, jj_video_caps, renderer_type[i]->pipeline, NULL);
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
    jj_video_scan_decoders(renderer->pipeline);
    g_atomic_int_set(&jj_first_frame_seen, 0);
    jj_codec_start_usec = g_get_monotonic_time();
    gst_element_set_state''')
    text = replace_once(text,
                        'gst_element_get_state(renderer->pipeline, &old_state, &new_state, 100 * GST_MSECOND)',
                        'gst_element_get_state(renderer->pipeline, &old_state, &new_state, sync ? 100 * GST_MSECOND : 0)')
    text = replace_once(text, '''        if (sync) {
            GST_BUFFER_PTS(buffer) = pts;
        }
        gst_buffer_fill(buffer, 0, data, *data_len);''', '''        if (sync) {
            GST_BUFFER_PTS(buffer) = pts;
        } else {
            GST_BUFFER_PTS(buffer) = gst_element_get_current_running_time(renderer->pipeline);
        }
        jj_video_ingress(buffer);
        gst_buffer_fill(buffer, 0, data, *data_len);''')
    return text
