"""Compile the actual receiver input bridge, then test it against deterministic Win32 fakes.

No desktop focus/cursor, Bluetooth service, or production receiver is changed by these tests.
"""
from pathlib import Path
import os
import runpy
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
ZIG = ROOT / "build/native-toolchain/zig-x86_64-windows-0.15.2/zig.exe"
WORK = ROOT / "build/iphone-native-input-tests"


HARNESS = r'''
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <string>
#include <vector>
#include <algorithm>
static HWND video = (HWND) 1, other = (HWND) 2;
static HWND foreground = video, capture_window = NULL;
static HANDLE property = NULL;
static bool visible = true, iconic = false, event_exists = true, event_ready = true;
static bool raw_registered = false, clipped = false, timer_active = false;
static int cursor_count = 2, opened = 0, closed = 0, waited = 0, raw_cleanup = 0;
static int failure = 0, held[256] = {0}, assertions = 0;
static ULONGLONG ticks = 0;
static RAWINPUT incoming = {0};
static std::vector<std::string> reports;
static void check(bool value, const char *why) {
    ++assertions;
    if (!value) { fprintf(stderr, "FAIL: %s\n", why); ExitProcess(9); }
}
static int test_puts(const char *line) { reports.push_back(line); return 0; }
static int test_printf(const char *format, ...) {
    char line[512]; va_list args; va_start(args, format);
    int length = vsnprintf(line, sizeof(line), format, args); va_end(args);
    std::string value(line); if (!value.empty() && value.back() == '\n') value.pop_back();
    reports.push_back(value); return length;
}
static DWORD test_env(LPCWSTR name, LPWSTR target, DWORD size) {
    check(wcscmp(name, L"JIANJIE_AIRPLAY_CONTROL_READY_EVENT") == 0, "exact event env key");
    if (failure == 7) return 0;
    if (failure == 8) return size + 1;
    const wchar_t *value = L"Local\\JJInputTest";
    wcscpy(target, value); return (DWORD) wcslen(value);
}
static HANDLE test_open(DWORD access, BOOL inherit, LPCWSTR name) {
    check(access == SYNCHRONIZE && !inherit, "event is synchronize only, not inherited");
    check(wcscmp(name, L"Local\\JJInputTest") == 0, "correct event name");
    if (!event_exists) return NULL;
    ++opened; return (HANDLE) 3;
}
static DWORD test_wait(HANDLE handle, DWORD timeout) {
    check(handle == (HANDLE) 3 && timeout == 0, "nonblocking event observation");
    ++waited; return event_ready ? WAIT_OBJECT_0 : WAIT_TIMEOUT;
}
static BOOL test_close(HANDLE handle) { check(handle == (HANDLE) 3, "owned event closed"); ++closed; return TRUE; }
static HWND test_ancestor(HWND window, UINT kind) { check(kind == GA_ROOT, "foreground uses root"); return window; }
static HWND test_foreground() { return foreground; }
static BOOL test_visible(HWND) { return visible; }
static BOOL test_iconic(HWND) { return iconic; }
static BOOL test_rect(HWND, LPRECT rect) { *rect = RECT{0, 0, 640, 480}; return failure != 1; }
static BOOL test_screen(HWND, LPPOINT point) { point->x += 20; point->y += 30; return TRUE; }
static SHORT test_held(int key) { return held[key] ? (SHORT) 0x8000 : 0; }
static BOOL test_register(PCRAWINPUTDEVICE devices, UINT count, UINT size) {
    check(count == 1 && size == sizeof(RAWINPUTDEVICE), "single mouse raw registration");
    check(devices->usUsagePage == 1 && devices->usUsage == 2, "mouse usage only");
    if (devices->dwFlags == RIDEV_REMOVE) {
        check(devices->hwndTarget == NULL, "remove target is null"); raw_registered = false; return TRUE;
    }
    check(devices->dwFlags == RIDEV_NOLEGACY && devices->hwndTarget == video,
          "foreground raw mouse, no input sink or global hook");
    if (failure == 2) return FALSE;
    raw_registered = true; return TRUE;
}
static HWND test_set_capture(HWND window) {
    HWND previous = capture_window; if (failure != 3) capture_window = window; return previous;
}
static HWND test_capture() { return capture_window; }
static BOOL test_release_capture() { capture_window = NULL; return TRUE; }
static BOOL test_clip(const RECT *rect) {
    if (rect) {
        check(rect->left == 20 && rect->top == 30 && rect->right == 660 && rect->bottom == 510,
              "clip uses client screen coordinates");
        if (failure == 4) return FALSE;
    }
    clipped = rect != NULL; return TRUE;
}
static int test_show(BOOL show) { return cursor_count += show ? 1 : -1; }
static HCURSOR test_cursor(HCURSOR cursor) { return cursor; }
static HCURSOR test_load(HINSTANCE, LPCWSTR) { return (HCURSOR) 4; }
static UINT_PTR test_timer(HWND, UINT_PTR id, UINT interval, TIMERPROC callback) {
    check(interval >= 8 && interval <= 16 && callback == NULL, "bounded window timer interval");
    if (failure == 5) return 0;
    timer_active = true; return id;
}
static BOOL test_kill(HWND, UINT_PTR) { timer_active = false; return TRUE; }
static ULONGLONG test_ticks() { return ticks; }
static HANDLE test_property(HWND, LPCWSTR) { return property; }
static BOOL test_set_property(HWND, LPCWSTR, HANDLE value) {
    if (failure == 6) return FALSE;
    property = value; return TRUE;
}
static HANDLE test_remove_property(HWND, LPCWSTR) { HANDLE value = property; property = NULL; return value; }
static UINT test_raw(HRAWINPUT, UINT command, LPVOID output, PUINT bytes, UINT header) {
    check(command == RID_INPUT && header == sizeof(RAWINPUTHEADER) && *bytes >= sizeof(incoming),
          "raw mouse structure size");
    if (failure == 9) return (UINT) -1;
    memcpy(output, &incoming, sizeof(incoming)); return sizeof(incoming);
}
static LRESULT test_default(HWND, UINT message, WPARAM, LPARAM) {
    if (message == WM_INPUT) ++raw_cleanup; return 0;
}
#define puts test_puts
#define printf test_printf
#define GetEnvironmentVariableW test_env
#define OpenEventW test_open
#define WaitForSingleObject test_wait
#define CloseHandle test_close
#define GetAncestor test_ancestor
#define GetForegroundWindow test_foreground
#define IsWindowVisible test_visible
#define IsIconic test_iconic
#define GetClientRect test_rect
#define ClientToScreen test_screen
#define GetAsyncKeyState test_held
#define GetKeyState test_held
#define RegisterRawInputDevices test_register
#define SetCapture test_set_capture
#define GetCapture test_capture
#define ReleaseCapture test_release_capture
#define ClipCursor test_clip
#define ShowCursor test_show
#define SetCursor test_cursor
#define LoadCursorW test_load
#define SetTimer test_timer
#define KillTimer test_kill
#define GetTickCount64 test_ticks
#define GetPropW test_property
#define SetPropW test_set_property
#define RemovePropW test_remove_property
#define GetRawInputData test_raw
#define DefWindowProcW test_default
/* INPUT_BRIDGE */
static bool message(UINT kind, WPARAM word = 0, LPARAM data = 0) {
    LRESULT result = 0; return jj_input_message(video, kind, word, data, &result);
}
static JJInputState *state() { return (JJInputState *) property; }
static bool has(const char *line) { return std::find(reports.begin(), reports.end(), line) != reports.end(); }
static size_t lines(const char *prefix) {
    size_t count = 0; for (auto &line : reports) if (line.find(prefix) == 0) ++count; return count;
}
static void idle() {
    check(!raw_registered && !clipped && !capture_window && !timer_active && cursor_count == 2,
          "capture resources fully released and cursor counter balanced");
    check(opened == closed, "no leaked event handle");
    if (state()) {
        check(!state()->captured && !state()->ready && state()->count == 0 && state()->buttons == 0 &&
              state()->pending_modifiers == 0,
              "inactive state contains no pressed mouse or pending motion");
        for (bool key : state()->down) check(!key, "all HID keys released");
    }
}
static void reset() {
    if (property) message(WM_NCDESTROY);
    cursor_count = 2; idle();
    failure = 0; foreground = video; visible = event_exists = event_ready = true; iconic = false;
    memset(held, 0, sizeof(held)); reports.clear(); ticks = 0; incoming = RAWINPUT{};
}
static bool toggle() {
    held[VK_LCONTROL] = held[VK_LMENU] = 1;
    bool handled = message(WM_KEYDOWN, 'M');
    message(WM_KEYUP, 'M');
    held[VK_LCONTROL] = held[VK_LMENU] = 0;
    message(WM_KEYUP, VK_LCONTROL); message(WM_SYSKEYUP, VK_LMENU);
    return handled;
}
static void begin() {
    check(toggle(), "Ctrl+Alt+M handled");
    check(state() && state()->captured && raw_registered && clipped && timer_active,
          "ready foreground Ctrl+Alt+M acquires all resources");
    check(cursor_count < 0 && has("JJ_AIRPLAY_CONTROL capture 1"), "capture announced and cursor hidden");
}
static void raw(USHORT flags = 0, LONG x = 0, LONG y = 0, SHORT wheel = 0, USHORT kind = 0) {
    incoming = RAWINPUT{}; incoming.header.dwType = RIM_TYPEMOUSE;
    incoming.data.mouse.usFlags = kind; incoming.data.mouse.usButtonFlags = flags;
    incoming.data.mouse.lLastX = x; incoming.data.mouse.lLastY = y;
    incoming.data.mouse.usButtonData = (USHORT) wheel;
    check(message(WM_INPUT), "raw input consumed");
}
static void timer() { check(message(WM_TIMER, jj_input_timer_id), "capture timer handled"); }
int main() {
    const int controls[] = {VK_LCONTROL, VK_RCONTROL};
    const int alts[] = {VK_LMENU, VK_RMENU};
    for (int control : controls) for (int alt : alts) {
        reset(); held[control] = held[alt] = 1;
        message(WM_KEYDOWN, control); message(WM_SYSKEYDOWN, alt);
        check(message(WM_KEYDOWN, 'M') && state()->captured, "all left/right Ctrl+Alt+M combinations enter");
        message(WM_KEYUP, 'M'); held[control] = held[alt] = 0;
        check(!message(WM_KEYUP, control) && !message(WM_SYSKEYUP, alt),
              "entry modifier ups also clear original renderer state");
        check(lines("JJ_AIRPLAY_INPUT key ") == 0, "entering shortcut modifiers never reach phone");
        reports.clear(); held[control] = held[alt] = 1;
        message(WM_KEYDOWN, control); message(WM_SYSKEYDOWN, alt);
        check(lines("JJ_AIRPLAY_INPUT key ") == 0, "Ctrl/Alt deferred while shortcut undecided");
        message(WM_KEYDOWN, 'M'); check(message(WM_SYSCHAR, 13), "shortcut translated character consumed");
        message(WM_KEYUP, 'M'); held[control] = held[alt] = 0;
        message(WM_KEYUP, control); message(WM_SYSKEYUP, alt);
        check(!state()->captured && state()->pending_modifiers == 0 && lines("JJ_AIRPLAY_INPUT key ") == 0,
              "exit chord discards pending modifiers and M without phone key events"); idle();
    }
    for (int modifiers = 0; modifiers < 3; ++modifiers) {
        reset(); held[VK_LCONTROL] = modifiers == 1; held[VK_LMENU] = modifiers == 2;
        check(!message(WM_KEYDOWN, 'M') && !property && reports.empty(), "M/Ctrl+M/Alt+M cannot enter capture");
    }
    reset(); begin(); reports.clear(); message(WM_KEYDOWN, 'M'); message(WM_KEYUP, 'M');
    check(has("JJ_AIRPLAY_INPUT key 0 16 0 0 0 0 0"), "plain M remains a normal captured key");
    reports.clear(); held[VK_LCONTROL] = 1; message(WM_KEYDOWN, VK_LCONTROL);
    message(WM_KEYDOWN, 'M'); message(WM_KEYUP, 'M'); held[VK_LCONTROL] = 0; message(WM_KEYUP, VK_LCONTROL);
    check(state()->captured && has("JJ_AIRPLAY_INPUT key 1 16 0 0 0 0 0") && state()->pending_modifiers == 0,
          "Ctrl+M remains a phone key and releases its modifier");
    reports.clear(); held[VK_LMENU] = 1; message(WM_SYSKEYDOWN, VK_LMENU);
    message(WM_SYSKEYDOWN, 'M'); message(WM_SYSKEYUP, 'M'); held[VK_LMENU] = 0; message(WM_SYSKEYUP, VK_LMENU);
    check(state()->captured && has("JJ_AIRPLAY_INPUT key 4 16 0 0 0 0 0") && state()->pending_modifiers == 0,
          "Alt+M remains a phone key and releases its modifier");
    reset(); begin(); reports.clear(); message(WM_KEYDOWN, VK_LCONTROL);
    check(reports.empty(), "standalone Ctrl down remains undecided"); message(WM_KEYUP, VK_LCONTROL);
    check(reports.size() == 2 && has("JJ_AIRPLAY_INPUT key 1 0 0 0 0 0 0") &&
          reports.back() == "JJ_AIRPLAY_INPUT key 0 0 0 0 0 0 0", "standalone modifier tap is preserved and released");
    reports.clear(); message(WM_SYSKEYDOWN, VK_LMENU); raw(0, 2, 3);
    check(has("JJ_AIRPLAY_INPUT key 4 0 0 0 0 0 0") && state()->pending_modifiers == 0,
          "modified mouse motion commits modifier before mouse report");
    message(WM_SYSKEYUP, VK_LMENU); check(reports.back() == "JJ_AIRPLAY_INPUT key 0 0 0 0 0 0 0", "mouse modifier released");
    reports.clear(); message(WM_KEYDOWN, VK_LCONTROL); message(WM_SYSKEYDOWN, VK_LMENU);
    message(WM_KEYDOWN, VK_ESCAPE);
    check(lines("JJ_AIRPLAY_INPUT key ") == 0, "Esc discards pending shortcut modifiers"); idle();
    reset(); message(WM_KEYDOWN, VK_F8); check(state()->captured, "F8 compatibility still enters");
    message(WM_KEYUP, VK_F8); message(WM_KEYDOWN, VK_F8); idle();
    reset(); event_exists = false; toggle();
    check(has("JJ_AIRPLAY_CONTROL unavailable"), "missing event cannot capture"); idle();
    int wait_before = waited; check(!message(WM_TIMER, jj_input_timer_id), "no idle timer polling");
    check(waited == wait_before && !message(WM_KEYDOWN, 'Q'), "idle renderer hotkeys untouched");
    reset(); event_ready = false; toggle();
    check(has("JJ_AIRPLAY_CONTROL unavailable"), "reset event cannot capture"); idle();
    reset(); foreground = other; toggle(); idle();
    reset(); iconic = true; toggle(); idle();
    reset(); visible = false; toggle(); idle();
    for (int fail = 1; fail <= 8; ++fail) {
        reset(); failure = fail; toggle();
        check(has("JJ_AIRPLAY_CONTROL unavailable"), "failed acquire reports unavailable"); idle();
    }
    reset(); cursor_count = 100; toggle();
    check(!state()->captured && cursor_count == 100, "cursor hide limit rolls back exactly");
    cursor_count = 2; idle();
    reset(); begin(); held[VK_LCONTROL] = held[VK_LMENU] = 1;
    message(WM_KEYDOWN, 'M', (LPARAM) 1 << 30);
    check(state()->captured && lines("JJ_AIRPLAY_CONTROL capture 1") == 1, "shortcut repeats do not toggle");
    toggle(); check(has("JJ_AIRPLAY_CONTROL capture 0"), "Ctrl+Alt+M toggles out"); idle();
    reset(); begin(); reports.clear();
    for (int i = 0; i < 1000; ++i) raw(0, 2, -3);
    check(state()->count == 1, "1000 mouse packets merge into one bounded record"); timer();
    check(has("JJ_AIRPLAY_INPUT mouse 0 2000 -3000 0"), "relative motion sums exactly");
    reports.clear(); state()->next_mouse_tick = 0;
    for (ticks = 0; ticks < 1000; ++ticks) { raw(0, 1, 0); timer(); }
    check(lines("JJ_AIRPLAY_INPUT mouse ") == 125, "hard 125 lines per second even with early timers");
    reset(); begin(); reports.clear();
    unsigned peak_queue = 0;
    for (ticks = 1; ticks <= 5005; ++ticks) {
        raw(0, 1, -1); if (state()->count > peak_queue) peak_queue = state()->count;
        if (ticks % 10 == 0) timer();
    }
    check(peak_queue == 1 && state()->count == 1, "five seconds of 1000 Hz motion never queues frames");
    ticks = 5010; timer();
    check(state()->count == 0 && lines("JJ_AIRPLAY_INPUT mouse ") == 501,
          "normal 10 ms timer emits about 100 reports/s and drains final motion within 10 ms");
    long long total_x = 0, total_y = 0;
    for (auto &line : reports) {
        unsigned buttons; long long dx, dy, wheel;
        if (sscanf(line.c_str(), "JJ_AIRPLAY_INPUT mouse %u %lld %lld %lld", &buttons, &dx, &dy, &wheel) == 4) {
            total_x += dx; total_y += dy;
        }
    }
    check(total_x == 5005 && total_y == -5005, "coalescing and final drain preserve total relative displacement");
    size_t motion_lines = reports.size(); ticks = 10000; timer();
    check(reports.size() == motion_lines, "stationary mouse produces no repeated reports or trailing frames");
    reset(); begin(); reports.clear(); raw(RI_MOUSE_LEFT_BUTTON_DOWN); raw(RI_MOUSE_LEFT_BUTTON_UP);
    check(state()->count == 2, "quick down/up edges remain separate"); timer(); ticks = 8; timer();
    check(reports.size() == 2 && reports[0] == "JJ_AIRPLAY_INPUT mouse 1 0 0 0" &&
          reports[1] == "JJ_AIRPLAY_INPUT mouse 0 0 0 0", "click edges emitted in order");
    raw(RI_MOUSE_LEFT_BUTTON_DOWN | RI_MOUSE_RIGHT_BUTTON_DOWN | RI_MOUSE_MIDDLE_BUTTON_DOWN);
    check(state()->buttons == 7, "all three mouse buttons supported");
    reset(); begin(); reports.clear(); raw(RI_MOUSE_WHEEL, 0, 0, 60); raw(RI_MOUSE_WHEEL, 0, 0, 60); timer();
    check(has("JJ_AIRPLAY_INPUT mouse 0 0 0 1"), "high resolution wheel remainder preserved");
    raw(RI_MOUSE_WHEEL, 0, 0, -120); ticks = 8; timer();
    check(has("JJ_AIRPLAY_INPUT mouse 0 0 0 -1"), "negative wheel sign preserved");
    raw(0, 12000, 12000, 0, MOUSE_MOVE_ABSOLUTE);
    check(state()->count == 0, "absolute mouse position never becomes a relative jump");
    reset(); held[VK_LBUTTON] = held['A'] = 1; begin(); reports.clear();
    raw(RI_MOUSE_LEFT_BUTTON_DOWN); raw(RI_MOUSE_LEFT_BUTTON_UP);
    check(state()->count == 0 && state()->buttons == 0, "mouse held before capture is ignored until up");
    raw(RI_MOUSE_LEFT_BUTTON_DOWN); timer();
    check(has("JJ_AIRPLAY_INPUT mouse 1 0 0 0"), "fresh down works after initial held up");
    message(WM_KEYDOWN, 'A', (LPARAM) 1 << 30); message(WM_KEYUP, 'A');
    check(lines("JJ_AIRPLAY_INPUT key ") == 0, "preexisting held key is suppressed until up");
    message(WM_KEYDOWN, 'A'); message(WM_KEYDOWN, 'A', (LPARAM) 1 << 30); message(WM_KEYUP, 'A');
    check(lines("JJ_AIRPLAY_INPUT key ") == 2 && has("JJ_AIRPLAY_INPUT key 0 4 0 0 0 0 0") &&
          has("JJ_AIRPLAY_INPUT key 0 0 0 0 0 0 0"), "HID key down/up deduplicated");
    size_t before = reports.size(); message(WM_KEYDOWN, 0xff); message(WM_CHAR, 0x4e2d);
    check(reports.size() == before, "unknown keys and text never emitted");
    reset(); begin(); reports.clear();
    message(WM_KEYDOWN, VK_CONTROL); message(WM_KEYDOWN, VK_SHIFT, (LPARAM) 0x36 << 16);
    message(WM_SYSKEYDOWN, VK_MENU, (LPARAM) 1 << 24);
    message(WM_KEYDOWN, 'A'); message(WM_KEYUP, 'A');
    check(has("JJ_AIRPLAY_INPUT key 97 0 0 0 0 0 0"), "left Ctrl/right Shift/right Alt modifier bits");
    check(jj_input_usage(VK_LWIN, 0) == 0xe3 && jj_input_usage(VK_RWIN, 0) == 0xe7 &&
          jj_input_usage(VK_CONTROL, (LPARAM) 1 << 24) == 0xe4 &&
          jj_input_usage(VK_RETURN, (LPARAM) 1 << 24) == 0x58, "right modifiers and keypad Enter usages");
    message(WM_KEYUP, VK_CONTROL); check(has("JJ_AIRPLAY_INPUT key 96 0 0 0 0 0 0"), "modifier up clears correct bit");
    reset(); begin(); reports.clear();
    for (WPARAM key = 'A'; key <= 'G'; ++key) message(WM_KEYDOWN, key);
    check(has("JJ_AIRPLAY_INPUT key 0 1 1 1 1 1 1"), "seven keys uses standard HID ErrorRollOver");
    message(WM_KEYUP, 'G');
    check(reports.back() == "JJ_AIRPLAY_INPUT key 0 4 5 6 7 8 9", "six keys recover after rollover");
    reset(); begin(); reports.clear(); message(WM_KEYDOWN, VK_ESCAPE);
    check(has("JJ_AIRPLAY_INPUT release") && !state()->captured, "Esc releases without closing window");
    check(message(WM_CHAR, VK_ESCAPE), "translated Esc character consumed after exit");
    check(message(WM_KEYDOWN, VK_ESCAPE, (LPARAM) 1 << 30) && message(WM_KEYUP, VK_ESCAPE),
          "Esc repeat/up consumed after exit");
    check(lines("JJ_AIRPLAY_INPUT key ") == 0 && !message(WM_KEYDOWN, 'Q'), "Esc absent from HID; idle hotkeys resume"); idle();
    reset(); begin(); message(WM_SYSKEYDOWN, VK_MENU);
    check(!message(WM_SYSKEYDOWN, VK_TAB, (LPARAM) 1 << 29), "Alt+Tab returned to operating system"); idle();
    reset(); begin(); check(!message(WM_KEYDOWN, VK_LWIN), "Windows key returned to operating system"); idle();
    const UINT release_messages[] = {WM_KILLFOCUS, WM_CANCELMODE, WM_CLOSE, WM_DESTROY,
        WM_ENTERSIZEMOVE, WM_SIZING, WM_MOVING, WM_SIZE, WM_MOVE, WM_DPICHANGED,
        WM_DISPLAYCHANGE, WM_ACTIVATEAPP, WM_ACTIVATE, WM_SHOWWINDOW, WM_CAPTURECHANGED};
    for (UINT kind : release_messages) {
        reset(); begin(); message(WM_KEYDOWN, 'B'); raw(RI_MOUSE_LEFT_BUTTON_DOWN, 10, 20);
        message(kind); check(has("JJ_AIRPLAY_CONTROL capture 0"), "lifecycle event releases capture"); idle();
    }
    reset(); begin(); WINDOWPOS pos = {video, NULL, 0, 0, 640, 480, SWP_NOMOVE | SWP_NOSIZE};
    message(WM_WINDOWPOSCHANGING, 0, (LPARAM) &pos); check(state()->captured, "z-order alone does not exit capture");
    pos.flags = SWP_NOMOVE; message(WM_WINDOWPOSCHANGING, 0, (LPARAM) &pos); idle();
    reset(); begin(); event_ready = false; timer(); idle();
    reset(); begin(); event_ready = false; int cleanup_before = raw_cleanup; raw(0, 2, 3);
    check(raw_cleanup == cleanup_before + 1, "ready loss still cleans foreground raw buffer"); idle();
    reset(); begin(); foreground = other; timer(); idle();
    reset(); begin(); capture_window = other; timer(); capture_window = NULL; idle();
    reset(); begin(); reports.clear(); failure = 9; raw();
    check(reports.empty(), "invalid raw packet produces no input");
    reset(); begin(); message(WM_NCDESTROY); check(!property, "destroy removes per-window state"); idle();
    begin(); message(WM_NCDESTROY); check(!property, "recreated window captures and cleans independently"); idle();
    reset(); begin(); reports.clear();
    for (int i = 0; i < 65; ++i) raw(i & 1 ? RI_MOUSE_LEFT_BUTTON_UP : RI_MOUSE_LEFT_BUTTON_DOWN);
    check(has("JJ_AIRPLAY_INPUT release") && has("JJ_AIRPLAY_CONTROL capture 0"), "bounded queue overflow safely releases all input"); idle();
    reset(); fprintf(stdout, "PASS %d assertions\n", assertions); return 0;
}
'''


WINDOW_HARNESS = r'''
#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdarg.h>
#include <string>
#include <vector>
static bool jj_video_seen = false;
extern "C" void jj_get_video_size(int *width, int *height) { *width = *height = 0; }
static std::vector<std::string> reports;
static int parent_keys = 0, child_keys = 0, assertions = 0;
static void check(bool value, const char *why) {
    ++assertions;
    if (!value) { fprintf(stderr, "FAIL: %s (%lu)\n", why, GetLastError()); ExitProcess(9); }
}
static int test_puts(const char *line) { reports.push_back(line); return 0; }
static int test_printf(const char *format, ...) {
    char text[512]; va_list args; va_start(args, format);
    int count = vsnprintf(text, sizeof(text), format, args); va_end(args);
    reports.push_back(text); return count;
}
#define puts test_puts
#define printf test_printf
/* WINDOW_BRIDGE */
static LRESULT CALLBACK original_proc(HWND window, UINT message, WPARAM word, LPARAM data) {
    if ((message == WM_KEYDOWN || message == WM_SYSKEYDOWN) && word == 'M') {
        if (GetParent(window)) ++child_keys; else ++parent_keys;
    }
    return DefWindowProcW(window, message, word, data);
}
static int unavailable() {
    int count = 0; for (auto &line : reports) if (line == "JJ_AIRPLAY_CONTROL unavailable") ++count; return count;
}
static void send_focus_key(UINT message, WPARAM key, LPARAM data = 0) {
    HWND focus = GetFocus(); check(focus != NULL, "private desktop thread has real HWND focus");
    check(PostMessageW(focus, message, key, data) != FALSE, "PostMessage to actual focus target");
    MSG event;
    while (PeekMessageW(&event, NULL, 0, 0, PM_REMOVE)) DispatchMessageW(&event);
}
static std::wstring desktop_name(HDESK desktop) {
    wchar_t name[256] = {0}; DWORD size = 0;
    check(GetUserObjectInformationW(desktop, UOI_NAME, name, sizeof(name), &size) != FALSE, "desktop name");
    return name;
}
int main() {
    HDESK original = GetThreadDesktop(GetCurrentThreadId());
    HDESK input_before = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    check(input_before != NULL, "input desktop accessible for non-interference check");
    std::wstring input_name = desktop_name(input_before);
    HWND user_foreground = GetForegroundWindow();
    wchar_t name[96]; swprintf(name, 96, L"JJInputMessages-%lu", GetCurrentProcessId());
    HDESK private_desktop = CreateDesktopW(name, NULL, NULL, 0, GENERIC_ALL, NULL);
    check(private_desktop != NULL && SetThreadDesktop(private_desktop), "private desktop created without switching input desktop");
    check(desktop_name(private_desktop) != input_name, "test windows are outside user's input desktop");
    WNDCLASSW cls = {0}; cls.lpfnWndProc = original_proc; cls.hInstance = GetModuleHandleW(NULL);
    cls.lpszClassName = L"JJNativeInputMessageTest";
    check(RegisterClassW(&cls) != 0, "register native test window class");
    HWND parent = CreateWindowExW(0, cls.lpszClassName, L"Private receiver test", WS_OVERLAPPEDWINDOW,
        0, 0, 320, 240, NULL, NULL, cls.hInstance, NULL);
    HWND child = CreateWindowExW(0, cls.lpszClassName, L"Private renderer child", WS_CHILD,
        0, 0, 160, 120, parent, NULL, cls.hInstance, NULL);
    check(parent && child && !IsWindowVisible(parent), "real hidden parent and child HWNDs");
    check(GetAncestor(child, GA_ROOT) == parent, "child root is actual parent HWND");
    jj_prepare_window(parent);
    check((WNDPROC) GetWindowLongPtrW(parent, GWLP_WNDPROC) == jj_video_proc, "actual receiver top-level subclass installed");
    check(!GetPropW(child, jj_original_proc_key), "production does not blindly subclass children");
    BYTE keys[256] = {0};
    keys[VK_CONTROL] = keys[VK_LCONTROL] = keys[VK_MENU] = keys[VK_LMENU] = 0x80;
    check(SetKeyboardState(keys), "only this test thread's keyboard queue state changed");
    SetActiveWindow(parent); SetFocus(parent);
    check(GetFocus() == parent, "real parent focus set on private desktop");
    check(!jj_input_foreground(parent), "hidden window cannot pass foreground capture gate");
    reports.clear(); send_focus_key(WM_SYSKEYDOWN, 'M', (LPARAM) 1 << 29);
    check(unavailable() == 1 && parent_keys == 0, "focused parent dispatch reaches bridge and is safely rejected by gate");
    send_focus_key(WM_SYSKEYUP, 'M');
    SetFocus(child); check(GetFocus() == child, "real child focus on private desktop");
    reports.clear(); send_focus_key(WM_SYSKEYDOWN, 'M', (LPARAM) 1 << 29);
    check(child_keys == 1 && parent_keys == 0 && unavailable() == 0,
          "Windows dispatches a child key to child; parent window proc does not receive it automatically");
    send_focus_key(WM_SYSKEYUP, 'M');
    check(!jj_input_foreground(child), "hidden child also fails actual foreground gate");
    check(!GetPropW(child, jj_input_key), "child message did not create a hidden input session");
    check(!GetCapture(), "real message experiment never captures desktop mouse");
    JJInputState *state = (JJInputState *) GetPropW(parent, jj_input_key);
    check(state && !state->captured && !state->ready && !state->raw_mouse && !state->timer,
          "parent rejection holds no input resource or ready handle");
    memset(keys, 0, sizeof(keys)); SetKeyboardState(keys);
    DestroyWindow(parent); check(!IsWindow(child) && !IsWindow(parent), "private HWND tree destroyed");
    check(SetThreadDesktop(original), "test thread restored to original desktop");
    HDESK input_after = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    check(input_after && desktop_name(input_after) == input_name, "input desktop was never switched");
    check(GetForegroundWindow() == user_foreground, "user foreground unchanged by hidden-window test");
    CloseDesktop(input_before); CloseDesktop(input_after); CloseDesktop(private_desktop);
    fprintf(stdout, "PASS %d real HWND assertions; parentShortcut=1 childShortcut=0; foreground unchanged\n", assertions);
    return 0;
}
'''


class NativeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.name != "nt" or not ZIG.is_file():
            raise unittest.SkipTest("Windows Zig toolchain is required for receiver native tests")
        WORK.mkdir(parents=True, exist_ok=True)
        temporary = WORK / "tmp"
        temporary.mkdir(exist_ok=True)
        cls.env = os.environ.copy()
        cls.env["ZIG_GLOBAL_CACHE_DIR"] = str(ROOT / "build/native-toolchain/cache")
        cls.env["ZIG_LOCAL_CACHE_DIR"] = str(WORK / "zig-cache")
        cls.env["TMP"] = cls.env["TEMP"] = str(temporary)

    def test_foreground_capture_state_machine(self):
        bridge = runpy.run_path(str(ROOT / "native/airplay/windows_video.py"))["CONTROL_INPUT"]
        source = WORK / "state_machine.cpp"
        source.write_text(HARNESS.replace("/* INPUT_BRIDGE */", bridge), encoding="utf-8")
        executable = WORK / "state_machine.exe"
        result = subprocess.run(
            [str(ZIG), "c++", "-target", "x86_64-windows-gnu", "-std=c++11", "-O2", "-g0",
             "-DNDEBUG", "-D_WIN32_WINNT=0x0a00",
             str(source), "-luser32", "-o", str(executable)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(executable)], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"^PASS \d+ assertions\s*$")
        print(result.stdout.strip())

    def test_real_hidden_window_message_routing(self):
        bridge = runpy.run_path(str(ROOT / "native/airplay/windows_video.py"))["BRANDING"]
        source = WORK / "window_messages.cpp"
        source.write_text(WINDOW_HARNESS.replace("/* WINDOW_BRIDGE */", bridge), encoding="utf-8")
        executable = WORK / "window_messages.exe"
        result = subprocess.run(
            [str(ZIG), "c++", "-target", "x86_64-windows-gnu", "-std=c++11", "-O2", "-g0",
             "-DNDEBUG", "-D_WIN32_WINNT=0x0a00", str(source), "-luser32", "-o", str(executable)],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = subprocess.run([str(executable)], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"^PASS \d+ real HWND assertions;.*foreground unchanged\s*$")
        print(result.stdout.strip())

    def test_patched_receiver_translation_unit(self):
        build = runpy.run_path(str(ROOT / "native/airplay/build.py"))
        source = WORK / "uxplay.cpp"
        source.write_text(build["patched_source"](), encoding="utf-8")
        upstream, sdk = build["UPSTREAM"], build["SDK"]
        flags = ["-target", "x86_64-windows-gnu", "-O2", "-g0", "-DNDEBUG", "-DWIN32",
                 "-D_WIN32_WINNT=0x0a00", "-DWINSOCK2", "-DSTANDALONE", "-DNOHOLD",
                 "-DPLIST_210", "-DPLIST_230", "-DOPENSSL_API_COMPAT=0x10101000L"]
        for directory in [upstream, upstream / "renderers", upstream / "lib", upstream / "lib/playfair",
                          upstream / "lib/llhttp", upstream / "lib/mdnsd", sdk / "include",
                          sdk / "include/glib-2.0", sdk / "lib/glib-2.0/include", sdk / "include/gstreamer-1.0"]:
            flags += ["-I", str(directory)]
        result = subprocess.run(
            [str(ZIG), "c++", *flags, "-std=c++11", "-c", str(source), "-o", str(WORK / "uxplay.o")],
            cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
