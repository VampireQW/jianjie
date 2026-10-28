from pathlib import Path
import hashlib
import json
import runpy
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = runpy.run_path(str(ROOT / "native/airplay/package.py"))


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.base = Path(self.temp.name).resolve()
        assert self.base.is_relative_to(ROOT / "build")
        self.addCleanup(self.temp.cleanup)
        self.native = self.base / "native/airplay"
        self.native.mkdir(parents=True)
        (self.base / "build").mkdir()
        (self.base / "_app.ico").write_bytes(b"synthetic icon")
        self.receiver = self.base / "receiver.exe"
        self.receiver.write_bytes(b"synthetic prebuilt artifact")
        meta = {"engineSha256": hashlib.sha256(self.receiver.read_bytes()).hexdigest(), "engineBytes": self.receiver.stat().st_size, "commit": "pinned-commit"}
        (self.native / "manifest.json").write_text(json.dumps(meta))
        with zipfile.ZipFile(self.native / "source.zip", "w") as archive:
            for name in PACKAGE["NATIVE_INPUTS"]:
                data = ("synthetic input " + name).encode()
                (self.native / name).write_bytes(data)
                archive.writestr("native/airplay/" + name, data)
            archive.writestr("_app.ico", b"synthetic icon")

    def test_matching_artifact_and_source_can_be_explicitly_reused(self):
        with patch("subprocess.check_output", side_effect=["pinned-commit", "", ""]):
            files = PACKAGE["package_data"](self.base, self.receiver)
        self.assertEqual(files[0], (str(self.receiver), "native/airplay"))
        self.assertTrue((self.base / "build/airplay-package-provenance.json").is_file())

    def test_modified_binary_is_rejected(self):
        self.receiver.write_bytes(b"x" * self.receiver.stat().st_size)
        with self.assertRaisesRegex(ValueError, "digest"):
            PACKAGE["package_data"](self.base, self.receiver)

    def test_changed_native_input_requires_rebuild(self):
        (self.native / "windows_video.py").write_text("changed source")
        with patch("subprocess.check_output", side_effect=["pinned-commit", "", ""]):
            with self.assertRaisesRegex(ValueError, "source changed"):
                PACKAGE["package_data"](self.base, self.receiver)

    def test_dirty_upstream_tree_is_rejected(self):
        with patch("subprocess.check_output", side_effect=["pinned-commit", " M uxplay.cpp"]):
            with self.assertRaisesRegex(ValueError, "clean pinned"):
                PACKAGE["package_data"](self.base, self.receiver)


if __name__ == "__main__":
    unittest.main(verbosity=2)
