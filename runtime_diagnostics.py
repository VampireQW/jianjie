"""Bounded local failure records, separate from user-facing operation messages."""
from datetime import datetime, timezone
import json
from pathlib import Path
import threading

import common as C

_LOCK = threading.Lock()


def record_failure(stage, error):
    try:
        path = Path(C.data_dir()) / "mirroring" / "diagnostics.jsonl"
        entry = json.dumps(dict(time=datetime.now(timezone.utc).isoformat(), build=C.APP_BUILD_ID,
                                stage=stage, error=type(error).__name__, detail=str(error)[:1400],
                                winerror=getattr(error, "winerror", None), errno=getattr(error, "errno", None)),
                           ensure_ascii=False) + "\n"
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 96 * 1024:
                path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as target:
                target.write(entry)
    except OSError:
        pass


def component_failure(error):
    blocked = getattr(error, "winerror", None) in (225, 226)
    filename = str(getattr(error, "filename", "") or "").lower()
    if blocked:
        return "投屏组件被 Windows 安全防护拦截，请查看保护历史记录。", True
    if getattr(error, "errno", None) == 22 and filename.endswith("receiver.exe"):
        return "投屏组件暂时无法读取，请查看安全防护记录后重试。", True
    if isinstance(error, ValueError) and str(error).startswith(("组件包", "投屏引擎")):
        return str(error)[:160], False
    if isinstance(error, FileNotFoundError):
        return "投屏组件缺失，请重新准备组件。", False
    if isinstance(error, PermissionError):
        return "暂时无法更新投屏组件，请关闭其他投屏窗口后重试。", False
    return "投屏组件暂时不可用，请重新准备或导入本地组件包。", False


def control_failure(message=""):
    message = str(message)
    if (message.startswith(("蓝牙", "输入", "鼠标", "键盘", "连接", "电脑控制"))
            and not any(part in message for part in ("[Errno", "0x", "HRESULT", "Traceback", ":\\"))):
        return message[:160]
    return "电脑控制暂时不可用，请检查蓝牙连接后重新开启。"
