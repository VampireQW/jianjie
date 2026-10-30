"""独立于 GUI 的本机调用端；未来的模型工具执行器可以复用 call()。"""
import http.client
import json
from .paths import endpoint_file
from .core import TaskError, WRITE_TOOLS


def call_online(name, arguments, descriptor=None, submitted_day=None):
    try:
        info = json.loads((descriptor or endpoint_file()).read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError()
        port, token = info["port"], info["token"]
        if (type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str)
                or type(info.get("version", 1)) is not int):
            raise ValueError()
    except (OSError, ValueError, KeyError):
        raise TaskError("请启动简截，并在托盘菜单启用本地 MCP", "app_unavailable") from None
    if submitted_day is not None and info.get("version", 1) < 2:
        raise TaskError("请启动支持待提交队列的新版简截", "upgrade_required")
    # 地址固定为本机，绕过系统代理；不会读取描述文件中的任意主机地址。
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    sending = False
    try:
        conn.connect()
        headers = {"Authorization": "Bearer " + token,
                   "Content-Type": "application/json", "User-Agent": "JianjieLocalMCP/2"}
        if submitted_day is not None:
            headers["X-Jianjie-Submitted-Date"] = submitted_day
        sending = True
        conn.request("POST", "/v1/tools/call", json.dumps({"name": name, "arguments": arguments},
                     ensure_ascii=False).encode(), headers)
        response = conn.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError()
        if response.status == 403:
            raise TaskError("本地 MCP 已关闭或重新启用，等待重新连接", "app_unavailable",
                            {"delivery_uncertain": False})
        if response.status != 200 or not value.get("ok"):
            raise TaskError(value.get("error", "本机接口请求失败"), value.get("code", "request_failed"),
                            value.get("details"))
        return value
    except (OSError, ValueError, http.client.HTTPException):
        raise TaskError("简截未响应；将使用原 request_id 重试", "app_unavailable",
                        {"delivery_uncertain": sending}) from None
    finally:
        conn.close()


def call(name, arguments, descriptor=None):
    # The descriptor override remains useful for isolated transport diagnostics.
    from .outbox import Outbox
    if name == "jianjie_queue":
        return Outbox().manage(arguments)
    if name in WRITE_TOOLS:
        return Outbox().submit(name, arguments, descriptor=descriptor)
    if name == "jianjie_status":
        from .core import check_keys, today
        check_keys(arguments, ())
        try:
            value = call_online(name, arguments, descriptor)
            value["app_available"] = True
        except TaskError as error:
            if error.code != "app_unavailable":
                raise
            value = {"ok": True, "app_available": False, "today": today(), "timezone": "Asia/Shanghai",
                     "message": "MCP 已连接。简截未就绪；写入可先存入待提交队列，读取任务需启动简截并开启本地 MCP。"}
        value["queue"] = Outbox().counts()
        return value
    return call_online(name, arguments, descriptor)
