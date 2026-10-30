"""MCP stdio / JSON-RPC 2.0。标准输出只承载逐行 JSON 协议。"""
import json
import sys
from .client import call
from .core import TaskError
from .tools import TOOLS

VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def serve(input_stream=None, output_stream=None, invoke=call):
    incoming, outgoing = input_stream or sys.stdin.buffer, output_stream or sys.stdout.buffer
    initialized = False

    def send(value):
        outgoing.write((json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"))
        outgoing.flush()

    while True:
        raw = incoming.readline(131073)
        if not raw:
            return
        request_id = None
        try:
            if len(raw) > 131072:
                raise ValueError("message too large")
            msg = json.loads(raw)
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
                continue
            request_id = msg.get("id")
            if "id" not in msg:
                continue
            method, params = msg["method"], msg.get("params", {})
            if not isinstance(params, dict):
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "Invalid params"}})
                continue
            if method == "initialize":
                requested = params.get("protocolVersion")
                result = {"protocolVersion": requested if requested in VERSIONS else VERSIONS[0],
                          "serverInfo": {"name": "jianjie-local-tasks", "version": "1.1.0"},
                          "capabilities": {"tools": {}},
                          "instructions": "仅管理简截本机任务。拆解由 Agent 完成。新增默认待办，明确今天才入今日；修改先查询真实 ID/version。写入离线时会保存本机队列，简截启动并开启本地 MCP 后自动提交。delivery_status=queued 只代表等待提交，必须明确告知用户尚未写入清单；用 jianjie_queue 查询结果。仅 applied 才报告已添加或已修改。冲突不得自动覆盖；重试复用原 request_id。后台提交不会自行触发 Agent 发言。"}
                initialized = True
            elif method == "ping":
                result = {}
            elif not initialized:
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Initialize first"}})
                continue
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                try:
                    if params.get("name") not in {t["name"] for t in TOOLS}:
                        raise TaskError("未知任务工具", "unknown_tool")
                    arguments = params.get("arguments", {})
                    if not isinstance(arguments, dict):
                        raise TaskError("arguments 须为对象")
                    value = invoke(params["name"], arguments)
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                              "structuredContent": value, "isError": False}
                except TaskError as error:
                    value = {"ok": False, "code": error.code, "error": str(error)}
                    if error.details is not None:
                        value["details"] = error.details
                    result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                              "structuredContent": value, "isError": True}
            else:
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
                continue
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ValueError, UnicodeError):
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            if len(raw) > 131072:
                return
        except Exception:
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal error"}})


def main():
    from .stdio import prepare_stdio
    try:
        prepare_stdio()
    except OSError:
        return 1
    if "--call" in sys.argv:
        index = sys.argv.index("--call")
        try:
            value = call(sys.argv[index + 1], json.loads(sys.argv[index + 2] if len(sys.argv) > index + 2 else "{}"))
        except (TaskError, ValueError, IndexError) as error:
            value = {"ok": False, "code": getattr(error, "code", "invalid_arguments"), "error": str(error)}
        print(json.dumps(value, ensure_ascii=False))
        return 0 if value.get("ok") else 1
    from .outbox import OutboxWorker
    worker = OutboxWorker().start()
    try:
        serve()
    finally:
        worker.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
