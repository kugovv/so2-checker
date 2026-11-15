#!/usr/bin/env python3
"""One ephemeral signed-APK/offline-launch test; never enters an account.

Only fixed per-source file metadata/build and the publisher signing certificate
are accepted. APKPure's published SHA1 is a metadata check, never a substitute
for cryptographic APK signature verification against the publisher SHA256.
No APK, AVD, logcat, XML, account data, or source URL is published as an artifact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import subprocess
import tempfile
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
import zipfile

PACKAGE = "com.axlebolt.standoff2"
BUILD = "203982"
SOURCE_PROFILES = {
    "apkmirror-203982": {"bytes": 103734905, "metadata_hash": {
        "algorithm": "sha256", "value": "f492fe4c5ea0d5aa084ee0376a394625c7248cc96df70cbc966588337d21932a"}},
    "apkpure-203982": {"bytes": 103739086, "metadata_hash": {
        "algorithm": "sha1", "value": "67dc7c1619d32d18ea494b832091cb23b20f0c9c"}},
}
PUBLISHER_SHA256 = "cd2902a2cf385491759513a10174cc7198f8e08b6db7756e1f8ae2e3a1b2063d"
MAX_DOWNLOAD = 160 * 1024 * 1024
DOWNLOAD_SECONDS = 180
REPORT_LIMIT = 48 * 1024
SERIAL = "emulator-5554"
GIB = 1024 ** 3


class CompatError(Exception):
    def __init__(self, category, detail=None):
        self.category = category
        self.detail = detail
        super().__init__(category)


def source_profile():
    name = os.environ.get("APK_SOURCE_PROFILE", "apkmirror-203982")
    if name not in SOURCE_PROFILES:
        raise CompatError("unknown_source_profile")
    return name, SOURCE_PROFILES[name]


def guard() -> Path:
    if (os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux"):
        raise CompatError("ephemeral_runner_required")
    root = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    if not Path.cwd().resolve().is_relative_to(root):
        raise CompatError("workspace_required")
    return root


def command(args, timeout=30, binary=False):
    try:
        result = subprocess.run(args, capture_output=True, text=not binary, timeout=timeout, check=False)
        return result.returncode, result.stdout, result.stderr
    except (OSError, subprocess.TimeoutExpired):
        return None, b"" if binary else "", b"" if binary else ""


def adb(*args, timeout=30):
    return command(["adb", "-s", SERIAL, *args], timeout=timeout)


def host_report(root):
    disk = shutil.disk_usage(root)
    memory = {}
    try:
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        memory = {key: int(values[source].split()[0]) // 1024 for key, source in
                  [("total", "MemTotal"), ("available", "MemAvailable")]}
    except (OSError, KeyError, ValueError):
        pass
    kvm = Path("/dev/kvm")
    return {"cpu_cores": os.cpu_count(), "memory_mib": memory,
            "disk_bytes": {"total": disk.total, "used": disk.used, "free": disk.free},
            "kvm_present": kvm.exists() and stat.S_ISCHR(kvm.stat().st_mode),
            "kvm_accessible": os.access(kvm, os.R_OK | os.W_OK),
            "df": command(["df", "-h", "--output=size,used,avail,pcent,target", str(root)])[1][:4096],
            "free": command(["free", "-m"])[1][:4096]}


def checked_url(url):
    try:
        if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 for c in url):
            raise ValueError()
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.port not in (None, 443) or parsed.fragment:
            raise ValueError()
        hostname = parsed.hostname.encode("idna").decode("ascii")
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None and not literal.is_global:
            raise ValueError()
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or len(addresses) > 32:
            raise ValueError()
        for family, _, _, _, address in addresses:
            if family not in (socket.AF_INET, socket.AF_INET6) or not ipaddress.ip_address(address[0]).is_global:
                raise ValueError()
        return parsed, hostname, addresses
    except (ValueError, UnicodeError, OSError):
        raise CompatError("invalid_or_nonpublic_https_url") from None


class PinnedHTTPS(http.client.HTTPSConnection):
    """Pin the validated DNS results while preserving normal TLS hostname checks."""
    def __init__(self, hostname, addresses, deadline):
        super().__init__(hostname, timeout=min(20, max(0.1, deadline - time.monotonic())), context=ssl.create_default_context())
        self.addresses, self.deadline = addresses, deadline

    def connect(self):
        for family, kind, protocol, _, address in self.addresses:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise CompatError("download_timeout")
            connection = socket.socket(family, kind, protocol)
            try:
                connection.settimeout(min(15, remaining))
                connection.connect(address)
                self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
                self.stream_sock = self.sock
                return
            except OSError:
                connection.close()
        raise CompatError("download_connection_failed")


class VisibleDownloadLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current, self.links = None, []

    def handle_starttag(self, tag, attributes):
        if tag == "a":
            values = dict(attributes)
            self.current = [values.get("href", ""), values.get("class", ""), ""]

    def handle_data(self, text):
        if self.current is not None:
            self.current[2] += text[:1024]

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            href, classes, text = self.current
            text = " ".join(text.lower().split())
            if href and ("downloadbutton" in classes.lower().split() or re.search(r"\bdownload\s+apk\b", text) or text == "click here"):
                self.links.append(href)
            self.current = None


def visible_download_url(html, current_url):
    if urlsplit(current_url).hostname not in {"www.apkmirror.com", "apkmirror.com"}:
        raise CompatError("unexpected_html_download_page")
    lowered = html.lower()
    if "cf-chl-" in lowered or "<title>just a moment" in lowered or "challenge-platform" in lowered:
        raise CompatError("download_challenge_blocked")
    parser = VisibleDownloadLinks()
    parser.feed(html)
    choices = list(dict.fromkeys(urljoin(current_url, link) for link in parser.links))
    if len(choices) != 1:
        raise CompatError("download_link_missing_or_ambiguous")
    return choices[0]


def download_apk(url, destination):
    profile_name, profile = source_profile()
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    redirects = pages = 0
    while True:
        parsed, hostname, addresses = checked_url(url)
        if time.monotonic() >= deadline:
            raise CompatError("download_timeout")
        connection = PinnedHTTPS(hostname, addresses, deadline)
        response = None
        try:
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            connection.request("GET", target, headers={"User-Agent": "so2-checker-compatibility/1.0", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                redirects += 1
                location = response.getheader("Location")
                if redirects > 3 or not location:
                    raise CompatError("download_redirect_limit")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise CompatError("download_blocked" if response.status in (401, 403, 429) else "download_http_error")
            if response.getheader("Content-Encoding", "identity").lower() not in ("identity", ""):
                raise CompatError("unexpected_content_encoding")
            html = "text/html" in response.getheader("Content-Type", "").lower()
            limit = 2 * 1024 * 1024 if html else MAX_DOWNLOAD
            length = response.getheader("Content-Length")
            if length and (not length.isdigit() or int(length) > limit):
                raise CompatError("download_size_limit")
            total, chunks, digest = 0, [], hashlib.sha256()
            metadata_digest = hashlib.new(profile["metadata_hash"]["algorithm"])
            with destination.open("wb") as stream:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise CompatError("download_timeout")
                    connection.stream_sock.settimeout(min(20, remaining))
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise CompatError("download_size_limit")
                    if html:
                        chunks.append(chunk)
                    else:
                        stream.write(chunk)
                        digest.update(chunk)
                        metadata_digest.update(chunk)
            if html:
                pages += 1
                if pages > 2:
                    raise CompatError("download_html_page_limit")
                url = visible_download_url(b"".join(chunks).decode("utf-8", errors="replace"), url)
                continue
            if total != profile["bytes"] or metadata_digest.hexdigest() != profile["metadata_hash"]["value"]:
                raise CompatError("apk_hash_or_size_mismatch")
            return {"source_profile": profile_name, "bytes": total, "actual_sha256": digest.hexdigest(),
                    "metadata_hash_verified": True, "redirects": redirects,
                    "visible_html_pages": pages, "source_host": hostname}
        except CompatError as error:
            if error.detail is None:
                error.detail = {"source_host": hostname[:253], "redirects": redirects,
                                "html_pages": pages, "http_status": response.status if response is not None else None}
            raise
        except (OSError, http.client.HTTPException):
            raise CompatError("download_transport_error", {"source_host": hostname[:253], "redirects": redirects,
                              "html_pages": pages, "http_status": response.status if response is not None else None}) from None
        finally:
            if response is not None:
                response.close()
            connection.close()


def sdk_root():
    value = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not value:
        raise CompatError("android_sdk_missing")
    return Path(value)


def build_tool(name):
    root = sdk_root() / "build-tools"
    try:
        candidates = sorted((directory / name for directory in root.iterdir() if (directory / name).is_file()),
                            key=lambda path: tuple(int(x) for x in re.findall(r"\d+", path.parent.name)), reverse=True)
    except OSError:
        candidates = []
    if not candidates:
        raise CompatError("android_build_tool_missing", {"tool": name, "build_tools_available": False})
    return str(candidates[0])


def signer_certificate_digests(output):
    """Accept the old and Build Tools 37 certificate labels, never key hashes."""
    matches = re.findall(
        r"^(?:Signer #\d+|V[1-4](?:\.\d+)? Signer:) certificate SHA-256 digest: ([a-fA-F0-9]{64})\s*$",
        output, re.MULTILINE,
    )
    return {value.lower() for value in matches}, len(matches)


def verify_apk(path, *, signature_only=False):
    executable = build_tool("apksigner")
    code, output, errors = command([executable, "verify", "--verbose", "--print-certs", str(path)], timeout=60)
    certificates, match_count = signer_certificate_digests(output)
    tool_path = Path(executable)
    diagnostic = {"tool": tool_path.name, "build_tools_version": tool_path.parent.name,
                  "tool_relative_path": "build-tools/" + tool_path.parent.name + "/" + tool_path.name,
                  "tool_executable": os.access(executable, os.X_OK), "exit_code": code,
                  "observed_signer_sha256": sorted(certificates), "signer_digest_matches": match_count,
                  "verification_stdout": output.encode("utf-8")[:4096].decode("utf-8", errors="ignore"),
                  "verification_stderr": errors.encode("utf-8")[:4096].decode("utf-8", errors="ignore")}
    if code is None or code != 0:
        raise CompatError("apk_verifier_failed", diagnostic)
    if not certificates:
        raise CompatError("apk_verifier_output_unrecognized", diagnostic)
    if certificates != {PUBLISHER_SHA256}:
        raise CompatError("publisher_signature_mismatch", diagnostic)
    if signature_only:
        return {"publisher_certificate_sha256": PUBLISHER_SHA256, "signature_diagnostics": diagnostic,
                "manifest_checked": False, "zip_abi_checked": False}, None
    code, output, _ = command([build_tool("aapt"), "dump", "badging", str(path)], timeout=30)
    package = re.search(r"^package: name='([^']+)' versionCode='([^']+)'", output, re.M)
    activity = re.search(r"^launchable-activity: name='([^']+)'", output, re.M)
    native = re.search(r"^native-code: (.+)$", output, re.M)
    abis = set(re.findall(r"'([^']+)'", native.group(1))) if native else set()
    if code != 0 or not package or package.groups() != (PACKAGE, BUILD) or not activity or not re.fullmatch(r"[\w.$]+", activity.group(1), re.ASCII):
        raise CompatError("apk_manifest_mismatch")
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 30000 or "AndroidManifest.xml" not in archive.namelist():
                raise CompatError("apk_zip_invalid")
            zip_abis = {item.filename.split("/")[1] for item in entries
                        if item.filename.startswith("lib/") and item.filename.endswith(".so") and len(item.filename.split("/")) == 3}
    except (OSError, zipfile.BadZipFile):
        raise CompatError("apk_zip_invalid") from None
    if "arm64-v8a" not in abis or abis != zip_abis:
        raise CompatError("apk_abi_mismatch")
    return {"package": PACKAGE, "version_code": BUILD, "abis": sorted(abis),
            "publisher_certificate_sha256": PUBLISHER_SHA256, "signature_diagnostics": diagnostic,
            "manifest_checked": True, "zip_abi_checked": True}, activity.group(1)


def command_failure_detail(tool, code, output, errors):
    return {"tool": tool, "exit_code": code,
            "stdout": output.encode("utf-8")[:2048].decode("utf-8", errors="ignore"),
            "stderr": errors.encode("utf-8")[:2048].decode("utf-8", errors="ignore")}


def dummy_link_diagnostics(code, output, errors):
    """Prove kernel link-kind dummy, rather than trusting the name dummy0."""
    encoded = output.encode("utf-8")
    bounded = encoded[:2048].decode("utf-8", errors="ignore")
    headers = re.findall(r"^\d+:\s+([^\s:]+):", bounded, re.M)
    dummy_kind = bool(re.search(r"^[ \t]+dummy(?:[ \t]|$)", bounded, re.M))
    verified = code == 0 and len(encoded) <= 2048 and headers == ["dummy0"] and dummy_kind
    return {"exit_code": code, "interface": "dummy0", "kernel_kind_dummy_verified": verified,
            "output_truncated": len(encoded) > 2048, "stdout": bounded,
            "stderr": errors.encode("utf-8")[:1024].decode("utf-8", errors="ignore")}


def route_diagnostics(output, family, *, verified_dummy_interface=False):
    """Classify destinations, not occurrences of 'default' in table names.

    ip-route's unreachable/blackhole/prohibit entries discard packets; throw
    entries abandon that table. A usable route in ANY other table still blocks
    the offline check. Unknown syntax, family mismatches and truncation also
    block verification rather than being interpreted as absent connectivity.
    """
    limit = 4096
    encoded = output.encode("utf-8")
    bounded = encoded[:limit].decode("utf-8", errors="ignore")
    lines = [line.strip() for line in bounded.splitlines() if line.strip()]
    truncated = len(encoded) > limit or len(lines) > 128
    nonusable = {"unreachable", "throw", "prohibit", "blackhole"}
    types = nonusable | {"unicast", "local", "broadcast", "multicast", "anycast", "nat"}
    blocking_defaults = nonusable_defaults = dummy_defaults = unparsed = parsed = 0
    for line in lines[:128]:
        tokens = line.split()
        route_type = tokens[0] if tokens[0] in types else "unicast"
        destination_index = 1 if tokens[0] in types else 0
        try:
            destination = tokens[destination_index]
            if destination == "default":
                default = True
            else:
                network = ipaddress.ip_network(destination, strict=False)
                if network.version != family:
                    raise ValueError()
                default = network.prefixlen == 0
        except (IndexError, ValueError):
            unparsed += 1
            if any(token in {"default", "0.0.0.0/0", "::/0"} for token in tokens):
                blocking_defaults += 1
            continue
        parsed += 1
        if default:
            if route_type in nonusable:
                nonusable_defaults += 1
            elif (route_type == "unicast" and verified_dummy_interface and tokens.count("dev") == 1
                  and tokens.index("dev") + 1 < len(tokens) and tokens[tokens.index("dev") + 1] == "dummy0"
                  and not any(word in tokens for word in ("via", "nexthop", "nhid", "encap"))):
                # Android's verified dummy link is a packet sink. Forwarding
                # modifiers or an additional/default real interface still block.
                nonusable_defaults += 1
                dummy_defaults += 1
            else:
                # Omitted/unicast types and less common default route types are
                # conservatively treated as potentially usable, even linkdown.
                blocking_defaults += 1
    return {"classification_complete": not truncated and unparsed == 0,
            "usable_or_uncertain_default_count": blocking_defaults,
            "nonusable_default_count": nonusable_defaults,
            "verified_dummy_default_count": dummy_defaults,
            "parsed_route_count": parsed, "unparsed_line_count": unparsed,
            "output_truncated": truncated, "stdout": bounded}


def offline_status():
    airplane = adb("shell", "settings", "get", "global", "airplane_mode_on")
    wifi = adb("shell", "settings", "get", "global", "wifi_on")
    routes = [adb("shell", "ip", family, "route", "show", "table", "all") for family in ("-4", "-6")]
    commands_ok = airplane[0] == wifi[0] == 0 and all(row[0] == 0 for row in routes)
    dummy = None
    if any(re.search(r"(?:^|\s)dev\s+dummy0(?:\s|$)", row[1]) for row in routes):
        dummy = dummy_link_diagnostics(*adb("shell", "ip", "-d", "link", "show", "dev", "dummy0"))
    verified_dummy = dummy is not None and dummy["kernel_kind_dummy_verified"]
    diagnostics = {"ipv" + str(family): {"exit_code": row[0], **route_diagnostics(row[1], family, verified_dummy_interface=verified_dummy)}
                   for family, row in zip((4, 6), routes)}
    default = any(row["usable_or_uncertain_default_count"] for row in diagnostics.values())
    complete = all(row["classification_complete"] for row in diagnostics.values())
    return {"airplane_mode_enabled": airplane[1].strip() == "1", "wifi_disabled": wifi[1].strip() == "0",
            "route_queries_succeeded": commands_ok, "default_route_present": bool(default),
            "route_classification_complete": complete, "route_diagnostics": diagnostics,
            "dummy_link_diagnostics": dummy,
            "verified": commands_ok and complete and airplane[1].strip() == "1" and wifi[1].strip() == "0" and not default}


def disable_android_network():
    actions = [adb("shell", "cmd", "connectivity", "airplane-mode", "enable"),
               adb("shell", "settings", "put", "global", "airplane_mode_on", "1"),
               adb("shell", "svc", "wifi", "disable"), adb("shell", "svc", "data", "disable")]
    for _ in range(6):
        state = offline_status()
        state["disable_commands_succeeded"] = all(item[0] == 0 for item in actions)
        state["verified"] = state["verified"] and state["disable_commands_succeeded"]
        if state["verified"]:
            return state
        time.sleep(1)
    return state


def process_id():
    code, output, _ = adb("shell", "pidof", PACKAGE)
    return output.split()[0] if code == 0 and re.fullmatch(r"\d+(?:\s+\d+)*\s*", output) else None


def foreground():
    code, output, _ = adb("shell", "dumpsys", "activity", "activities")
    return code == 0 and bool(re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*" + re.escape(PACKAGE) + r"/", output))


def emulator_package_version():
    try:
        text = (sdk_root() / "emulator" / "source.properties").read_text()
        return next(line.split("=", 1)[1].strip() for line in text.splitlines() if line.startswith("Pkg.Revision"))
    except (OSError, StopIteration, CompatError):
        return None


def launch_offline(activity, output_directory):
    before = offline_status()
    if not before["verified"]:
        raise CompatError("offline_not_verified_before_launch")
    # Activity identifiers were restricted to package-name characters. Quote the
    # complete component for the remote Android shell, including literal '$'.
    import shlex
    component = shlex.quote(PACKAGE + "/" + activity)
    code, output, errors = adb("shell", "am start -W -n " + component, timeout=60)
    if code != 0 or "Error:" in output or "Error type" in output or "Status: timeout" in output:
        raise CompatError("activity_launch_failed", command_failure_detail("adb-am-start", code, output, errors))
    first_pid = process_id()
    time.sleep(30)
    alive, focused = process_id() is not None, foreground()
    after = offline_status()
    result = {"attempted": True, "process_alive_after_30s": alive, "app_foreground": focused,
              "offline_after_launch": after, "screenshot_saved": False,
              "error_categories": {"process_exited": not alive, "native_crash_observed": False,
                                   "linker_error_observed": False, "java_crash_observed": False,
                                   "anr_observed": False}, "filtered_logcat_inspected": False}
    if first_pid:
        code, logs, _ = adb("logcat", "-d", "--pid=" + first_pid, "-t", "300", "-v", "brief")
        if code == 0:
            result["filtered_logcat_inspected"] = True
            for key, pattern in [("native_crash_observed", r"Fatal signal|tombstoned"),
                                 ("linker_error_observed", r"UnsatisfiedLinkError|dlopen failed"),
                                 ("java_crash_observed", r"FATAL EXCEPTION"), ("anr_observed", r"ANR in")]:
                result["error_categories"][key] = bool(re.search(pattern, logs))
            del logs  # No raw game/system logs in the report or artifact.
    if after["verified"] and alive and focused:
        code, png, _ = command(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"], binary=True)
        if code == 0 and png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) <= 4 * 1024 * 1024 and foreground():
            (output_directory / "offline-game.png").write_bytes(png)
            result["screenshot_saved"] = True
    return result


def initial_report():
    profile_name, profile = source_profile()
    return {"schema_version": 1, "purpose": "signed_game_apk_offline_compatibility",
            "source_profile": profile_name, "expected_apk_bytes": profile["bytes"],
            "verification_only": os.environ.get("APK_VERIFY_ONLY") == "true",
            "expected_metadata_hash": profile["metadata_hash"].copy(), "actual_sha256": None,
            "expected_version_code": BUILD,
            "authenticity_gate": {"method": "apksigner_verified_publisher_certificate_sha256",
                                  "expected_certificate_sha256": PUBLISHER_SHA256, "verified": False},
            "game_installed": False, "game_authenticated": False, "data_source_ready": False,
            "account_login_attempted": False, "guest_authentication_requested": False,
            "apk_persisted_as_artifact": False, "android_state_persisted": False,
            "launch_attempted": False, "failure_category": None, "failure_detail": None,
            "notes": ["A running offline process does not establish full game functionality or sign-in support.",
                      "Published SHA1 metadata is not a substitute for the mandatory publisher APK signature verification.",
                      "Only bounded observations are published; no APK, raw logs, XML or account data."]}


def save_report(path, report):
    body = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    if len(body) > REPORT_LIMIT:
        raise CompatError("report_size_limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def execute(phase):
    root = guard()
    directory = root / "artifacts" / "android-game-compat"
    if not directory.resolve().is_relative_to(root):
        raise CompatError("report_path_outside_workspace")
    path = directory / "report.json"
    report = initial_report()
    if phase != "before" and path.exists() and path.stat().st_size <= REPORT_LIMIT:
        report = json.loads(path.read_text())
    report["updated_utc"] = datetime.now(timezone.utc).isoformat()
    status = 0
    try:
        if phase == "before":
            report["host_before"] = host = host_report(root)
            if report["verification_only"]:
                report["capacity_checks"] = {"disk_1gib_free": host["disk_bytes"]["free"] >= GIB}
            else:
                report["capacity_checks"] = {"kvm_present": host["kvm_present"], "disk_12gib_free": host["disk_bytes"]["free"] >= 12 * GIB,
                                             "memory_6gib_available": host["memory_mib"].get("available", 0) >= 6144}
            if not all(report["capacity_checks"].values()):
                raise CompatError("runner_capacity_insufficient")
        elif phase in {"game", "verify"}:
            directory.mkdir(parents=True, exist_ok=True)
            report["host_verification" if phase == "verify" else "host_during"] = host_report(root)
            report["verification_only"] = phase == "verify"
            if phase == "game":
                report["emulator_package_version"] = emulator_package_version()
                if adb("shell", "getprop", "sys.boot_completed")[1].strip() != "1":
                    raise CompatError("android_not_booted")
            with tempfile.TemporaryDirectory(prefix=".android-game-apk-", dir=root) as temporary:
                # Temporary APK and its cleanup stay within the checked workspace.
                if not Path(temporary).resolve().is_relative_to(root):
                    raise CompatError("temporary_path_outside_workspace")
                apk = Path(temporary) / "game.apk"
                report["download"] = download_apk(os.environ.get("APK_SOURCE_URL", ""), apk)
                report["actual_sha256"] = report["download"]["actual_sha256"]
                report["verified_apk"], activity = verify_apk(apk, signature_only=phase == "verify")
                report["authenticity_gate"]["verified"] = True
                if phase == "game":
                    report["network_before_install"] = disable_android_network()
                    if not report["network_before_install"]["verified"]:
                        raise CompatError("offline_network_not_verified")
                    code, output, errors = adb("install", "--no-streaming", str(apk), timeout=180)
                    if code != 0 or "Success" not in output:
                        raise CompatError("apk_install_failed", command_failure_detail("adb-install", code, output, errors))
                    report["game_installed"] = True
                    if os.environ.get("ALLOW_OFFLINE_LAUNCH") == "true":
                        report["launch_attempted"] = True
                        report["launch"] = launch_offline(activity, directory)
                        if not report["launch"]["offline_after_launch"]["verified"]:
                            raise CompatError("offline_not_verified_after_launch")
                        if not report["launch"]["process_alive_after_30s"]:
                            raise CompatError("game_process_exited")
        elif phase == "after":
            report["host_after"] = host_report(root)
            outcome = os.environ.get("COMPAT_EMULATOR_OUTCOME", "unknown")
            report["emulator_step_outcome"] = outcome if outcome in {"success", "failure", "cancelled", "skipped"} else "unknown"
            verifier_outcome = os.environ.get("COMPAT_VERIFY_OUTCOME", "unknown")
            report["signature_step_outcome"] = verifier_outcome if verifier_outcome in {"success", "failure", "cancelled", "skipped"} else "unknown"
            if "host_before" in report:
                report["disk_used_delta_bytes"] = report["host_after"]["disk_bytes"]["used"] - report["host_before"]["disk_bytes"]["used"]
    except CompatError as error:
        report["failure_category"], status = error.category, 1
        report["failure_detail"] = error.detail
    except Exception:
        # Do not stringify URL, HTTP headers, subprocess output or exceptions.
        report["failure_category"], status = "local_probe_error", 1
    save_report(path, report)
    print(json.dumps({key: report[key] for key in ("game_installed", "game_authenticated", "data_source_ready", "launch_attempted", "failure_category")}))
    return status


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "verify", "game", "after"), required=True)
    raise SystemExit(execute(parser.parse_args().phase))
