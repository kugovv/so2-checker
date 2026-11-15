"""Bounded local extraction of the observed Standoff 2 build 203982 XAPK.

The caller must verify the outer archive SHA-256 before extraction, and verify
the APK publisher signature, Android manifest and ABI before installation.
This module performs no networking, shell commands or Android operations.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zipfile

PACKAGE_NAME = "com.axlebolt.standoff2"
VERSION_CODE = "203982"
VERSION_NAME = "1.1.0"
APK_MEMBER = PACKAGE_NAME + ".apk"
OBB_MEMBER = f"Android/obb/{PACKAGE_NAME}/main.{VERSION_CODE}.{PACKAGE_NAME}.obb"
EXPECTED_XAPK_SHA256 = "2c587b32be2e9b391902bf167526f07c177c72b4536839a9c38348815926078a"
MAX_TOTAL_BYTES = 3 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ICON_BYTES = 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAX_COMPRESSION_RATIO = 200
ALLOWED_FILES = frozenset({"manifest.json", APK_MEMBER, OBB_MEMBER, "icon.png"})
ALLOWED_DIRECTORIES = frozenset({"Android", "Android/obb", f"Android/obb/{PACKAGE_NAME}"})


class BundleError(ValueError):
    """A bounded error code; archive-controlled strings are never included."""


@dataclass(frozen=True)
class ExtractedBundle:
    apk_path: Path
    obb_path: Path
    metadata: dict


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _check_directory(stream, max_entries):
    """Bound ZIP metadata before ZipFile allocates its central-directory objects."""
    size = os.fstat(stream.fileno()).st_size
    if not 22 <= size <= MAX_TOTAL_BYTES:
        raise BundleError("archive_size_exceeded")
    stream.seek(max(0, size - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or offset + 22 > len(tail):
        raise BundleError("invalid_zip_directory")
    _, disk, central_disk, disk_count, count, length, start, comment = struct.unpack_from("<4s4H2LH", tail, offset)
    if offset + 22 + comment != len(tail) or disk or central_disk or disk_count != count:
        raise BundleError("unsupported_zip_layout")
    if not 0 < count <= max_entries:
        raise BundleError("entry_count_exceeded")
    if length > 128 * 1024 or start + length != size - len(tail) + offset:
        raise BundleError("invalid_zip_directory")
    stream.seek(0)


def _inventory(archive, maximum, max_entries):
    entries = archive.infolist()
    if not entries or len(entries) > max_entries:
        raise BundleError("entry_count_exceeded")
    files, seen, total = {}, set(), 0
    for entry in entries:
        name = entry.orig_filename
        normalized = name[:-1] if name.endswith("/") else name
        parts = normalized.split("/")
        if (name != entry.filename or len(name) > 512 or "\\" in name or ":" in name
                or name.startswith("/") or any(ord(c) < 32 or ord(c) == 127 for c in name)
                or any(part in {"", ".", ".."} for part in parts)
                or PurePosixPath(normalized).as_posix() != normalized):
            raise BundleError("unsafe_member_path")
        folded = normalized.casefold()
        if folded in seen:
            raise BundleError("duplicate_member")
        seen.add(folded)
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or stat.S_ISLNK(mode):
            raise BundleError("unsafe_member_type")
        if entry.flag_bits & 1 or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise BundleError("unsupported_member_encoding")
        if entry.is_dir():
            if normalized not in ALLOWED_DIRECTORIES or entry.file_size:
                raise BundleError("unexpected_directory")
            continue
        if kind == stat.S_IFDIR or name not in ALLOWED_FILES:
            raise BundleError("unexpected_member")
        if not _positive_int(entry.file_size) or entry.file_size > maximum:
            raise BundleError("member_size_exceeded")
        if entry.file_size > max(1, entry.compress_size) * MAX_COMPRESSION_RATIO:
            raise BundleError("compression_ratio_exceeded")
        total += entry.file_size
        if total > maximum:
            raise BundleError("total_size_exceeded")
        files[name] = entry
    if not {"manifest.json", APK_MEMBER, OBB_MEMBER}.issubset(files):
        raise BundleError("required_member_missing")
    if files["manifest.json"].file_size > MAX_MANIFEST_BYTES:
        raise BundleError("manifest_size_exceeded")
    if "icon.png" in files and files["icon.png"].file_size > MAX_ICON_BYTES:
        raise BundleError("icon_size_exceeded")
    return files


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("duplicate_manifest_key")
        result[key] = value
    return result


def _manifest(archive, files):
    with archive.open(files["manifest.json"]) as stream:
        raw = stream.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BundleError("manifest_size_exceeded")
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_json_object)
    if (not isinstance(document, dict) or type(document.get("xapk_version")) is not int
            or document["xapk_version"] != 2):
        raise BundleError("unsupported_manifest")
    if (document.get("package_name") != PACKAGE_NAME
            or str(document.get("version_code")) != VERSION_CODE
            or document.get("version_name") != VERSION_NAME):
        raise BundleError("manifest_identity_mismatch")
    apks, expansions = document.get("split_apks"), document.get("expansions")
    if (not isinstance(apks, list) or len(apks) != 1 or not isinstance(apks[0], dict)
            or apks[0].get("file") != APK_MEMBER or apks[0].get("id") != "base"
            or not isinstance(expansions, list) or len(expansions) != 1
            or not isinstance(expansions[0], dict) or expansions[0].get("file") != OBB_MEMBER
            or expansions[0].get("install_path") != OBB_MEMBER
            or expansions[0].get("install_location") != "EXTERNAL_STORAGE"):
        raise BundleError("manifest_file_mapping_mismatch")
    if document.get("icon") is not None and document["icon"] != "icon.png":
        raise BundleError("unexpected_icon_reference")
    expected_total = files[APK_MEMBER].file_size + files[OBB_MEMBER].file_size
    if type(document.get("total_size")) is not int or document["total_size"] != expected_total:
        raise BundleError("manifest_total_size_mismatch")
    declarations = {APK_MEMBER: apks[0], OBB_MEMBER: expansions[0]}
    for name, declaration in declarations.items():
        if "size" in declaration and (type(declaration["size"]) is not int
                                      or declaration["size"] != files[name].file_size):
            raise BundleError("declared_file_size_mismatch")
        if "md5" in declaration and (not isinstance(declaration["md5"], str)
                                     or not re.fullmatch(r"[0-9a-fA-F]{32}", declaration["md5"])):
            raise BundleError("invalid_declared_md5")
    return document, declarations


def _destination(destination):
    supplied = Path(destination).absolute()
    for component in (supplied, *supplied.parents):
        if component.is_symlink() or getattr(component, "is_junction", lambda: False)():
            raise BundleError("unsafe_destination")
    # Resolve legitimate Windows short-name aliases without accepting links.
    path = supplied.resolve()
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise BundleError("destination_not_empty")
    elif not path.parent.is_dir():
        raise BundleError("destination_parent_missing")
    return path


def _copy(archive, entry, destination, created_files, created_dirs):
    output = destination.joinpath(*PurePosixPath(entry.filename).parts)
    for directory in reversed(output.parent.parents):
        if directory == destination or destination not in directory.parents:
            continue
        if not directory.exists():
            directory.mkdir(mode=0o700)
            created_dirs.append(directory)
        if directory.is_symlink() or not directory.is_dir():
            raise BundleError("unsafe_destination")
    if not output.parent.exists():
        output.parent.mkdir(mode=0o700)
        created_dirs.append(output.parent)
    if output.parent.is_symlink() or output.parent.resolve() != output.parent:
        raise BundleError("unsafe_destination")
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    created_files.append(output)
    sha256, md5, written = hashlib.sha256(), hashlib.md5(usedforsecurity=False), 0
    with os.fdopen(descriptor, "wb") as target, archive.open(entry) as source:
        while chunk := source.read(CHUNK_BYTES):
            written += len(chunk)
            if written > entry.file_size:
                raise BundleError("member_size_mismatch")
            target.write(chunk)
            sha256.update(chunk)
            md5.update(chunk)
    if written != entry.file_size:
        raise BundleError("member_size_mismatch")
    return {"bytes": written, "sha256": sha256.hexdigest(), "md5": md5.hexdigest()}


def extract_xapk(archive_path, destination, *, expected_apk_sha256=None,
                 max_total_bytes=MAX_TOTAL_BYTES, max_entries=16) -> ExtractedBundle:
    """Extract into a caller-owned, workspace-checked private temporary directory.

    Only manifest/base APK/main OBB are written; an optional icon is ignored.
    Returned hashes establish content identity, not publisher authenticity.
    """
    if (not _positive_int(max_total_bytes) or max_total_bytes > MAX_TOTAL_BYTES
            or not _positive_int(max_entries) or max_entries > 64):
        raise BundleError("invalid_limits")
    if expected_apk_sha256 is not None and (not isinstance(expected_apk_sha256, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_apk_sha256)):
        raise BundleError("invalid_expected_apk_hash")
    created_files, created_dirs = [], []
    try:
        output = _destination(destination)
        source_path = Path(archive_path)
        if source_path.is_symlink() or not source_path.is_file():
            raise BundleError("unsafe_archive")
        with source_path.open("rb") as source:
            _check_directory(source, max_entries)
            archive = zipfile.ZipFile(source)
            try:
                files = _inventory(archive, max_total_bytes, max_entries)
                document, declarations = _manifest(archive, files)
                if not output.exists():
                    output.mkdir(mode=0o700)
                    created_dirs.append(output)
                if os.name == "posix":
                    os.chmod(output, 0o700)
                hashes = {}
                for name in ("manifest.json", APK_MEMBER, OBB_MEMBER):
                    hashes[name] = _copy(archive, files[name], output, created_files, created_dirs)
                    expected_md5 = declarations.get(name, {}).get("md5")
                    if expected_md5 and hashes[name]["md5"] != expected_md5.lower():
                        raise BundleError("declared_md5_mismatch")
                if expected_apk_sha256 and hashes[APK_MEMBER]["sha256"] != expected_apk_sha256.lower():
                    raise BundleError("apk_sha256_mismatch")
            finally:
                archive.close()
        return ExtractedBundle(output / APK_MEMBER, output.joinpath(*OBB_MEMBER.split("/")), {
            "format": "xapk_v2", "package_name": PACKAGE_NAME,
            "version_code": VERSION_CODE, "version_name": document["version_name"],
            "files": hashes, "extracted_bytes": sum(value["bytes"] for value in hashes.values()),
            "outer_archive_sha256_verified_here": False, "apk_publisher_signature_verified": False,
            "apk_manifest_and_abi_verified": False,
        })
    except (BundleError, OSError, ValueError, UnicodeError, zipfile.BadZipFile, RuntimeError, RecursionError) as error:
        for path in reversed(created_files):
            try: path.unlink()
            except OSError: pass
        for path in reversed(created_dirs):
            try: path.rmdir()
            except OSError: pass
        if isinstance(error, BundleError):
            raise
        raise BundleError("invalid_bundle") from None
