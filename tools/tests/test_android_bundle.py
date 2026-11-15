"""Local security regressions using the observed build-203982 XAPK layout."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import android_bundle as bundle


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.archive, self.output = self.root / "game.xapk", self.root / "extracted"
        self.apk, self.obb = b"APK_PAYLOAD_UNIQUE" * 3, bytes(range(256)) * 8

    def manifest(self):
        # Actual manifest has these fields, an absent icon.png reference, and no
        # per-file hashes. Payloads are tiny test data, not installable APK/OBB.
        return {"xapk_version": 2, "package_name": bundle.PACKAGE_NAME,
                "version_code": "203982", "version_name": "1.1.0", "icon": "icon.png",
                "total_size": len(self.apk) + len(self.obb),
                "split_apks": [{"file": bundle.APK_MEMBER, "id": "base"}],
                "expansions": [{"file": bundle.OBB_MEMBER, "install_path": bundle.OBB_MEMBER,
                                "install_location": "EXTERNAL_STORAGE"}]}

    def write(self, manifest=None, extra=(), compression=zipfile.ZIP_STORED, raw_manifest=None):
        with warnings.catch_warnings(), zipfile.ZipFile(self.archive, "w", compression=compression) as archive:
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr(bundle.APK_MEMBER, self.apk)
            archive.writestr(bundle.OBB_MEMBER, self.obb)
            archive.writestr("manifest.json", raw_manifest if raw_manifest is not None else
                             json.dumps(manifest if manifest is not None else self.manifest()).encode())
            for name, data in extra:
                archive.writestr(name, data)

    def extract(self, **kwargs):
        return bundle.extract_xapk(self.archive, self.output, **kwargs)

    def reject(self, **kwargs):
        expected_error = kwargs.pop("error", None)
        with self.assertRaises(bundle.BundleError) as caught:
            self.extract(**kwargs)
        if expected_error:
            self.assertEqual(str(caught.exception), expected_error)
        self.assertTrue(not self.output.exists() or not list(self.output.iterdir()))
        self.assertTrue(self.archive.is_file())

    def test_safe_real_layout_streams_three_files_and_returns_hashes(self):
        self.write()
        result = self.extract(expected_apk_sha256=hashlib.sha256(self.apk).hexdigest())
        self.assertEqual(result.apk_path.read_bytes(), self.apk)
        self.assertEqual(result.obb_path.read_bytes(), self.obb)
        self.assertEqual(set(result.metadata["files"]), {"manifest.json", bundle.APK_MEMBER, bundle.OBB_MEMBER})
        self.assertEqual(result.metadata["format"], "xapk_v2")
        self.assertEqual(result.metadata["version_code"], "203982")
        for name, payload in ((bundle.APK_MEMBER, self.apk), (bundle.OBB_MEMBER, self.obb)):
            self.assertEqual(result.metadata["files"][name]["bytes"], len(payload))
            self.assertEqual(result.metadata["files"][name]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertFalse(result.metadata["apk_publisher_signature_verified"])
        self.assertFalse(result.metadata["apk_manifest_and_abi_verified"])
        self.assertNotIn(str(self.root), json.dumps(result.metadata))
        if os.name == "posix":
            self.assertEqual(result.apk_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)

    def test_optional_icon_is_bounded_and_not_extracted(self):
        self.write(extra=[("icon.png", b"not executed or interpreted")])
        self.extract()
        self.assertFalse((self.output / "icon.png").exists())

    def test_observed_stored_members_with_data_descriptors_are_supported(self):
        class StreamingOutput(io.BytesIO):
            def seek(self, *args):
                raise io.UnsupportedOperation("unseekable test writer")
        output = StreamingOutput()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, payload in ((bundle.APK_MEMBER, self.apk), (bundle.OBB_MEMBER, self.obb),
                                  ("manifest.json", json.dumps(self.manifest()).encode())):
                archive.writestr(name, payload)
        self.archive.write_bytes(output.getvalue())
        with zipfile.ZipFile(self.archive) as archive:
            self.assertTrue(all(item.flag_bits & 8 for item in archive.infolist()))
        self.assertEqual(self.extract().obb_path.read_bytes(), self.obb)

    def test_traversal_absolute_backslash_and_control_names_are_rejected(self):
        for name in ("../outside", "/outside", "C:/outside", "Android/../outside",
                     "Android//outside", "Android\\outside", "evil\nname", "./manifest.json"):
            with self.subTest(name=name):
                self.write(extra=[(name, b"x")])
                if "\\" in name:
                    # Windows ZipInfo normalizes backslashes when writing, so
                    # put the hostile separator into both actual ZIP headers.
                    self.archive.write_bytes(self.archive.read_bytes().replace(
                        name.replace("\\", "/").encode(), name.encode()))
                self.reject(error="unsafe_member_path")
        self.assertFalse((self.root / "outside").exists())

    def test_nul_in_actual_zip_filename_is_rejected(self):
        self.write(extra=[("evil", b"x")])
        self.archive.write_bytes(self.archive.read_bytes().replace(b"evil", b"ev\x00l"))
        self.reject(error="unsafe_member_path")

    def test_duplicate_and_case_colliding_members_are_rejected(self):
        for name in (bundle.APK_MEMBER, bundle.APK_MEMBER.upper()):
            with self.subTest(name=name):
                self.write(extra=[(name, b"unexpected")])
                self.reject(error="duplicate_member")

    def test_symlink_and_unknown_files_are_rejected(self):
        link = zipfile.ZipInfo("icon.png")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra, error in (([(link, b"/etc/passwd")], "unsafe_member_type"),
                             ([("other-package.apk", b"x")], "unexpected_member")):
            self.write(extra=extra)
            self.reject(error=error)

    def test_manifest_package_build_and_version_are_fixed(self):
        changes = {"package_name": "other.package", "version_code": "203983",
                   "version_name": "0.38.2", "xapk_version": 2.0, "total_size": 1}
        for key, value in changes.items():
            with self.subTest(field=key):
                document = self.manifest()
                document[key] = value
                self.write(manifest=document)
                self.reject()

    def test_manifest_file_mapping_cannot_redirect_extraction_or_install_path(self):
        for key, value in (("file", "other.obb"), ("install_path", "../../elsewhere"),
                           ("install_location", "INTERNAL_STORAGE")):
            document = self.manifest()
            document["expansions"][0][key] = value
            self.write(manifest=document)
            self.reject()
        document = self.manifest()
        document["split_apks"].append({"file": "split_config.apk", "id": "other"})
        self.write(manifest=document)
        self.reject()

    def test_declared_sizes_and_optional_md5_are_checked(self):
        document = self.manifest()
        document["split_apks"][0].update(size=len(self.apk), md5=hashlib.md5(self.apk).hexdigest().upper())
        document["expansions"][0].update(size=len(self.obb), md5=hashlib.md5(self.obb).hexdigest())
        self.write(manifest=document)
        self.extract()
        with tempfile.TemporaryDirectory() as second:
            document["split_apks"][0]["size"] += 1
            self.write(manifest=document)
            with self.assertRaises(bundle.BundleError):
                bundle.extract_xapk(self.archive, Path(second) / "out")

    def test_wrong_md5_and_apk_sha256_roll_back_only_owned_files(self):
        self.output.mkdir()
        document = self.manifest()
        document["split_apks"][0]["md5"] = "0" * 32
        self.write(manifest=document)
        self.reject(error="declared_md5_mismatch")
        self.write()
        self.reject(expected_apk_sha256="0" * 64, error="apk_sha256_mismatch")

    def test_corrupt_crc_rolls_back_extraction(self):
        self.write()
        self.archive.write_bytes(self.archive.read_bytes().replace(b"APK_PAYLOAD_UNIQUE", b"BAD_PAYLOAD_UNIQUE", 1))
        self.reject(error="invalid_bundle")

    def test_compression_bomb_and_aggregate_budget_are_rejected(self):
        self.obb = b"\x00" * 20000
        self.write(compression=zipfile.ZIP_DEFLATED)
        self.reject(error="compression_ratio_exceeded")
        self.apk, self.obb = b"a" * 2000, b"b" * 2000
        self.write()
        self.reject(max_total_bytes=4100, error="total_size_exceeded")

    def test_huge_declared_member_is_rejected_without_decompression(self):
        self.write()
        data = bytearray(self.archive.read_bytes())
        cursor = 0
        while True:
            cursor = data.find(b"PK\x01\x02", cursor)
            if cursor < 0:
                self.fail("test central member missing")
            length = struct.unpack_from("<H", data, cursor + 28)[0]
            if data[cursor + 46:cursor + 46 + length] == bundle.OBB_MEMBER.encode():
                struct.pack_into("<L", data, cursor + 24, bundle.MAX_TOTAL_BYTES + 1)
                break
            cursor += 46
        self.archive.write_bytes(data)
        self.reject(error="member_size_exceeded")

    def test_central_entry_limit_is_enforced_before_zipfile_parses_it(self):
        self.write()
        data = bytearray(self.archive.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        struct.pack_into("<HH", data, end + 8, 65535, 65535)
        self.archive.write_bytes(data)
        with patch.object(bundle.zipfile, "ZipFile", side_effect=AssertionError("must not parse central directory")):
            self.reject(error="entry_count_exceeded")

    def test_existing_files_are_never_overwritten(self):
        self.write()
        self.output.mkdir()
        existing = self.output / "keep.txt"
        existing.write_bytes(b"keep")
        with self.assertRaises(bundle.BundleError):
            self.extract()
        self.assertEqual(existing.read_bytes(), b"keep")
        self.assertEqual(list(self.output.iterdir()), [existing])

    def test_destination_symlink_is_rejected(self):
        self.write()
        actual = self.root / "actual"
        actual.mkdir()
        try:
            self.output.symlink_to(actual, target_is_directory=True)
        except OSError:
            self.skipTest("creating symlinks is unavailable for this account")
        with self.assertRaises(bundle.BundleError):
            self.extract()
        self.assertFalse(list(actual.iterdir()))

    def test_duplicate_json_keys_and_oversized_manifest_are_rejected(self):
        self.write(raw_manifest=b'{"xapk_version":2,"xapk_version":2}')
        self.reject(error="duplicate_manifest_key")
        self.write(raw_manifest=b" " * (bundle.MAX_MANIFEST_BYTES + 1))
        self.reject(error="manifest_size_exceeded")

    def test_missing_obb_cannot_be_replaced_by_a_manifest_claim(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("manifest.json", json.dumps(self.manifest()))
            archive.writestr(bundle.APK_MEMBER, self.apk)
        self.reject(error="required_member_missing")


if __name__ == "__main__":
    unittest.main()
