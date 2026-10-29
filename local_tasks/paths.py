import os
from pathlib import Path


def home():
    return Path(os.environ.get("JJ_MCP_HOME") or
                Path(os.environ.get("APPDATA", Path.home())) / "jianjie")


def endpoint_file():
    return home() / "agent-endpoint.json"
