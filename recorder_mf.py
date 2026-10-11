# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — Media Foundation H.264 编码器（R120：替换 cv2.VideoWriter）。

ctypes 直调 MF Sink Writer，零第三方依赖：全量版录屏因此可整体裁掉 cv2（-43.6MB）。
ABI 事实全部对表 wine 公开头文件镜像（与 MS SDK 一致，2026-09-24）：
  mfapi.h         GUID 值 / MF_VERSION=0x00020070 / MFStartup / MFCreateMediaType
                  / MFCreateSample / MFCreateMemoryBuffer
  mfobjects.idl   IMFAttributes(21=SetUINT32, 22=SetUINT64, 24=SetGUID)
                  IMFMediaBuffer(3=Lock, 4=Unlock, 6=SetCurrentLength)
                  IMFSample(36=SetSampleTime, 38=SetSampleDuration, 42=AddBuffer)
  mfreadwrite.idl IMFSinkWriter(3=AddStream, 4=SetInputMediaType, 5=BeginWriting,
                  6=WriteSample, 11=Finalize)，IID=3137f1cd-fe5e-4805-a5d8-fb477448cb3d
                  IMFSourceReader(9=ReadSample)
                  MFCreateSinkWriterFromURL(url,bstm,attr,&w) / MFCreateSourceReaderFromURL(url,attr,&r)
最小链路已经 _mf_probe.py 验证：45 帧 320x240 NV12 → 251KB MP4（ftyp mp42）。
"""
import ctypes
import threading
from ctypes import (HRESULT, POINTER, byref, c_uint8, c_uint16, c_uint32,
                    c_uint64, c_int64, c_void_p, c_wchar_p)

import numpy as np

_ole32 = ctypes.OleDLL("ole32")
_mfplat = ctypes.WinDLL("mfplat")
_mfrw = ctypes.WinDLL("mfreadwrite")


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_uint32), ("Data2", c_uint16), ("Data3", c_uint16),
                ("Data4", c_uint8 * 8)]


def _g(d1, d2, d3, *d4):
    g = GUID()
    g.Data1, g.Data2, g.Data3 = d1, d2, d3
    for i, b in enumerate(d4):
        g.Data4[i] = b
    return g


_MF_MT_MAJOR_TYPE = _g(0x48eba18e, 0xf8c9, 0x4687, 0xbf, 0x11, 0x0a, 0x74, 0xc9, 0xf9, 0x6a, 0x8f)
_MF_MT_SUBTYPE = _g(0xf7e34c9a, 0x42e8, 0x4714, 0xb7, 0x4b, 0xcb, 0x29, 0xd7, 0x2c, 0x35, 0xe5)
_MF_MT_FRAME_SIZE = _g(0x1652c33d, 0xd6b2, 0x4012, 0xb8, 0x34, 0x72, 0x03, 0x08, 0x49, 0xa3, 0x7d)
_MF_MT_FRAME_RATE = _g(0xc459a2e8, 0x3d2c, 0x4e44, 0xb1, 0x32, 0xfe, 0xe5, 0x15, 0x6c, 0x7b, 0xb0)
_MF_MT_PAR = _g(0xc6376a1e, 0x8d0a, 0x4027, 0xbe, 0x45, 0x6d, 0x9a, 0x0a, 0xd3, 0x9b, 0xb6)
_MF_MT_INTERLACE_MODE = _g(0xe2724bb8, 0xe676, 0x4806, 0xb4, 0xb2, 0xa8, 0xd6, 0xef, 0xb4, 0x4c, 0xcd)
_MF_MT_AVG_BITRATE = _g(0x20332624, 0xfb0d, 0x4d9e, 0xbd, 0x0d, 0xcb, 0xf6, 0x78, 0x6c, 0x10, 0x2e)
_MF_MT_ALL_INDEP = _g(0xc9173739, 0x5e56, 0x461c, 0xb7, 0x13, 0x46, 0xfb, 0x99, 0x5c, 0xb9, 0x5f)
_MF_MT_DEFAULT_STRIDE = _g(0x644b4e48, 0x1e02, 0x4516, 0xb0, 0xeb, 0xc0, 0x1c, 0xa9, 0xd4, 0x9a, 0xc6)
_MFMediaType_Video = _g(0x73646976, 0, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)
_MFVideoFormat_H264 = _g(0x34363248, 0, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)  # 'H264'
_MFVideoFormat_NV12 = _g(0x3231564e, 0, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)  # 'NV12'
_MFVideoFormat_RGB32 = _g(0x00000016, 0, 0x0010, 0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71)  # D3DFMT_X8R8G8B8=22
_MF_VERSION = 0x00020070                     # MF_SDK_VERSION(2)<<16 | MF_API_VERSION(0x70)，Win8+
_PROGRESSIVE = 2
_FIRST_VIDEO_STREAM = 0xFFFFFFFC             # MF_SOURCE_READER_FIRST_VIDEO_STREAM


def _vt(obj, idx):
    return ctypes.cast(ctypes.cast(obj, POINTER(c_void_p)).contents, POINTER(c_void_p))[idx]


def _proto(obj, idx, restype, argtypes):
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(_vt(obj, idx))


def _release(obj):
    if obj:
        _proto(obj, 2, c_uint32, ())(obj)


def _check(hr, what):
    if hr != 0:
        raise RuntimeError("%s 失败 hr=0x%08X" % (what, hr & 0xFFFFFFFF))


# ---- MFStartup 配对（进程级引用计数；CoInitializeEx 归各调用线程自理） ----
_mf_lock = threading.Lock()
_mf_ref = 0


def _mf_acquire():
    global _mf_ref
    with _mf_lock:
        if _mf_ref == 0:
            _check(_mfplat.MFStartup(c_uint32(_MF_VERSION), c_uint32(0)), "MFStartup")
        _mf_ref += 1


def _mf_release():
    global _mf_ref
    with _mf_lock:
        _mf_ref -= 1
        if _mf_ref == 0:
            _mfplat.MFShutdown()


_RPC_E_CHANGED_MODE = -2147417850   # 0x80010106：线程已被初始化为另一套间模型


def _co_init():
    """本线程 COM 初始化，返回是否持有引用（True 则关闭侧必须配对 CoUninitialize）。

    真机坑（R120 补）：Qt windows 平台早已把 UI 线程初始化为 STA，再请求 MTA 得
    RPC_E_CHANGED_MODE；ctypes 对 HRESULT restype 失败自动 raise OSError，必须接住——
    该失败不持有引用，关闭侧不得 CoUninitialize，否则偷拆 Qt 的 STA 引用，一轮录停后
    UI 线程 COM 失衡（剪贴板/OLE 连坐）。S_OK/S_FALSE 都算成功持有。MF 在 STA 下正常
    工作，无需强求 MTA。offscreen 冒烟平台不初始化 COM，此坑只有真平台能触发。"""
    try:
        _ole32.CoInitializeEx(None, 0x0)      # COINIT_MULTITHREADED
        return True
    except OSError as e:
        if e.winerror == _RPC_E_CHANGED_MODE:
            return False
        raise


def bgr_to_nv12(bgr):
    """numpy BGR(H,W,3) → NV12 字节（BT.601 工作室范围，MF H.264 编码器默认矩阵）。"""
    h, w = bgr.shape[:2]
    b = bgr[..., 0].astype(np.int32)
    g = bgr[..., 1].astype(np.int32)
    r = bgr[..., 2].astype(np.int32)
    y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16
    # 2x2 块均值下采样色度（整数域：求和后移位，避免 float 右移）
    b2 = b.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3)) >> 2
    g2 = g.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3)) >> 2
    r2 = r.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3)) >> 2
    u = ((-38 * r2 - 74 * g2 + 112 * b2 + 128) >> 8) + 128
    v = ((112 * r2 - 94 * g2 - 18 * b2 + 128) >> 8) + 128
    uv = np.empty((h // 2, w), np.uint8)
    uv[:, 0::2] = np.clip(u, 0, 255)
    uv[:, 1::2] = np.clip(v, 0, 255)
    return np.vstack([np.clip(y, 0, 255).astype(np.uint8), uv]).tobytes()


class MfH264Writer:
    """MF Sink Writer H.264 MP4 编码器。接口对齐旧 cv2.VideoWriter 用法：write_bgra/close。

    输入 MFVideoFormat_RGB32——其内存字节序恰为 mss 原生 BGRA，抓帧零转换直喂；
    RGB→NV12 色彩转换由 Sink Writer 自动加载的系统 Color Converter DSP 完成（SIMD 多线程），
    Python 侧只剩一次翻转拷贝（MF 未压缩 RGB 默认 bottom-up，负 stride 属性实测被 DSP 忽略，
    只能 flipud 喂帧——1080p 约 4ms，远低于 numpy 自转 NV12 的 130ms）。"""

    def __init__(self, path, w, h, fps, bitrate=0):
        w, h = w & ~1, h & ~1                     # H.264 宏块偶数约束
        if w < 16 or h < 16:
            raise ValueError("编码尺寸过小：%dx%d" % (w, h))
        self.w, self.h, self.fps = w, h, int(fps)
        self.path = path
        self._n = 0                               # 已写帧数（时间戳=帧号×帧长，严格递增）
        self._dur = 10_000_000 // self.fps        # 100ns 单位
        self._closed = False
        # Sink Writer 在创建它的线程里用 COM：本线程初始化（Qt UI 线程已 STA，见 _co_init）
        self._coinit = _co_init()
        _mf_acquire()
        self._mf = True
        try:
            writer = c_void_p()
            _check(_mfrw.MFCreateSinkWriterFromURL(c_wchar_p(path), None, None, byref(writer)),
                   "MFCreateSinkWriterFromURL")
            self._writer = writer

            otype = self._make_type(_MFVideoFormat_H264, bitrate or self._default_bitrate())
            idx = c_uint32(0)
            _check(_proto(writer, 3, HRESULT, (c_void_p, c_void_p))(writer, otype, byref(idx)),
                   "AddStream")
            self._idx = idx.value
            _release(otype)

            itype = self._make_type(_MFVideoFormat_RGB32, 0)
            _check(_proto(writer, 4, HRESULT, (c_uint32, c_void_p, c_void_p))(
                writer, self._idx, itype, None), "SetInputMediaType(RGB32)")
            _release(itype)

            _check(_proto(writer, 5, HRESULT, ())(writer), "BeginWriting")
        except Exception:
            self.close()
            raise

    def _default_bitrate(self):
        # 1080p30 ≈ 5Mbps；下限保小窗口清晰度，上限防超大选区码率虚高
        return int(min(max(self.w * self.h * self.fps * 0.08, 1_200_000), 10_000_000))

    def _make_type(self, subtype, bitrate):
        mt = c_void_p()
        _check(_mfplat.MFCreateMediaType(byref(mt)), "MFCreateMediaType")
        set_guid = _proto(mt, 24, HRESULT, (c_void_p, c_void_p))
        set_u32 = _proto(mt, 21, HRESULT, (c_void_p, c_uint32))
        set_u64 = _proto(mt, 22, HRESULT, (c_void_p, c_uint64))
        _check(set_guid(mt, byref(_MF_MT_MAJOR_TYPE), byref(_MFMediaType_Video)), "SetGUID(major)")
        _check(set_guid(mt, byref(_MF_MT_SUBTYPE), byref(subtype)), "SetGUID(subtype)")
        if bitrate:
            _check(set_u32(mt, byref(_MF_MT_AVG_BITRATE), bitrate), "SetUINT32(bitrate)")
            _check(set_u32(mt, byref(_MF_MT_ALL_INDEP), 1), "SetUINT32(all-indep)")
        _check(set_u64(mt, byref(_MF_MT_FRAME_SIZE), (self.w << 32) | self.h), "SetUINT64(size)")
        _check(set_u64(mt, byref(_MF_MT_FRAME_RATE), (self.fps << 32) | 1), "SetUINT64(fps)")
        _check(set_u64(mt, byref(_MF_MT_PAR), (1 << 32) | 1), "SetUINT64(par)")
        _check(set_u32(mt, byref(_MF_MT_INTERLACE_MODE), _PROGRESSIVE), "SetUINT32(interlace)")
        return mt

    def write_bgra(self, bgra):
        """喂一帧 numpy BGRA(H,W,4)（mss 原生字节序）；尺寸必须等于构造尺寸（构造期已裁偶）。"""
        if self._closed:
            raise RuntimeError("编码器已关闭")
        if bgra.shape[0] != self.h or bgra.shape[1] != self.w:
            raise ValueError("帧尺寸 %dx%d 与编码器 %dx%d 不符"
                             % (bgra.shape[1], bgra.shape[0], self.w, self.h))
        data = np.ascontiguousarray(bgra[::-1]).tobytes()     # bottom-up 翻转（见类文档串）
        mbuf = c_void_p()
        _check(_mfplat.MFCreateMemoryBuffer(c_uint32(len(data)), byref(mbuf)), "MFCreateMemoryBuffer")
        try:
            pbuf, pmax, pcur = c_void_p(), c_uint32(0), c_uint32(0)
            _check(_proto(mbuf, 3, HRESULT, (c_void_p, c_void_p, c_void_p))(
                mbuf, byref(pbuf), byref(pmax), byref(pcur)), "Buffer.Lock")
            ctypes.memmove(pbuf.value, data, len(data))
            _proto(mbuf, 4, HRESULT, ())(mbuf)
            _check(_proto(mbuf, 6, HRESULT, (c_uint32,))(mbuf, len(data)), "SetCurrentLength")

            samp = c_void_p()
            _check(_mfplat.MFCreateSample(byref(samp)), "MFCreateSample")
            try:
                _check(_proto(samp, 42, HRESULT, (c_void_p,))(samp, mbuf), "Sample.AddBuffer")
                _check(_proto(samp, 36, HRESULT, (c_int64,))(samp, self._n * self._dur), "SetSampleTime")
                _check(_proto(samp, 38, HRESULT, (c_int64,))(samp, self._dur), "SetSampleDuration")
                _check(_proto(self._writer, 6, HRESULT, (c_uint32, c_void_p))(
                    self._writer, self._idx, samp), "WriteSample")
                self._n += 1
            finally:
                _release(samp)
        finally:
            _release(mbuf)

    @property
    def frames(self):
        return self._n

    def close(self):
        if self._closed:
            return
        self._closed = True
        w = getattr(self, "_writer", None)
        if w:
            try:
                _check(_proto(w, 11, HRESULT, ())(w), "Finalize")     # 零帧时 Finalize 会报错
            except Exception:
                pass                                                   # 关闭路径不再抛，由调用方查文件
            _release(w)
            self._writer = None
        if getattr(self, "_mf", False):
            _mf_release()
            self._mf = False
        if getattr(self, "_coinit", False):
            _ole32.CoUninitialize()
            self._coinit = False


def read_back_info(path, max_samples=10**6):
    """MF Source Reader 读回验证（冒烟锁用）：返回 (帧数, 宽, 高, 首帧非全黑)。"""
    _co_owned = _co_init()
    _mf_acquire()
    reader = None
    try:
        reader = c_void_p()
        _check(_mfrw.MFCreateSourceReaderFromURL(c_wchar_p(path), None, byref(reader)),
               "MFCreateSourceReaderFromURL")
        # 默认按 native type（H.264 压缩流）输出；显式要 NV12，Source Reader 才加载解码 MFT
        nvt = c_void_p()
        _check(_mfplat.MFCreateMediaType(byref(nvt)), "MFCreateMediaType")
        _check(_proto(nvt, 24, HRESULT, (c_void_p, c_void_p))(
            nvt, byref(_MF_MT_MAJOR_TYPE), byref(_MFMediaType_Video)), "SetGUID(major)")
        _check(_proto(nvt, 24, HRESULT, (c_void_p, c_void_p))(
            nvt, byref(_MF_MT_SUBTYPE), byref(_MFVideoFormat_NV12)), "SetGUID(nv12)")
        _check(_proto(reader, 7, HRESULT, (c_uint32, c_void_p, c_void_p))(
            reader, _FIRST_VIDEO_STREAM, None, nvt), "SetCurrentMediaType(NV12)")
        _release(nvt)
        n, first_len, first_nonblack = 0, 0, False
        read_sample = _proto(reader, 9, HRESULT,
                             (c_uint32, c_uint32, c_void_p, c_void_p, c_void_p, c_void_p))
        while n < max_samples:
            idx, flags, ts, samp = c_uint32(0), c_uint32(0), c_int64(0), c_void_p()
            hr = read_sample(reader, _FIRST_VIDEO_STREAM, 0,
                             byref(idx), byref(flags), byref(ts), byref(samp))
            _check(hr, "ReadSample")
            if not samp.value:                        # EOF（END_OF_STREAM flag 或空样本）
                break
            if n == 0:
                # 首帧：取样本长度与内容（解码默认输出 NV12：Y 平面 w*h 在前）
                buf = c_void_p()
                _check(_proto(samp, 40, HRESULT, (c_uint32, c_void_p))(samp, 0, byref(buf)),
                       "GetBufferByIndex")
                pbuf, pmax, pcur = c_void_p(), c_uint32(0), c_uint32(0)
                _check(_proto(buf, 3, HRESULT, (c_void_p, c_void_p, c_void_p))(
                    buf, byref(pbuf), byref(pmax), byref(pcur)), "Buffer.Lock")
                head = ctypes.string_at(pbuf.value, min(pcur.value, 1 << 20))
                _proto(buf, 4, HRESULT, ())(buf)
                _release(buf)
                first_len = pcur.value                    # NV12 长度 = w*h*3/2 → 冒烟按长度比对尺寸
                first_nonblack = any(b > 40 for b in head[:4096])
                _release(samp)
                n += 1
                continue
            _release(samp)
            n += 1
        # 尺寸反推（NV12）：w*h = first_len*2/3；宽高比未知——冒烟用固定尺寸即可比对长度
        return n, first_len, first_nonblack
    finally:
        _release(reader)
        _mf_release()
        if _co_owned:
            _ole32.CoUninitialize()


def nv12_to_bgra(nv12, w, h):
    """NV12 uint8 数组 → top-down BGRA ndarray（BT.601 工作室范围，bgr_to_nv12 的逆变换）。

    2x2 最近邻上采样色度（与编码侧 2x2 均值下采样对偶）；int32 域，1080p 单帧约 20ms。"""
    y = nv12[:h * w].reshape(h, w).astype(np.int32)
    uv = nv12[h * w:h * w * 3 // 2].reshape(h // 2, w)
    u = np.repeat(np.repeat(uv[:, 0::2], 2, axis=0), 2, axis=1).astype(np.int32) - 128
    v = np.repeat(np.repeat(uv[:, 1::2], 2, axis=0), 2, axis=1).astype(np.int32) - 128
    c = y - 16
    out = np.empty((h, w, 4), np.uint8)
    out[..., 0] = np.clip((298 * c + 516 * u + 128) >> 8, 0, 255)
    out[..., 1] = np.clip((298 * c - 100 * u - 208 * v + 128) >> 8, 0, 255)
    out[..., 2] = np.clip((298 * c + 409 * v + 128) >> 8, 0, 255)
    out[..., 3] = 255
    return out


class MfFrameReader:
    """MF Source Reader 逐帧解码为 top-down BGRA（R120 快剪/验证读帧用）。

    输出类型设 NV12（解码 MFT 原生格式；RGB32 实测被 Source Reader 拒——硬解输出不能直接
    进 Color Converter DSP 转 RGB，MF_E_INVALIDMEDIATYPE），numpy 转 BGRA（快剪是离线操作，
    1080p 约 20ms/帧无实时压力）。YUV 帧无 bottom-up 惯例，解码输出恒 top-down。
    公开尺寸 w/h 已裁偶（H.264 重写宏块约束），内部 _w0/_h0 为源帧原始尺寸。"""

    def __init__(self, path):
        self._coinit = _co_init()
        _mf_acquire()
        self._mf = True
        self._closed = False
        try:
            reader = c_void_p()
            _check(_mfrw.MFCreateSourceReaderFromURL(c_wchar_p(path), None, byref(reader)),
                   "MFCreateSourceReaderFromURL")
            self._reader = reader
            mt = c_void_p()
            _check(_mfplat.MFCreateMediaType(byref(mt)), "MFCreateMediaType")
            _check(_proto(mt, 24, HRESULT, (c_void_p, c_void_p))(
                mt, byref(_MF_MT_MAJOR_TYPE), byref(_MFMediaType_Video)), "SetGUID(major)")
            _check(_proto(mt, 24, HRESULT, (c_void_p, c_void_p))(
                mt, byref(_MF_MT_SUBTYPE), byref(_MFVideoFormat_NV12)), "SetGUID(nv12)")
            _check(_proto(reader, 7, HRESULT, (c_uint32, c_void_p, c_void_p))(
                reader, _FIRST_VIDEO_STREAM, None, mt), "SetCurrentMediaType(NV12)")
            _release(mt)
            # 实际输出参数（GetCurrentMediaType=6，GetUINT64=8）：尺寸与帧率
            cur = c_void_p()
            _check(_proto(reader, 6, HRESULT, (c_uint32, c_void_p))(
                reader, _FIRST_VIDEO_STREAM, byref(cur)), "GetCurrentMediaType")
            get_u64 = _proto(cur, 8, HRESULT, (c_void_p, c_void_p))
            v = c_uint64(0)
            _check(get_u64(cur, byref(_MF_MT_FRAME_SIZE), byref(v)), "GetUINT64(size)")
            self._w0, self._h0 = int(v.value >> 32), int(v.value & 0xFFFFFFFF)
            self.w, self.h = self._w0 & ~1, self._h0 & ~1
            v2 = c_uint64(0)
            hr = get_u64(cur, byref(_MF_MT_FRAME_RATE), byref(v2))
            den = v2.value & 0xFFFFFFFF
            self.fps = (v2.value >> 32) / den if hr == 0 and den else 30.0
            _release(cur)
            if self.w < 16 or self.h < 16:
                raise ValueError("源视频尺寸过小：%dx%d" % (self._w0, self._h0))
        except Exception:
            self.close()
            raise

    def frames(self, max_samples=10**8):
        """yield (ts_100ns, top-down BGRA ndarray，裁偶后 w×h)。调用方负责 close()。"""
        read_sample = _proto(self._reader, 9, HRESULT,
                             (c_uint32, c_uint32, c_void_p, c_void_p, c_void_p, c_void_p))
        n = 0
        while n < max_samples and not self._closed:
            idx, flags, ts, samp = c_uint32(0), c_uint32(0), c_int64(0), c_void_p()
            _check(read_sample(self._reader, _FIRST_VIDEO_STREAM, 0,
                               byref(idx), byref(flags), byref(ts), byref(samp)), "ReadSample")
            if not samp.value:                        # EOF
                break
            n += 1
            try:
                buf = c_void_p()
                _check(_proto(samp, 40, HRESULT, (c_uint32, c_void_p))(samp, 0, byref(buf)),
                       "GetBufferByIndex")
                try:
                    pbuf, pmax, pcur = c_void_p(), c_uint32(0), c_uint32(0)
                    _check(_proto(buf, 3, HRESULT, (c_void_p, c_void_p, c_void_p))(
                        buf, byref(pbuf), byref(pmax), byref(pcur)), "Buffer.Lock")
                    if pcur.value != self._h0 * self._w0 * 3 // 2:
                        raise RuntimeError("NV12 帧非紧凑布局：%d != %d（stride 填充未支持）"
                                           % (pcur.value, self._h0 * self._w0 * 3 // 2))
                    arr = np.frombuffer(ctypes.string_at(pbuf.value, pcur.value), np.uint8)
                    _proto(buf, 4, HRESULT, ())(buf)
                finally:
                    _release(buf)
            finally:
                _release(samp)
            top = nv12_to_bgra(arr, self._w0, self._h0)      # 恒 top-down（YUV 无 bottom-up 惯例）
            yield int(ts.value), top[:self.h, :self.w]

    def close(self):
        if self._closed:
            return
        self._closed = True
        r = getattr(self, "_reader", None)
        if r:
            _release(r)
            self._reader = None
        if getattr(self, "_mf", False):
            _mf_release()
            self._mf = False
        if getattr(self, "_coinit", False):
            _ole32.CoUninitialize()
            self._coinit = False
