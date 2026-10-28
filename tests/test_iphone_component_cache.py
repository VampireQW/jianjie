import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import hashlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import iphone_mirror as mirror


class ReceiverCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "build")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        assert self.base.is_relative_to(Path(__file__).resolve().parents[1] / "build")
        self.bundle = self.base / "bundle"
        self.bundle.mkdir()
        self.root = self.base / "installed"
        self.root.mkdir()
        self.engine = b"synthetic engine for integrity tests"
        self.meta = {"engineSha256": hashlib.sha256(self.engine).hexdigest(),
                     "engineBytes": len(self.engine)}
        self.addCleanup(patch.stopall)
        patch("iphone_mirror.bundled_dir", return_value=self.bundle).start()

    def test_verified_installed_engine_never_accesses_missing_bundle(self):
        (self.root / "receiver.exe").write_bytes(self.engine)
        with patch("iphone_mirror.bundled_dir", side_effect=AssertionError("bundle must not be consulted")):
            self.assertEqual(mirror.ensure_receiver(self.root, self.meta), self.root / "receiver.exe")

    def test_cached_session_prepares_without_reading_bundle_engine(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        (self.root / "receiver.exe").write_bytes(self.engine)
        runtime = self.root / "runtime.dll"
        runtime.write_bytes(b"runtime")
        self.meta["runtimeSha256"] = "a" * 64
        (self.root / "ready.json").write_text(json.dumps({"runtimeSha256": self.meta["runtimeSha256"], "files": {"runtime.dll": 7}}))
        for name in ("manifest.json", "source.zip", "LICENSE"):
            (self.bundle / name).write_text("metadata", encoding="utf-8")
        class InlineThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        session = mirror.MirrorSession()
        with patch("iphone_mirror.component_dir", return_value=self.root), patch("iphone_mirror.metadata", return_value=self.meta), patch("iphone_mirror.threading.Thread", InlineThread), patch.object(session, "_launch") as launch:
            session.start("Synthetic receiver")
            app.processEvents()
            launch.assert_called_once_with(self.root, 1)
        self.assertFalse(session._preparing)

    def test_corrupt_installed_engine_cannot_be_reused_without_bundle(self):
        target = self.root / "receiver.exe"
        target.write_bytes(b"x" * len(self.engine))
        with self.assertRaises(FileNotFoundError):
            mirror.ensure_receiver(self.root, self.meta)
        self.assertEqual(target.read_bytes(), b"x" * len(self.engine))

    def test_missing_installed_engine_is_installed_from_verified_bundle(self):
        (self.bundle / "receiver.exe").write_bytes(self.engine)
        mirror.ensure_receiver(self.root, self.meta)
        self.assertEqual((self.root / "receiver.exe").read_bytes(), self.engine)
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_bad_bundle_is_rejected_without_replacing_installed_file(self):
        target = self.root / "receiver.exe"
        target.write_bytes(b"old")
        (self.bundle / "receiver.exe").write_bytes(b"x" * len(self.engine))
        with self.assertRaisesRegex(ValueError, "校验失败"):
            mirror.ensure_receiver(self.root, self.meta)
        self.assertEqual(target.read_bytes(), b"old")

    def test_changed_bundle_during_copy_is_rejected(self):
        (self.bundle / "receiver.exe").write_bytes(self.engine)
        def corrupt_copy(origin, destination):
            Path(destination).write_bytes(b"x" * len(self.engine))
        with patch("iphone_mirror.shutil.copyfile", side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, "校验失败"):
                mirror.ensure_receiver(self.root, self.meta)
        self.assertFalse((self.root / "receiver.exe").exists())
        self.assertEqual(list(self.root.glob("*.pending")), [])

    def test_invalid_manifest_does_not_trust_installed_engine(self):
        (self.root / "receiver.exe").write_bytes(self.engine)
        for invalid in ({}, {"engineSha256": "x" * 64, "engineBytes": 12},
                        {"engineSha256": self.meta["engineSha256"], "engineBytes": 0}):
            with self.assertRaisesRegex(ValueError, "版本信息"):
                mirror.ensure_receiver(self.root, invalid)

    def test_runtime_install_reuses_verified_engine_when_bundle_engine_is_missing(self):
        (self.root / "receiver.exe").write_bytes(self.engine)
        for name in ("LICENSE", "source.zip", "manifest.json"):
            (self.bundle / name).write_text("synthetic metadata", encoding="utf-8")
        package = self.base / "runtime.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("libgstreamer-1.0-0.dll", b"synthetic runtime")
            archive.writestr("libplist-2.0.dll", b"synthetic runtime")
        self.meta.update(runtimeBytes=package.stat().st_size,
                         runtimeSha256=hashlib.sha256(package.read_bytes()).hexdigest())
        with patch("iphone_mirror.component_dir", return_value=self.root), patch("iphone_mirror.metadata", return_value=self.meta):
            self.assertEqual(mirror.install_runtime(package), self.root)
        self.assertEqual((self.root / "receiver.exe").read_bytes(), self.engine)
        self.assertTrue((self.root / "ready.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
