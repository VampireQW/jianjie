"""Package freshly built or explicitly selected, source-matched AirPlay artifacts."""
from pathlib import Path
import hashlib
import json
import runpy
import subprocess
import zipfile

NATIVE_INPUTS = ("build.py", "windows_video.py", "windows_discovery.py", "video_stats.c", "display_latency.c", "dependencies.json")
DATA_FILES = ("manifest.json", "LICENSE", "source.zip", "configure_firewall.ps1")


def package_data(project_root, prebuilt_receiver=None):
    root = Path(project_root)
    native = root / "native/airplay"
    if not prebuilt_receiver:
        runpy.run_path(str(native / "build.py"))["build"]()
        receiver = native / "receiver.exe"
    else:
        receiver = Path(prebuilt_receiver).resolve(strict=True)
        meta = json.loads((native / "manifest.json").read_text(encoding="utf-8"))
        if receiver.stat().st_size != meta["engineBytes"]:
            raise ValueError("Prebuilt AirPlay receiver size does not match the release manifest")
        with receiver.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != meta["engineSha256"]:
            raise ValueError("Prebuilt AirPlay receiver digest does not match the release manifest")
        upstream = root / "build/airplay-research/UxPlay"
        def git(*args):
            return subprocess.check_output(["git", "-C", str(upstream), *args], text=True).strip()
        if git("rev-parse", "HEAD") != meta["commit"]:
            raise ValueError("Prebuilt AirPlay upstream revision differs from the current source")
        if git("status", "--porcelain", "--untracked-files=all") or git("ls-files", "--others", "--ignored", "--exclude-standard"):
            raise ValueError("Prebuilt AirPlay requires a clean pinned upstream tree")
        with zipfile.ZipFile(native / "source.zip") as archive:
            for name in NATIVE_INPUTS:
                if archive.read("native/airplay/" + name) != (native / name).read_bytes():
                    raise ValueError("Prebuilt AirPlay source changed: " + name)
            if archive.read("_app.ico") != (root / "_app.ico").read_bytes():
                raise ValueError("Prebuilt AirPlay icon differs from the current build")
        proof = dict(receiverSha256=actual, receiverBytes=receiver.stat().st_size,
                     sourceCommit=meta["commit"], nativeInputsMatchSourceArchive=True,
                     explicitPrebuilt=True)
        (root / "build/airplay-package-provenance.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        print("Reusing explicit AirPlay artifact with matching digest and native sources", flush=True)
    return [(str(receiver), "native/airplay")] + [(str(native / name), "native/airplay") for name in DATA_FILES]
