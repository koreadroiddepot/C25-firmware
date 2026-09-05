"""Offline tests: python3 -m unittest discover -s tests -v.

Execute the workflow's actual embedded Python without calling GitHub.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import textwrap
import unittest
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/c25-release.yml"
SOURCE = textwrap.dedent(WORKFLOW.read_text().split("python3 - <<'PY'\n", 1)[1].rsplit("          PY", 1)[0])
CODE = compile(SOURCE, str(WORKFLOW), "exec")


def image(payload=b"test", segments=1):
    header = bytearray(24)
    header[0], header[1], header[23] = 0xE9, segments, 1
    result = header
    checksum = 0xEF
    for _ in range(segments):
        result += struct.pack("<II", 0x3C000020, len(payload)) + payload
        for value in payload:
            checksum ^= value
    result += bytes(15 - len(result) % 16) + bytes([checksum])
    return bytes(result) + hashlib.sha256(result).digest()


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.ns = {"__name__": "test_workflow"}
        exec(CODE, self.ns)

    def test_valid_images_and_distinct_hashes(self):
        for length in range(32):
            for segments in (1, 2, 16):
                data = image(bytes(range(length)), segments)
                whole, embedded = self.ns["validate_image"](data)
                self.assertEqual(whole, hashlib.sha256(data).hexdigest())
                self.assertEqual(embedded, data[-32:].hex())
                self.assertNotEqual(whole, embedded)

    def test_size_boundary(self):
        data = image(bytes(1_769_472 - 80))
        self.assertEqual(len(data), 1_769_472)
        self.ns["validate_image"](data)
        with self.assertRaisesRegex(ValueError, "APP image"):
            self.ns["validate_image"](data + b"x")

    def test_corruption_and_truncation(self):
        valid = image()
        cases = [b"", valid[:-1], valid + b"x", valid[:-32]]
        for offset in (0, 1, 23, 28, 32, 36, 47, len(valid) - 1):
            bad = bytearray(valid)
            bad[offset] ^= 0xFF
            cases.append(bytes(bad))
        # Even a recomputed SHA cannot hide a wrong segment checksum.
        bad = bytearray(valid[:-32])
        bad[47] ^= 1
        cases.append(bytes(bad) + hashlib.sha256(bad).digest())
        for data in cases:
            with self.subTest(data=data[:24]), self.assertRaises(ValueError):
                self.ns["validate_image"](data)

    def run_release(self, publish=False, draft=True, duplicate=False,
                    existing=None, corrupt=False, changed=False, post_corrupt=False):
        data = image()
        inputs = dict(version="0.11.0", minimum_installed_version="0.10.0",
                      hardware_revision="C25-REV-A", storage_layout="C25-AUDIO512-V1",
                      release_notes='Korean 한국어\n"quotes" $(touch bad)',
                      mandatory=False, publish=publish)
        release = dict(id=1, tag_name="v0.11.0", draft=draft, prerelease=False,
                       upload_url="https://uploads.github.com/repos/test/repo/releases/1/assets{?name,label}")
        assets = [dict(id=2, name="c25-firmware.bin", state="uploaded", size=len(data),
                       browser_download_url="https://github.com/test/repo/releases/download/v0.11.0/c25-firmware.bin")]
        if duplicate:
            assets.append(dict(assets[0], id=3))
        contents = {2: data[:-1] + b"x" if corrupt else data}
        if existing is not None:
            assets.append(dict(id=4, name="latest.json", state="uploaded", size=len(existing)))
            contents[4] = existing
        self.mutations = []
        downloads = 0

        def api(path, method="GET", payload=None, binary=False, upload=None):
            nonlocal downloads
            if method != "GET":
                self.mutations.append(method)
            if method == "POST":
                self.assertTrue(release["draft"])
                contents[4] = Path(upload).read_bytes()
                assets.append(dict(id=4, name="latest.json", state="uploaded", size=len(contents[4])))
                return assets[-1]
            if method == "PATCH":
                self.assertTrue(release["draft"])
                self.assertEqual(payload, {"draft": False, "make_latest": "true"})
                release["draft"] = False
                return release
            if binary:
                asset_id = int(path.rsplit("/", 1)[1])
                if asset_id == 2:
                    downloads += 1
                    if changed and downloads > 1 or post_corrupt and not release["draft"]:
                        return data[:-1] + b"x"
                return contents[asset_id]
            if "/assets?" in path:
                return assets
            if "?" in path:
                return [release]
            return release

        self.ns["api"] = api
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                with patch("time.sleep"), patch.dict(os.environ, INPUTS_JSON=json.dumps(inputs),
                                GITHUB_REPOSITORY="test/repo", GITHUB_STEP_SUMMARY="summary.md"):
                    self.ns["main"]()
                return json.loads(Path("latest.json").read_text(encoding="utf-8"))
            finally:
                os.chdir(previous)

    def test_dry_run_is_read_only(self):
        metadata = self.run_release()
        self.assertEqual(self.mutations, [])
        self.assertEqual(metadata["product"], "C25")
        self.assertEqual(metadata["hardware"], ["C25-REV-A"])
        self.assertIs(metadata["mandatory"], False)
        self.assertIn("한국어", metadata["notes"])

    def test_publish_uploads_then_publishes(self):
        self.run_release(publish=True)
        self.assertEqual(self.mutations, ["POST", "PATCH"])

    def test_identical_metadata_retry_does_not_upload_again(self):
        metadata = self.run_release()
        existing = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
        self.run_release(publish=True, existing=existing)
        self.assertEqual(self.mutations, ["PATCH"])

    def test_pagination(self):
        calls = []

        def api(path):
            calls.append(path)
            return list(range(100)) if path.endswith("&page=1") else [100]

        self.ns["api"] = api
        self.assertEqual(self.ns["pages"]("releases"), list(range(101)))
        self.assertEqual(len(calls), 2)

    def test_guards_prevent_writes(self):
        for case in (dict(draft=False), dict(duplicate=True), dict(corrupt=True),
                     dict(changed=True), dict(existing=b"{}")):
            with self.subTest(case=case), self.assertRaises(ValueError):
                self.run_release(publish=True, **case)
            self.assertEqual(self.mutations, [])

    def test_post_publish_corruption_fails_without_rollback(self):
        with self.assertRaisesRegex(ValueError, "Firmware changed"):
            self.run_release(publish=True, post_corrupt=True)
        self.assertEqual(self.mutations, ["POST", "PATCH"])

    def test_version_validation(self):
        for version in ("v1.2.3", "1.2", "01.2.3", "1.2.3\n", "1.2.3;echo bad"):
            with self.assertRaises(ValueError):
                self.ns["version_tuple"](version)
        self.assertGreater(self.ns["version_tuple"]("0.10.0"), self.ns["version_tuple"]("0.9.9"))


if __name__ == "__main__":
    unittest.main()
