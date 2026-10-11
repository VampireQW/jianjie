# -*- coding: utf-8 -*-
"""简截 JianJie for Windows — 动作真实执行器：启动应用/打开文件夹/网页/新建空白文档/PowerShell/系统操作/OCR/二维码/快剪。"""
import os
import re
import subprocess
import sys
import webbrowser
import zipfile
from pathlib import Path

import common as C
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

_SAFE_NAME = re.compile(r"^[\w\u4e00-\u9fff .\-]+$")


def toast_tray(msg):
    """由 main 持有的托盘气泡；此处仅提供接口占位，避免循环导入。"""
    raise NotImplementedError


# ---------- 应用启动 ----------
def find_wechat():
    cands = [
        os.path.expandvars(r"%ProgramFiles%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%LocalAppData%\Programs\Tencent\WeChat\WeChat.exe"),
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None

def find_vscode():
    for name in ("code.cmd", "code"):
        p = subprocess.run(["where", name], capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().splitlines()[0]
    return None

APP_TABLE = {
    "记事本": ["notepad"],
    "计算器": ["calc"],
    "画图": ["mspaint"],
    "文件资源管理器": ["explorer"],
    "终端": ["cmd"],
    "任务管理器": ["taskmgr"],
}

def launch_app(name, notify=None):
    """真实启动：内置表 > 已知安装路径 > 失败提示。"""
    try:
        if name in APP_TABLE:
            subprocess.Popen(APP_TABLE[name])
            return True, f"已启动 · {name}"
        if name == "微信":
            p = find_wechat()
            if p:
                subprocess.Popen([p])
                return True, f"已启动 · 微信"
            return False, "未找到微信安装路径，可将其快捷方式路径加入动作绑定"
        if name == "Visual Studio Code":
            p = find_vscode()
            if p:
                subprocess.Popen(["cmd", "/c", p])
                return True, "已启动 · Visual Studio Code"
            return False, "未找到 VS Code（code 命令），请确认已安装并加入 PATH"
        # 未知名称：交给 shell start（支持 start 菜单别名）；拒绝含元字符的输入
        if not _SAFE_NAME.match(name):
            return False, "名称包含不允许的字符"
        subprocess.Popen(["cmd", "/c", "start", "", name], creationflags=subprocess.CREATE_NO_WINDOW)
        return True, f"已尝试启动 · {name}"
    except Exception as e:
        return False, f"启动失败：{e}"


def open_path(path, notify=None):
    try:
        os.startfile(path)  # noqa
        return True, f"已打开 · {os.path.basename(path.rstrip(chr(92))) or path}"
    except Exception as e:
        return False, f"打开失败：{e}"


def open_url(url, notify=None):
    try:
        webbrowser.open(url)
        return True, f"正在打开 · {url}"
    except Exception as e:
        return False, f"打开失败：{e}"


# ---------- 新建空白文档（真实 OOXML，Word/Excel 可直接打开）----------
def new_blank_docx(target_dir):
    path = os.path.join(target_dir, f"新建文档_{timestamp_safe()}.docx")
    ct = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    doc = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t></w:t></w:r></w:p></w:body></w:document>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return path


def new_blank_xlsx(target_dir):
    path = os.path.join(target_dir, f"新建工作表_{timestamp_safe()}.xlsx")
    ct = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    wb = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    sheet = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData><row r="1"><c r="A1"><v></v></c></row></sheetData></worksheet>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def timestamp_safe():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def new_blank_doc(kind, notify=None):
    """kind: word / excel / ppt。返回 (ok, msg)。目录逐级试写，不可写时自动回退。"""
    target = C.documents_dir()
    try:
        if kind == "word":
            p = new_blank_docx(str(target))
        elif kind == "excel":
            p = new_blank_xlsx(str(target))
        else:
            ppt = _find_powerpnt()
            if ppt:
                subprocess.Popen([ppt])
                return True, "已打开 PowerPoint，请新建空白演示文稿"
            return False, "未找到 PowerPoint，ppt 空白文档暂不可用"
        os.startfile(p)  # noqa
        return True, f"已新建 · {os.path.basename(p)}（位于 {target}）"
    except Exception as e:
        return False, f"新建失败：{e}"


def _find_powerpnt():
    import winreg
    for hive, root in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\powerpnt.exe"),
                       (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\powerpnt.exe")):
        try:
            k = winreg.OpenKey(hive, root)
            v, _ = winreg.QueryValueEx(k, None)
            if os.path.isfile(v):
                return v
        except Exception:
            pass
    return None


# ---------- PowerShell ----------
def run_powershell(command, timeout=60):
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
        )
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        txt = out if out else err if err else "（无输出）"
        return p.returncode == 0, txt
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as e:
        return False, str(e)


# ---------- 系统操作 ----------
def lock_screen():
    import ctypes
    return ctypes.windll.user32.LockWorkStation()


def show_desktop():
    """模拟 Win+D：切回桌面空间。"""
    import ctypes
    import time
    user32 = ctypes.windll.user32
    VK_LWIN, VK_D = 0x5B, 0x44
    KEYUP = 0x0002
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_D, 0, KEYUP, 0)
    user32.keybd_event(VK_LWIN, 0, KEYUP, 0)
    return True


# ---------- 结果提示对话框 ----------
def result_dialog(title, text, parent=None, on_top=False):
    m = QMessageBox(parent)
    if on_top:
        m.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    m.setWindowTitle(f"{C.APP_NAME} · {title}")
    m.setText(text[:4000])
    m.setTextInteractionFlags(m.textInteractionFlags() | m.textInteractionFlags().TextSelectableByMouse)
    m.exec()


# ---------- 离线 OCR / 二维码 / 快剪 / 自启 ----------
_OCR_PS = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$Global:asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $Global:asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
$img = $env:JG_IMG
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($img)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language('zh-Hans-CN'))) } catch {} }
if (-not $engine) { Write-Output '__NO_OCR_ENGINE__'; exit 0 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
Write-Output $result.Text
'''

def ocr_image(path):
    """系统自带 OCR（离线）。返回 (ok, text)；引擎缺失时提示装语言包。"""
    try:
        env = {**os.environ, "JG_IMG": path}
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-Command", _OCR_PS],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=60)
        out = (r.stdout or "").strip()
        if not out:
            return False, (r.stderr or "OCR 无输出").strip()[:300]
        if out == "__NO_OCR_ENGINE__":
            return False, "系统缺少 OCR 引擎：请在 设置>时间和语言>语言 添加中文语言包"
        # Windows OCR 会在中日韩字符间插空格，清理 CJK 相邻空格
        out = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f，。：；！？]) +(?=[\u4e00-\u9fff\u3000-\u303f，。：；！？])", "", out)
        return True, out
    except Exception as e:
        return False, str(e)

def qr_generate(text, path):
    """生成二维码 PNG，返回 (ok, err)。"""
    try:
        import qrcode
        qrcode.make(text).save(path)
        return True, ""
    except Exception as e:
        return False, str(e)

def trim_video(src, start_s, end_s, out):
    """按秒截取视频片段（R120：MF 重编码 H.264 取代 cv2、无音轨）。返回 (ok, err)。"""
    try:
        from recorder_mf import MfH264Writer, MfFrameReader
        rd = MfFrameReader(src)
        try:
            s = max(0.0, start_s)
            e = max(s + 0.1, end_s)
            wr = None
            n = 0
            try:
                for ts, frame in rd.frames():
                    sec = ts / 10_000_000
                    if sec < s:
                        continue
                    if sec > e:
                        break
                    if wr is None:                    # 窗口内首帧才建编码器：零命中不留空文件
                        wr = MfH264Writer(out, rd.w, rd.h, rd.fps)
                    wr.write_bgra(frame)
                    n += 1
            finally:
                if wr:
                    wr.close()
        finally:
            rd.close()
        if n == 0:
            return False, "区间内没有任何帧（检查起止秒）"
        return True, f"{n} 帧 · {os.path.getsize(out)} 字节"
    except Exception as e:
        return False, str(e)

def latest_file(folder, exts):
    """目录下最近修改的指定扩展名文件，无则 None。"""
    try:
        cands = [os.path.join(folder, f) for f in os.listdir(folder)
                 if os.path.splitext(f)[1].lower() in exts]
        return max(cands, key=os.path.getmtime) if cands else None
    except Exception:
        return None

_AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _autostart_command():
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            pw = exe[:-10] + "pythonw.exe"
            if os.path.isfile(pw):
                exe = pw
        cmd = f'"{exe}" "{os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.pyw")}"'
    return cmd + " --background"


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY) as key:
            command, _ = winreg.QueryValueEx(key, C.APP_NAME)
        return command.strip().casefold() == _autostart_command().casefold()
    except OSError:
        return False


def autostart(enable):
    """开机自启：HKCU Run 键。冻结 exe 模式指向 exe 自身；脚本模式指向 pythonw + main.pyw。返回 (ok, err)。"""
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY,
                               0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            if enable:
                cmd = _autostart_command()
                winreg.SetValueEx(k, C.APP_NAME, 0, winreg.REG_SZ, cmd)
                if winreg.QueryValueEx(k, C.APP_NAME)[0] != cmd:
                    raise OSError("开机自启设置未能保存")
            else:
                try:
                    winreg.DeleteValue(k, C.APP_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except Exception as e:
        return False, str(e)


def check_dependencies():
    """返回缺失的可选依赖说明。"""
    missing = []
    for mod, use in (("mss", "录屏取帧"), ("cv2", "录屏编码"), ("numpy", "长截图拼接"), ("psutil", "网速小组件")):
        try:
            __import__(mod)
        except Exception:
            missing.append(f"{mod}（{use}）")
    return missing
