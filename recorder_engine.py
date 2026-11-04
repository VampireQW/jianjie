"""A bounded, single-worker recording loop. COM/GPU objects never cross threads."""
import math
import threading
import time
import numpy as np

from recorder_mf import MfH264Writer
from recorder_native import GpuCapture, GpuWriter, GpuRecorder

def frame_timestamp(elapsed,fps,previous):
    """Quantize timer jitter to the requested cadence; skipped slots retain real time."""
    if previous<0:return 0
    slot=max(round(elapsed*fps),round(previous*fps/10000000)+1)
    return slot*10000000//fps

def capture_region(monitor,region):
    """Region is a physical offset/size snapshot, not a Qt object."""
    if region is None:return dict(monitor)
    x,y,w,h=region
    left=max(0,min(int(x),monitor['width']-1))
    top=max(0,min(int(y),monitor['height']-1))
    right=max(left+1,min(int(x+w),monitor['width']))
    bottom=max(top+1,min(int(y+h),monitor['height']))
    return dict(left=monitor['left']+left,top=monitor['top']+top,width=right-left,height=bottom-top)

class RecordingClock:
    def __init__(self):
        self.lock=threading.Lock();self.origin=time.perf_counter();self.paused_at=None;self.paused_total=0.;self.ended_at=None

    def pause(self,value):
        with self.lock:
            now=time.perf_counter()
            if value and self.paused_at is None:self.paused_at=now
            elif not value and self.paused_at is not None:
                self.paused_total+=now-self.paused_at;self.paused_at=None

    def elapsed(self):
        with self.lock:return max(0.,(self.ended_at or self.paused_at or time.perf_counter())-self.origin-self.paused_total)

    def finish(self):
        with self.lock:self.ended_at=self.paused_at or time.perf_counter()

class RecordingWorker:
    def __init__(self,path,monitor,region,w,h,fps,process_frame=None,overlays=None):
        self.path=path;self.monitor=dict(monitor);self.region=region
        self.w,self.h,self.fps=w,h,int(fps);self.process_frame=process_frame
        self.overlays=overlays
        self.ready=threading.Event();self.stop_event=threading.Event();self.paused=False
        self.clock=None;self.thread=None;self.error=None;self.frames=0
        self.capture_backend='';self.encoder_backend='';self.actual_fps=0.

    def start(self):
        self.thread=threading.Thread(target=self._run,name='JianJie recording',daemon=True)
        self.thread.start()
        if not self.ready.wait(10):
            self.stop_event.set();raise RuntimeError('录屏准备超时，请重试')
        if self.error:raise RuntimeError(self.error)

    def elapsed(self):return self.clock.elapsed() if self.clock else 0.

    def pause(self,value):
        if self.clock:self.clock.pause(value)
        self.paused=value

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(10)
            if self.thread.is_alive():raise RuntimeError('视频仍在生成，请稍后重试')
        if self.error:raise RuntimeError(self.error)

    def _run(self):
        capture=writer=fallback=direct=None
        try:
            try:
                direct=GpuRecorder(self.path,self.monitor,self.w,self.h,self.fps)
                self.capture_backend=self.encoder_backend='GPU'
            except Exception:
                pass
            if not direct:
                try:
                    capture=GpuCapture(self.monitor);self.capture_backend='GPU'
                except Exception:
                    import mss
                    fallback=mss.MSS();self.capture_backend='兼容'
                try:
                    writer=GpuWriter(self.path,self.w,self.h,self.fps);self.encoder_backend='GPU'
                except Exception:
                    writer=MfH264Writer(self.path,self.w,self.h,self.fps);self.encoder_backend='兼容'
            self.clock=RecordingClock();period=1./self.fps;deadline=time.perf_counter()
            stat_start=deadline;stat_frames=0;last_timestamp=-1
            while not self.stop_event.is_set():
                if self.paused:
                    self.stop_event.wait(.02);deadline=time.perf_counter();stat_start=deadline;stat_frames=self.frames
                    continue
                box=capture_region(self.monitor,self.region)
                timestamp=frame_timestamp(self.elapsed(),self.fps,last_timestamp)
                if direct:
                    try:direct.write_frame(box,timestamp,self.overlays() if self.overlays else ())
                    except Exception:
                        if self.frames:raise
                        try:direct.close()
                        except Exception:pass
                        direct=None
                        import mss
                        fallback=mss.MSS();self.capture_backend='兼容'
                        writer=MfH264Writer(self.path,self.w,self.h,self.fps);self.encoder_backend='兼容'
                if not direct:
                    writer=self._write_compatible(capture,writer,fallback,box,timestamp)
                if not self.frames:
                    # Preparation includes driver startup and the first shader/encoder
                    # submission. The toolbar becomes active only after this finishes.
                    self.clock=RecordingClock()
                    deadline=time.perf_counter();stat_start=deadline;stat_frames=0
                self.frames+=1;last_timestamp=timestamp;self.ready.set()
                now=time.perf_counter()
                if now-stat_start>=1:
                    self.actual_fps=(self.frames-stat_frames)/(now-stat_start);stat_start=now;stat_frames=self.frames
                # Keep phase without catch-up writes. A slow frame never triggers duplicate encoding.
                deadline+=period
                if deadline<now:deadline+=math.ceil((now-deadline)/period)*period
                # Python's high-resolution waitable timer avoids the coarse Event.wait
                # timeout rounding on Windows. Stop latency is at most one frame period.
                delay=deadline-time.perf_counter()
                if delay>0:time.sleep(delay)
        except Exception as exc:
            self.error='录屏未完成，请重试' if self.frames else '无法准备录屏，请检查显卡驱动后重试'
            self.diagnostic_error=repr(exc)
        finally:
            if self.clock:self.clock.finish()
            if direct:
                try:direct.close()
                except Exception as exc:self.error='视频生成失败，请重试';self.diagnostic_error=repr(exc)
            if writer:
                try:writer.close()
                except Exception as exc:self.error='视频生成失败，请重试';self.diagnostic_error=repr(exc)
            if capture:capture.close()
            if fallback:fallback.close()
            self.ready.set()

    def _write_compatible(self,capture,writer,fallback,box,timestamp):
        # Fallback is also worker-owned and only grabs the selected physical region.
        if capture:
            frame=capture.grab(box)
        else:
            shot=fallback.grab(box)
            frame=np.asarray(shot).copy()
        if self.process_frame:self.process_frame(frame,box['left']-self.monitor['left'],box['top']-self.monitor['top'])
        if (frame.shape[1],frame.shape[0])!=(self.w,self.h):
            from PIL import Image
            frame=np.asarray(Image.fromarray(frame,'RGBA').resize((self.w,self.h),Image.Resampling.BILINEAR))
        try:writer.write_bgra(frame,timestamp=timestamp)
        except Exception:
            if self.frames or self.encoder_backend!='GPU':raise
            try:writer.close()
            except Exception:pass
            writer=MfH264Writer(self.path,self.w,self.h,self.fps);self.encoder_backend='兼容'
            writer.write_bgra(frame,timestamp=timestamp)
        return writer
