# -*- mode: python ; coding: utf-8 -*-
# R107 双包构建：JJ_LITE=1 时出轻量版（截图+长截图+GIF），否则全量版；R120 起两版同裁 cv2（录屏/快剪改系统 MF H.264，二维码识别随退役）。
# 两版共用运行时黑名单：Qt 全家桶未用模块（app 纯 Widgets）、opengl32sw（raster 引擎用不到）、
# PIL._avif/_webp（只出 PNG/JPG/GIF）；HTTPS 走 Windows WinHTTP，不随包分发 Python SSL/OpenSSL。
# R108 再压：未用 Qt 插件（qdirect2d/qminimal/qtuiotouch/qsvgicon + imageformats 仅留 qico）
# 与 translations（仅留 qtbase_zh_CN.qm 兜底系统对话框中文按钮）；LITE 加裁 PIL._imagingft
#（ImageFont 仅全量二维码锁用）。qoffscreen 必须保留——exe --smoke 靠 offscreen 平台跑。
import os
import runpy

LITE = os.environ.get("JJ_LITE") == "1"
_native_binaries = []
if not LITE:
    # Build from the current C++ source before packaging; never silently ship an old DLL.
    _native_build = runpy.run_path(os.path.join(SPECPATH, "native", "build.py"))
    _native_binaries = [(_native_build["build"](), "native")]

_excludes = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQmlModels",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvgWidgets",   # R133：QtSvg 回归（lucide 图标管线），QtSvgWidgets 仍裁
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtNetwork",
    "PySide6.QtTest", "PySide6.QtVirtualKeyboard", "PySide6.QtBluetooth",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtSerialPort", "PySide6.QtTextToSpeech",
    "ssl", "_ssl", "_hashlib",            # R182：HTTPS 用系统 WinHTTP；hashlib 使用 Python 内置算法后端
    "PIL._avif", "PIL._webp", "cv2",            # cv2：R120 起全量版同裁（MF H.264 取代 VideoWriter）
    # R120：懒加载子模块排包（实测 import numpy 时不加载，工程零引用；
    # linalg 在 import 时硬加载不可排）。np.random 原仅冒烟 RandomState 在用，已改确定性噪声。
    "numpy.random", "numpy.fft", "numpy.testing", "PIL.ImageCms",
    # R120 stdlib/杂项死重（全工程 grep 零引用；http/urllib/email/html/unittest 功能与冒烟在用不可排，
    # compression 是 3.14 zipfile 后端不可排）。实测教训（R120 exe 冒烟连环崩）：asyncio 不可排
    # ——unittest.mock 顶层硬 import；concurrent 不可排——asyncio.base_events/futures/tasks 顶层硬 import；
    # codeop 不可排——3.14 traceback 顶层硬 import。
    "multiprocessing", "yaml", "charset_normalizer",
    "xml", "xmlrpc", "csv", "_pyrepl", "pydoc", "pydoc_data", "bdb", "cmd", "code",
]
_hidden = ["psutil", "qrcode"]
if LITE:
    _excludes += ["mss", "qrcode", "fsearch", "sqlite3", "recorder_mf"]   # R120：fsearch 懒导入后 Lite 零引用面，sqlite3 仅它所引；cv2 两版同裁已入公共；recorder_mf 仅录屏用
    _hidden = ["psutil"]

# binaries 文件名黑名单兜底（dll/pyd 经 hook collect 路径进入，excludes 挡不住时在此拦截）
_kill = ("opengl32sw", "qt6qml", "qt6quick", "qt6pdf", "qt6svgwidgets", "qt6opengl",
         "qt6network", "qt6test", "qt6virtualkeyboard", "qtqml", "qtquick",
         "qtpdf", "qtsvgwidgets", "qtopengl", "qtnetwork", "qttest", "qtvirtualkeyboard",
         "_avif",
         # R182：_hashlib 可选的 OpenSSL 后端已排除，防止 ctypes/DLL 扫描再次带入 libcrypto。
         "libcrypto-3.dll", "libssl-3.dll",
         # R161：不要把宿主 PATH 中其他软件的 ICU 打进包（曾误收 Codex Poppler ICU 78，覆盖系统 ICU 后 QtCore 导入失败）
         "icudt", "icuuc", "icuin", "icuio", "icutest", "icutu",
         # R162：Win10+ 由系统 API-set 映射解析，禁止打包宿主机版本以避免版本漂移与包体膨胀。
         "api-ms-win-",
         # R182：WinHTTP 为 Win10+ 系统组件；动态调用即可，禁止被 ctypes 扫入单文件。
         "winhttp",
         # R108：未用插件（qoffscreen/qwindows/qmodernwindowsstyle/qico 保留）
         "qdirect2d", "qminimal", "qtuiotouch", "qsvgicon", "qwebp", "qtiff",
         "qicns", "qtga", "qwbmp", "qpdf", "qsvg", "qjpeg", "qgif", "_webp",
         # R120：ucrtbase.dll（Win10+ 系统组件）、PIL._imagingcms（零引用）、
         # numpy random/fft 子包 pyd 与 _core 测试 pyd（excludes 挡不住 hook collect 路径）
         "ucrtbase", "_imagingcms", "_tests.",
         "numpy/random", "numpy\\random", "numpy/fft", "numpy\\fft",
         # R120：stdlib 排包对应的 pyd/mypyc 扩展兜底
         "md__mypyc", "charset_normalizer", "_multiprocessing", "_yaml",
         "cv2", "opencv")
if LITE:
    _kill += ("mss", "_imagingft", "sqlite3")     # R120：sqlite3.dll 黑名单兜底；cv2/opencv 已入公共
else:
    # 手机照片直接由 Qt 解码，保留 JPEG 插件；PNG 已由 QtGui 内置。
    _kill = tuple(item for item in _kill if item != "qjpeg")

a = Analysis(
    ['main.pyw'],
    pathex=[],
    binaries=_native_binaries,
    datas=[],
    hiddenimports=_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_excludes,
    noarchive=False,
    optimize=0,
)
a.binaries = [b for b in a.binaries if not any(k in b[0].lower() for k in _kill)]


def _keep_data(d):
    """R108：Qt 翻译只留 qtbase_zh_CN.qm（系统对话框中文按钮），其余语言全裁。"""
    n = d[0].lower().replace("\\", "/")
    if "/translations/" in n and n.endswith(".qm"):
        return n.endswith("qtbase_zh_cn.qm")
    return True


a.datas = [d for d in a.datas if _keep_data(d)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='简截Lite' if LITE else '简截',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['_app.ico'],
    version='version.txt',
)
