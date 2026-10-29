"""独立于 GUI 的本机调用端；未来的模型工具执行器可以复用 call()。"""
import http.client
import json
from .paths import endpoint_file
from .core import TaskError


def call(name, arguments, descriptor=None):
    try:
        info = json.loads((descriptor or endpoint_file()).read_text(encoding="utf-8"))
        port, token = info["port"], info["token"]
        if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str):
            raise ValueError()
    except (OSError, ValueError, KeyError):
        raise TaskError("请启动简截，并在托盘菜单启用本地 MCP", "app_unavailable") from None
    # 地址固定为本机，绕过系统代理；不会读取描述文件中的任意主机地址。
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request("POST", "/v1/tools/call", json.dumps({"name": name, "arguments": arguments},
                     ensure_ascii=False).encode(), {"Authorization": "Bearer " + token,
                     "Content-Type": "application/json", "User-Agent": "JianjieLocalMCP/1"})
        response = conn.getresponse()
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        value = json.loads(raw)
        if response.status != 200 or not value.get("ok"):
            raise TaskError(value.get("error", "本机接口请求失败"), value.get("code", "request_failed"),
                            value.get("details"))
        return value
    except (OSError, ValueError, http.client.HTTPException):
        raise TaskError("简截未响应；请确认已打开且启用了本地 MCP，再用原 request_id 重试", "app_unavailable") from None
    finally:
        conn.close()
