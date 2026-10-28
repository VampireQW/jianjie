"""Actual decodebin selection, latency, and pixel ordering using synthetic H.264."""
import json
import os
from pathlib import Path
import re
import runpy
import struct
import subprocess
import sys
import tempfile
import time
import unittest

import psutil

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build/iphone-video-r287-research'
FFMPEG = Path(os.environ.get('JJ_TEST_FFMPEG', str(WORK / 'ffmpeg-test.exe')))
sys.path.insert(0, str(ROOT))
from iphone_mirror import decoder_preferences


def access_units(data, path):
    markers = list(re.finditer(b'\x00\x00(?:\x00)?\x01', data))
    starts = [m.start() for m in markers if data[m.end()] & 31 == 9]
    with path.open('wb') as target:
        for a, b in zip(starts, starts[1:] + [len(data)]):
            target.write(struct.pack('<I', b - a) + data[a:b])


@unittest.skipUnless(FFMPEG.is_file() and (ROOT / 'build/airplay-research/runtime').is_dir(),
                     'requires the optional Windows media test tools; never shipped in the application')
class DecoderLatencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        WORK.mkdir(exist_ok=True)
        build = runpy.run_path(str(ROOT / 'native/airplay/build.py'))
        sdk = build['SDK']
        cls.env = os.environ.copy()
        cls.env['ZIG_GLOBAL_CACHE_DIR'] = str(ROOT / 'build/native-toolchain/cache')
        cls.env['ZIG_LOCAL_CACHE_DIR'] = str(WORK / 'zig-cache')
        command = [str(build['ZIG']), 'cc', '-target', 'x86_64-windows-gnu', '-O2', '-g0']
        for directory in (sdk / 'include', sdk / 'include/glib-2.0', sdk / 'lib/glib-2.0/include',
                          sdk / 'include/gstreamer-1.0', ROOT / 'native/airplay'):
            command += ['-I', str(directory)]
        command += [str(ROOT / 'tests/native/iphone_decoder_probe.c'),
                    *[str(sdk / 'lib' / ('lib' + name + '.dll.a')) for name in
                      ('gstreamer-1.0', 'gstapp-1.0', 'gstvideo-1.0', 'gobject-2.0', 'glib-2.0')],
                    '-o', str(WORK / 'decode_probe.exe')]
        subprocess.run(command, env=cls.env, check=True, capture_output=True, timeout=180)
        runtime = ROOT / 'build/airplay-research/runtime'
        cls.env['PATH'] = str(runtime) + os.pathsep + cls.env['PATH']
        cls.env['GST_PLUGIN_PATH_1_0'] = cls.env['GST_PLUGIN_SYSTEM_PATH_1_0'] = str(runtime / 'lib/gstreamer-1.0')
        cls.env['GST_REGISTRY_1_0'] = str(WORK / 'registry.bin')
        cls.env['GST_REGISTRY_FORK'] = 'no'
        cls.make_fixture('ip', '1', 'aud=1:keyint=120:b-bias=-100')
        cls.make_fixture('bframes', '3', 'aud=1:keyint=120:bframes=3:b-adapt=0')
        original = (WORK / 'ip.h264').read_bytes()
        trace = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'info', '-i',
                                str(WORK / 'ip.h264'), '-c:v', 'copy', '-bsf:v', 'trace_headers',
                                '-f', 'null', '-'], capture_output=True, check=True).stderr.decode()
        flag = int(re.search(r'\]\s+(\d+)\s+bitstream_restriction_flag\s+1\s+=\s+1', trace)[1])
        # Keep every slice unchanged; remove only the optional SPS reorder restriction.
        parts = re.split(b'(\x00\x00(?:\x00)?\x01)', original)
        for index in range(2, len(parts), 2):
            nal = parts[index]
            if nal[0] & 31 != 7:
                continue
            raw = nal.replace(b'\x00\x00\x03', b'\x00\x00')
            bits = ''.join(f'{byte:08b}' for byte in raw)
            assert raw[1] == 100 and bits[flag] == '1'
            bits = bits[:flag] + '01'
            bits += '0' * (-len(bits) % 8)
            raw = int(bits, 2).to_bytes(len(bits) // 8, 'big')
            escaped, zeros = bytearray(), 0
            for byte in raw:
                if zeros == 2 and byte <= 3:
                    escaped.append(3)
                    zeros = 0
                escaped.append(byte)
                zeros = zeros + 1 if byte == 0 else 0
            parts[index] = bytes(escaped)
        modified = b''.join(parts)
        (WORK / 'unrestricted.h264').write_bytes(modified)
        access_units(modified, WORK / 'unrestricted.au')
        assert cls.reference_hashes('ip') == cls.reference_hashes('unrestricted')
        cls.results = {}

    @classmethod
    def make_fixture(cls, name, bframes, parameters):
        result = subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'info', '-f', 'lavfi',
                                 '-i', 'testsrc2=size=498x1080:rate=60', '-t', '4', '-an', '-c:v',
                                 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-profile:v',
                                 'high', '-level:v', '5.1', '-bf', bframes, '-refs', '1', '-x264-params',
                                 parameters, '-y', str(WORK / (name + '.h264'))], capture_output=True, check=True)
        (WORK / (name + '-encode.log')).write_bytes(result.stderr)
        if name == 'bframes':
            assert re.search(rb'frame B:[1-9]', result.stderr), 'fixture must actually contain B frames'
        else:
            assert not re.search(rb'frame B:[1-9]', result.stderr), 'fixture must contain only I/P frames'
        access_units((WORK / (name + '.h264')).read_bytes(), WORK / (name + '.au'))

    @classmethod
    def reference_hashes(cls, name):
        result = subprocess.run([str(FFMPEG), '-loglevel', 'error', '-i', str(WORK / (name + '.h264')),
                                 '-f', 'framemd5', '-'], capture_output=True, check=True)
        return [line.rsplit(',', 1)[1].strip() for line in result.stdout.decode().splitlines()
                if line and not line.startswith('#')]

    def run_decoder(self, name, low_latency, check=False, fallback=False):
        env = dict(self.env, GST_PLUGIN_FEATURE_RANK=decoder_preferences(low_latency))
        if fallback:
            env['GST_PLUGIN_FEATURE_RANK'] = 'avdec_h264:0,' + decoder_preferences(False)
        args = [str(WORK / 'decode_probe.exe'), str(WORK / (name + '.au')),
                'decodebin', str(int(low_latency))] + (['check'] if check else [])
        started = time.perf_counter()
        stdout = tempfile.TemporaryFile(dir=WORK)
        stderr = tempfile.TemporaryFile(dir=WORK)
        process = subprocess.Popen(args, env=env, stdout=stdout, stderr=stderr)
        monitor = psutil.Process(process.pid)
        cpu, peak_private = 0.0, 0
        while process.poll() is None:
            if time.perf_counter() - started > 22:
                process.terminate()
                process.wait(timeout=3)
                self.fail('owned synthetic decoder exceeded its timeout')
            try:
                times = monitor.cpu_times()
                cpu = times.user + times.system
                memory = monitor.memory_info()
                peak_private = max(peak_private, getattr(memory, 'private', memory.rss))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            time.sleep(.025)
        process.wait(timeout=2)
        stdout.seek(0)
        stderr.seek(0)
        output, errors = stdout.read(), stderr.read()
        stdout.close()
        stderr.close()
        output = output.decode()
        key = f'{name}-low{int(low_latency)}-check{int(check)}-fallback{int(fallback)}'
        (WORK / (key + '.log')).write_text(output + errors.decode(errors='replace'), encoding='utf-8')
        self.assertEqual(process.returncode, 0, output + errors.decode(errors='replace'))
        row = dict(meanAgeMs=float(re.search(r'RESULT \d+ \d+ ([\d.]+)', output)[1]),
                   decoder=re.search(r'JJ_AIRPLAY_DECODER (\w+)', output)[1],
                   cpuSecondsSampled=cpu, wallSeconds=time.perf_counter() - started,
                   peakPrivateMiB=peak_private / 1024**2)
        self.results[key] = row
        return row, [line.split()[1] for line in output.splitlines() if line.startswith('HASH ')]

    def test_actual_decodebin_switch_removes_implicit_dpb_delay(self):
        before, _ = self.run_decoder('unrestricted', False)
        after, _ = self.run_decoder('unrestricted', True)
        self.assertEqual(before['decoder'], 'd3d11h264dec')
        self.assertEqual(after['decoder'], 'avdec_h264')
        self.assertLess(after['meanAgeMs'], before['meanAgeMs'] / 2)

    def test_low_latency_preserves_all_pixels_and_b_frame_order(self):
        for name in ('unrestricted', 'bframes'):
            row, hashes = self.run_decoder(name, True, check=True)
            self.assertEqual(row['decoder'], 'avdec_h264')
            self.assertEqual(hashes, self.reference_hashes(name))
            self.assertEqual(len(hashes), 240)

    def test_hardware_fallback_remains_usable(self):
        row, _ = self.run_decoder('ip', True, fallback=True)
        self.assertEqual(row['decoder'], 'd3d11h264dec')

    @classmethod
    def tearDownClass(cls):
        (WORK / 'production-decoder-results.json').write_text(json.dumps(cls.results, indent=2), encoding='utf-8')
        print(json.dumps(cls.results, indent=2))


if __name__ == '__main__':
    unittest.main(verbosity=2)
