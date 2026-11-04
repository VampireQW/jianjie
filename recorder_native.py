"""Small Windows GPU bridge; no media runtime or third-party codec is shipped."""
import ctypes as ct
from pathlib import Path
import sys
import threading
import numpy as np

def _library():
    base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))
    api=ct.WinDLL(str(base/'native/jj_recording.dll'))
    ptr=ct.c_void_p
    api.jj_capture_open.argtypes=[ct.c_int]*4+[ct.POINTER(ptr)]
    api.jj_capture_open.restype=ct.c_long
    api.jj_capture_grab.argtypes=[ptr]+[ct.c_int]*4+[ptr,ct.c_int]
    api.jj_capture_grab.restype=ct.c_long
    api.jj_capture_close.argtypes=[ptr]
    api.jj_capture_close.restype=None
    api.jj_encoder_open.argtypes=[ct.c_wchar_p]+[ct.c_int]*4+[ct.POINTER(ptr)]
    api.jj_encoder_open.restype=ct.c_long
    api.jj_encoder_write.argtypes=[ptr,ptr,ct.c_int,ct.c_int64,ct.c_int64]
    api.jj_encoder_write.restype=ct.c_long
    api.jj_encoder_close.argtypes=[ptr]
    api.jj_encoder_close.restype=ct.c_long
    api.jj_recorder_open.argtypes=[ct.c_wchar_p]+[ct.c_int]*8+[ct.POINTER(ptr)]
    api.jj_recorder_open.restype=ct.c_long
    api.jj_recorder_prepare.argtypes=[ptr]+[ct.c_int]*4
    api.jj_recorder_prepare.restype=ct.c_long
    api.jj_recorder_overlay.argtypes=[ptr,ptr]+[ct.c_int]*3+[ct.c_float]*4
    api.jj_recorder_overlay.restype=ct.c_long
    api.jj_recorder_commit.argtypes=[ptr,ct.c_int64,ct.c_int64]
    api.jj_recorder_commit.restype=ct.c_long
    api.jj_recorder_close.argtypes=[ptr]
    api.jj_recorder_close.restype=ct.c_long
    return api

def _check(hr,operation):
    if hr<0:raise RuntimeError(f'{operation} (0x{hr&0xffffffff:08X})')

class GpuCapture:
    """One duplication session, one reusable CPU frame; caller finishes before next grab."""
    def __init__(self,monitor):
        self.api=_library();self.handle=ct.c_void_p();self.frame=None
        self.owner=threading.get_ident()
        _check(self.api.jj_capture_open(monitor['left'],monitor['top'],monitor['width'],monitor['height'],ct.byref(self.handle)),'GPU 抓帧初始化失败')

    def grab(self,region):
        if not self.handle or threading.get_ident()!=self.owner:raise RuntimeError('抓帧线程已结束')
        w,h=region['width'],region['height']
        if self.frame is None or self.frame.shape!=(h,w,4):self.frame=np.empty((h,w,4),np.uint8)
        _check(self.api.jj_capture_grab(self.handle,region['left'],region['top'],w,h,self.frame.ctypes.data,self.frame.strides[0]),'GPU 抓帧失败')
        return self.frame

    def close(self):
        if self.handle:
            self.api.jj_capture_close(self.handle);self.handle=ct.c_void_p();self.frame=None

class GpuWriter:
    def __init__(self,path,w,h,fps,bitrate=0):
        self.api=_library();self.handle=ct.c_void_p();self.w,self.h,self.fps=w,h,int(fps)
        self.owner=threading.get_ident();self._n=0;self._last=-1
        bitrate=bitrate or int(min(max(w*h*fps*.08,1200000),10000000))
        _check(self.api.jj_encoder_open(str(path),w,h,int(fps),bitrate,ct.byref(self.handle)),'GPU 编码初始化失败')

    def write_bgra(self,frame,timestamp=None):
        if not self.handle or threading.get_ident()!=self.owner:raise RuntimeError('编码器线程已结束')
        if frame.shape!=(self.h,self.w,4) or frame.dtype!=np.uint8:raise ValueError('录屏帧尺寸不符')
        timestamp=self._n*10000000//self.fps if timestamp is None else int(timestamp)
        if timestamp<=self._last:raise ValueError('帧时间必须递增')
        frame=np.ascontiguousarray(frame)
        duration=10000000//self.fps
        _check(self.api.jj_encoder_write(self.handle,frame.ctypes.data,frame.strides[0],timestamp,duration),'GPU 编码失败')
        self._n+=1;self._last=timestamp

    @property
    def frames(self):return self._n

    def close(self):
        if self.handle:
            handle=self.handle;self.handle=ct.c_void_p()
            _check(self.api.jj_encoder_close(handle),'视频生成失败')

class GpuRecorder:
    """Desktop texture → GPU crop/scale/overlays → GPU NV12 → system H.264 encoder."""
    def __init__(self,path,monitor,w,h,fps):
        self.api=_library();self.handle=ct.c_void_p();self.owner=threading.get_ident()
        self.w,self.h,self.fps=w,h,int(fps);self.frames=0
        bitrate=int(min(max(w*h*fps*.08,1200000),10000000))
        _check(self.api.jj_recorder_open(str(path),monitor['left'],monitor['top'],monitor['width'],monitor['height'],w,h,int(fps),bitrate,ct.byref(self.handle)),'GPU 录屏初始化失败')

    def write_frame(self,box,timestamp,overlays=()):
        if not self.handle or threading.get_ident()!=self.owner:raise RuntimeError('录屏线程已结束')
        _check(self.api.jj_recorder_prepare(self.handle,box['left'],box['top'],box['width'],box['height']),'GPU 抓帧失败')
        sx,sy=self.w/box['width'],self.h/box['height']
        for sprite,x,y in overlays:
            h,w=sprite.shape[:2]
            if x+w<=box['left'] or y+h<=box['top'] or x>=box['left']+box['width'] or y>=box['top']+box['height']:continue
            sprite=np.ascontiguousarray(sprite)
            _check(self.api.jj_recorder_overlay(self.handle,sprite.ctypes.data,w,h,sprite.strides[0],(x-box['left'])*sx,(y-box['top'])*sy,w*sx,h*sy),'录屏指针绘制失败')
        _check(self.api.jj_recorder_commit(self.handle,int(timestamp),10000000//self.fps),'GPU 编码失败')
        self.frames+=1

    def close(self):
        if self.handle:
            handle=self.handle;self.handle=ct.c_void_p()
            _check(self.api.jj_recorder_close(handle),'视频生成失败')
