"""Exercise DXGI on a private WARP device; no phone input or screen capture."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from iphone_mirror import MirrorSession

ROOT = Path(__file__).resolve().parents[1]
APP = QApplication.instance() or QApplication([])

PROBE = r'''
#include <stdio.h>
#define JJ_DISPLAY_LATENCY_NATIVE_PROBE
/* DISPLAY_LATENCY */
static HRESULT STDMETHODCALLTYPE no_dxgi(ID3D11Device *self, REFIID id, void **out) {
    *out = NULL;
    return E_NOINTERFACE;
}
int main(void) {
    UINT previous = 42, actual = 42;
    if (jj_limit_display_queue(NULL, &previous, &actual) != E_POINTER) return 2;
    ID3D11DeviceVtbl unsupported_vtable = {0};
    unsupported_vtable.QueryInterface = no_dxgi;
    ID3D11Device unsupported = {&unsupported_vtable};
    if (jj_limit_display_queue(&unsupported, &previous, &actual) != E_NOINTERFACE || previous || actual) return 3;
    ID3D11Device *device = NULL;
    HRESULT result = D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_WARP, NULL, 0, NULL, 0,
                                      D3D11_SDK_VERSION, &device, NULL, NULL);
    if (FAILED(result)) { fprintf(stderr, "Private WARP device creation failed: %lx\n", (unsigned long) result); return 4; }
    result = jj_limit_display_queue(device, &previous, &actual);
    device->lpVtbl->Release(device);
    if (FAILED(result) || actual != 1) return 5;
    printf("{\"nullDeviceRejected\":true,\"unsupportedInterfaceRejected\":true,\"previousFrames\":%u,\"actualFrames\":%u}\n", previous, actual);
    return 0;
}
'''


class DisplayLatencyTests(unittest.TestCase):
    def test_queue_telemetry_accepts_driver_success_and_failure(self):
        session = MirrorSession()
        with patch.object(session, '_state'):
            session._output(0, 'JJ_AIRPLAY_DISPLAY_QUEUE 1 3 1')
            self.assertTrue(session.video['displayQueueLimited'])
            self.assertEqual(session.video['displayQueueFrames'], 1)
            session._output(0, 'JJ_AIRPLAY_DISPLAY_QUEUE 0 3 3')
            self.assertFalse(session.video['displayQueueLimited'])
            self.assertEqual(session.video['displayQueueFrames'], 3)

    def test_bad_and_stale_telemetry_cannot_claim_a_queue_limit(self):
        session = MirrorSession()
        with patch.object(session, '_state'):
            for line in ('JJ_AIRPLAY_DISPLAY_QUEUE 1 3 3', 'JJ_AIRPLAY_DISPLAY_QUEUE 2 3 1',
                         'JJ_AIRPLAY_DISPLAY_QUEUE 1 -1 1', 'JJ_AIRPLAY_DISPLAY_QUEUE 1 17 1',
                         'JJ_AIRPLAY_DISPLAY_QUEUE x 3 1', 'JJ_AIRPLAY_DISPLAY_QUEUE 1 3'):
                session._output(0, line)
                self.assertNotIn('displayQueueLimited', session.video)
            session._output(1, 'JJ_AIRPLAY_DISPLAY_QUEUE 1 3 1')
            self.assertNotIn('displayQueueLimited', session.video)

    def test_actual_dxgi_device_uses_one_queued_frame_and_safe_fallback(self):
        config = runpy.run_path(str(ROOT / 'native/airplay/build.py'))
        work = ROOT / 'build/iphone-display-r292-native-test'
        work.mkdir(exist_ok=True)
        source = work / 'probe.c'
        source.write_text(PROBE.replace('/* DISPLAY_LATENCY */', (ROOT / 'native/airplay/display_latency.c').read_text(encoding='utf-8')), encoding='utf-8')
        env = os.environ.copy()
        env['ZIG_GLOBAL_CACHE_DIR'] = str(ROOT / 'build/native-toolchain/cache')
        env['ZIG_LOCAL_CACHE_DIR'] = str(work / 'zig-cache')
        temporary = work / 'tmp'; temporary.mkdir(exist_ok=True)
        env['TEMP'] = env['TMP'] = str(temporary)
        command = [str(config['ZIG']), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-g0',
                   '-D_WIN32_WINNT=0x0a00', str(source), '-ld3d11', '-ldxguid', '-o', str(work / 'probe.exe')]
        built = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        probe = subprocess.run([str(work / 'probe.exe')], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
        report = json.loads(probe.stdout)
        self.assertEqual(report['actualFrames'], 1)
        self.assertTrue(report['nullDeviceRejected'] and report['unsupportedInterfaceRejected'])
        (work / 'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main(verbosity=2)
