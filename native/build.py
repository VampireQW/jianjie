"""Reproducible native DLL build. Toolchain/source packages remain outside the EXE."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "build" / "native-toolchain"
ZIG = TOOLS / "zig-x86_64-windows-0.15.2" / "zig.exe"
SQLITE = TOOLS / "sqlite-amalgamation-3540000"


def build():
    if not ZIG.is_file() or not (SQLITE / "sqlite3.c").is_file():
        raise RuntimeError("缺少原生编译依赖，请按 native/README.md 准备 Zig 与 SQLite")
    output = ROOT / "native" / "jj_search.dll"
    object_file = ROOT / "build" / "native-sqlite.obj"
    environment = os.environ.copy()
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(TOOLS / "cache")
    environment["ZIG_LOCAL_CACHE_DIR"] = str(ROOT / "build" / "native-zig-cache")
    environment["TEMP"] = str(ROOT / "build" / "native-temp")
    environment["TMP"] = environment["TEMP"]
    Path(environment["TEMP"]).mkdir(parents=True, exist_ok=True)
    stamp_path = ROOT / "build" / "native-sqlite-stamp.json"
    source_hash = hashlib.sha256((SQLITE / "sqlite3.c").read_bytes()).hexdigest()
    defines = ["-DSQLITE_ENABLE_FTS5", "-DSQLITE_THREADSAFE=1", "-DSQLITE_DEFAULT_MEMSTATUS=0",
               "-DSQLITE_OMIT_LOAD_EXTENSION", "-DSQLITE_DQS=0", "-DSQLITE_TEMP_STORE=1"]
    stamp = {"sqlite": source_hash, "zig": "0.15.2", "flags": defines, "debug": False}
    previous = json.loads(stamp_path.read_text()) if stamp_path.is_file() else {}
    if not object_file.is_file() or previous != stamp:
        subprocess.run([str(ZIG), "cc", "-target", "x86_64-windows-gnu", "-O2", "-g0", "-DNDEBUG",
                        *defines, "-c", str(SQLITE / "sqlite3.c"), "-o", str(object_file)],
                       check=True, env=environment, cwd=ROOT)
        stamp_path.write_text(json.dumps(stamp), encoding="utf-8")
    subprocess.run([str(ZIG), "c++", "-target", "x86_64-windows-gnu", "-std=c++17", "-O2", "-g0",
                    "-DNDEBUG", "-shared", "-s", "-I", str(SQLITE),
                    str(ROOT / "native" / "search_core.cpp"), str(object_file),
                    "-o", str(output)], check=True, env=environment, cwd=ROOT)
    if output.read_bytes()[:2] != b"MZ":
        raise RuntimeError("原生搜索输出不是 Windows DLL")
    metadata = {"version": 1, "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "bytes": output.stat().st_size,
                "sourceSha256": hashlib.sha256((ROOT / "native" / "search_core.cpp").read_bytes()).hexdigest(),
                "sqliteSourceSha256": source_hash, "compiler": "Zig 0.15.2 / Clang"}
    (ROOT / "build" / "native-search-build.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print("Native search DLL: {} bytes".format(metadata["bytes"]), flush=True)
    return str(output)


if __name__ == "__main__":
    build()
