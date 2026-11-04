"""Recording must preserve time without feedback from repeated-frame encoding."""
import ctypes
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import recorder_mf
import recorder_engine as engine


class RecordingTests(unittest.TestCase):
    def test_subframe_timer_jitter_does_not_create_overlapping_sample_times(self):
        cases=[(0.,-1,0),(.0171,0,166666),(.0329,166666,333333),(.101,333333,1000000),(.102,1000000,1166666)]
        for elapsed,previous,want in cases:
            self.assertEqual(engine.frame_timestamp(elapsed,60,previous),want)

    def test_monotonic_clock_excludes_pause_and_freezes_after_stop(self):
        with patch('recorder_engine.time.perf_counter',return_value=100.) as clock:
            recording=engine.RecordingClock()
            clock.return_value=101.25;recording.pause(True)
            clock.return_value=105.;self.assertEqual(recording.elapsed(),1.25)
            clock.return_value=106.;recording.pause(False)
            clock.return_value=108.;recording.finish()
            clock.return_value=1000.;self.assertEqual(recording.elapsed(),3.25)

    def test_region_uses_native_negative_origin_and_clips_only_local_offsets(self):
        monitor=dict(left=-2560,top=-1440,width=2560,height=1440)
        self.assertEqual(engine.capture_region(monitor,(40,60,1280,720)),dict(left=-2520,top=-1380,width=1280,height=720))
        self.assertEqual(engine.capture_region(monitor,(2500,1400,200,200)),dict(left=-60,top=-40,width=60,height=40))

    def test_slow_encoder_does_not_trigger_duplicate_catchup_writes(self):
        calls=[];owners=[]
        class Device:
            def __init__(self,*args):owners.append(threading.get_ident())
            def write_frame(self,box,timestamp,overlays):
                owners.append(threading.get_ident());calls.append(timestamp);time.sleep(.035)
            def close(self):owners.append(threading.get_ident())
        with patch('recorder_engine.GpuRecorder',Device):
            worker=engine.RecordingWorker('unused',dict(left=0,top=0,width=320,height=240),None,320,240,60)
            worker.start();time.sleep(.25);worker.close()
        self.assertGreaterEqual(len(calls),4)
        self.assertLess(len(calls),10)
        self.assertEqual(calls[:2],[0,166666])  # First submission is preparation, before the clock starts.
        self.assertTrue(all(b-a>=300000 for a,b in zip(calls[1:],calls[2:])))
        self.assertEqual(len(set(owners)),1)
        self.assertNotEqual(owners[0],threading.get_ident())
        self.assertFalse(worker.thread.is_alive())

    def test_first_frame_preparation_is_not_counted_as_recording_stall(self):
        class Device:
            def __init__(self,*args):self.first=True
            def write_frame(self,*args):
                if self.first:time.sleep(.12);self.first=False
            def close(self):pass
        with patch('recorder_engine.GpuRecorder',Device):
            worker=engine.RecordingWorker('unused',dict(left=0,top=0,width=320,height=240),None,320,240,60)
            try:
                worker.start()
                self.assertLess(worker.elapsed(),.05)
            finally:worker.close()

    def test_pause_stops_submissions_and_resume_does_not_catch_up(self):
        calls=[]
        class Device:
            def __init__(self,*args):pass
            def write_frame(self,box,timestamp,overlays):calls.append(timestamp)
            def close(self):pass
        with patch('recorder_engine.GpuRecorder',Device):
            worker=engine.RecordingWorker('unused',dict(left=0,top=0,width=320,height=240),None,320,240,60)
            worker.start();time.sleep(.08);worker.pause(True);time.sleep(.05)
            count=len(calls);elapsed=worker.elapsed();time.sleep(.2)
            self.assertEqual(len(calls),count);self.assertAlmostEqual(worker.elapsed(),elapsed,delta=.001)
            worker.pause(False);time.sleep(.08);worker.close()
        self.assertGreater(len(calls),count)
        self.assertLess(calls[count]-calls[count-1],700000)
        self.assertLess(worker.elapsed(),.3)

    def test_unavailable_acceleration_falls_back_to_region_capture_on_same_worker(self):
        regions=[];submitted=[];owners=[]
        class Capture:
            def __init__(self):owners.append(threading.get_ident())
            def grab(self,region):regions.append(region);return np.zeros((48,64,4),np.uint8)
            def close(self):owners.append(threading.get_ident())
        class Writer:
            def __init__(self,*args):owners.append(threading.get_ident())
            def write_bgra(self,frame,timestamp=None):submitted.append((frame.shape,timestamp))
            def close(self):owners.append(threading.get_ident())
        with patch('recorder_engine.GpuRecorder',side_effect=RuntimeError('unsupported')),patch('recorder_engine.GpuCapture',side_effect=RuntimeError('unsupported')),patch('recorder_engine.GpuWriter',side_effect=RuntimeError('unsupported')),patch('recorder_engine.MfH264Writer',Writer),patch('mss.MSS',Capture):
            worker=engine.RecordingWorker('unused',dict(left=-2560,top=0,width=2560,height=1440),(16,32,64,48),64,48,60)
            worker.start();time.sleep(.08);worker.close()
        self.assertTrue(regions)
        self.assertTrue(all(r==dict(left=-2544,top=32,width=64,height=48) for r in regions))
        self.assertTrue(all(shape==(48,64,4) for shape,ts in submitted))
        self.assertEqual(len(set(owners)),1)
        self.assertNotEqual(owners[0],threading.get_ident())

    def test_delayed_frames_preserve_wall_clock_without_caller_encoding_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            writer=recorder_mf.MfH264Writer(str(Path(tmp)/'time.mp4'),64,48,60)
            try:
                for timestamp in (0,166667,850000,1000000):
                    frame=np.zeros((48,64,4),np.uint8)
                    frame[:,:,2]=timestamp//10000
                    writer.write_bgra(frame,timestamp=timestamp)
            finally:writer.close()
            reader=recorder_mf.MfFrameReader(str(Path(tmp)/'time.mp4'))
            try:times=[t for t,_ in reader.frames()]
            finally:reader.close()
            # MF can resample gaps internally; the caller still submits only 4 frames.
            self.assertEqual(writer.frames,4)
            self.assertEqual(times[0],0)
            self.assertAlmostEqual(times[-1],1000000,delta=2)

    def test_native_gpu_encoder_preserves_orientation_and_60_unique_frames(self):
        from recorder_native import GpuWriter
        with tempfile.TemporaryDirectory() as tmp:
            path=str(Path(tmp)/'gpu.mp4')
            writer=GpuWriter(path,320,240,60)
            try:
                for i in range(60):
                    frame=np.zeros((240,320,4),np.uint8)
                    frame[:120,:,2]=240
                    frame[120:,:,0]=240
                    frame[:,i*4:i*4+4,:3]=255
                    writer.write_bgra(frame,timestamp=i*10000000//60)
            finally:writer.close()
            reader=recorder_mf.MfFrameReader(path)
            try:frames=list(reader.frames())
            finally:reader.close()
            self.assertEqual(len(frames),60)
            for i,(_,frame) in enumerate(frames):
                white=np.flatnonzero(frame[50,:,:3].min(axis=1)>160)
                self.assertTrue(len(white)>0)
                self.assertAlmostEqual(float(np.median(white)),i*4+1.5,delta=2)
            self.assertGreater(frames[0][1][30,100,2],180)
            self.assertGreater(frames[0][1][200,100,0],180)
            self.assertGreater(frames[-1][1][30,237,:3].min(),180)

if __name__=='__main__':unittest.main()
