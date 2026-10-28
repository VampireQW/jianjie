"""Build the standalone Windows BLE HID helper with the installed system CLR.

No SDK downloads or Python packages are required. Import ``build()`` from the
PyInstaller spec, or run this file with the same Python used to package the app.
This module never starts the helper, modifies settings, or pairs a device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent
SOURCES = ("HidExperiment.cs", "PeerPinning.cs", "WinRtAwait.cs", "NotifyTransport.cs", "experiment.manifest")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(force: bool = False) -> Path:
    """Return native/iphone_control/control.exe, rebuilding if sources changed."""
    output = ROOT / "control.exe"
    manifest_path = ROOT / "manifest.json"
    source_hashes = {name: _sha256(ROOT / name) for name in SOURCES}
    if not force and output.is_file() and manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("sources") == source_hashes and previous.get("sha256") == _sha256(output):
            return output
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    framework = windows / "Microsoft.NET" / "Framework64" / "v4.0.30319"
    compiler = framework / "csc.exe"
    metadata = windows / "System32" / "WinMetadata"
    if not compiler.is_file() or not metadata.is_dir():
        raise RuntimeError("The Windows system .NET Framework compiler and WinMetadata are required.")
    refs = (
        "System.Runtime.dll", "System.Runtime.WindowsRuntime.dll",
        "System.Runtime.InteropServices.WindowsRuntime.dll",
        "System.Threading.Tasks.dll", "System.Web.Extensions.dll",
    )
    args = [str(compiler), "/nologo", "/target:exe", "/platform:x64", "/optimize+",
            "/win32manifest:" + str(ROOT / "experiment.manifest"),
            "/out:" + str(output)]
    args.extend("/r:" + str(framework / ref) for ref in refs)
    args.extend("/r:" + str(path) for path in sorted(metadata.glob("*.winmd")))
    args.extend(str(ROOT / name) for name in SOURCES if name.endswith(".cs"))
    subprocess.run(args, check=True, cwd=ROOT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    manifest = {
        "schemaVersion": 1,
        "component": "iphone-ble-hid",
        "version": "0.1.2-experimental",
        "protocol": "jsonl-v1",
        "executable": output.name,
        "sha256": _sha256(output),
        "bytes": output.stat().st_size,
        "architecture": "x64",
        "runtime": "Windows system .NET Framework CLR v4",
        "minimumWindowsBuild": 15063,
        "experimental": True,
        "sources": source_hashes,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    options = parser.parse_args()
    result = build(force=options.force)
    print(json.dumps({"path": str(result), "bytes": result.stat().st_size, "sha256": _sha256(result)}, ensure_ascii=False))
