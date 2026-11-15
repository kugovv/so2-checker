#!/usr/bin/env python3
"""Small public report for the manual, ephemeral GitHub Android boot test.

Fixed hardware/property/package probes only: no environment dump, account
inspection, game installation, login, screenshots, logcat, or Android archives.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

REPORT_LIMIT = 32 * 1024
GIB = 1024 ** 3
SERIAL = "emulator-5554"


def require_github_vm() -> Path:
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or os.environ.get("RUNNER_OS") != "Linux"):
        raise RuntimeError("This test is restricted to an ephemeral GitHub-hosted Linux runner")
    workspace = Path(os.environ["GITHUB_WORKSPACE"]).resolve(strict=True)
    if not Path.cwd().resolve().is_relative_to(workspace):
        raise RuntimeError("Run the report from the checked-out workspace")
    return workspace


def command(arguments: list[str], *, timeout: int = 20) -> dict:
    try:
        completed = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
        return {"exit_code": completed.returncode, "stdout": completed.stdout.strip()[:4096]}
    except (OSError, subprocess.TimeoutExpired):
        return {"exit_code": None, "stdout": "", "unavailable": True}


def memory_mib() -> dict:
    try:
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        return {"total": int(fields["MemTotal"].split()[0]) // 1024,
                "available": int(fields["MemAvailable"].split()[0]) // 1024}
    except (OSError, ValueError, KeyError):
        return {"total": None, "available": None}


def host_observation(workspace: Path) -> dict:
    disk = shutil.disk_usage(workspace)
    device = Path("/dev/kvm")
    try:
        details = device.stat()
        kvm = {"present": stat.S_ISCHR(details.st_mode),
               "readable": os.access(device, os.R_OK), "writable": os.access(device, os.W_OK),
               "mode": oct(stat.S_IMODE(details.st_mode))}
    except OSError:
        kvm = {"present": False, "readable": False, "writable": False}
    return {"cpu_cores": os.cpu_count(), "memory_mib": memory_mib(),
            "disk_bytes": {"total": disk.total, "used": disk.used, "free": disk.free},
            "df": command(["df", "-h", "--output=size,used,avail,pcent,target", str(workspace)]),
            "free": command(["free", "-m"]), "kvm": kvm}


def initial_report() -> dict:
    return {"schema_version": 1, "purpose": "manual_ephemeral_android_boot_test",
            "runner_environment": "github-hosted", "runner_os": "Linux",
            "requested_android": {"api_level": 35, "target": "google_apis_playstore",
                                  "architecture": "x86_64", "ram_mib": 4096,
                                  "gpu_mode": "software", "snapshots_enabled": False},
            "boot_test_passed": False, "game_installed": False,
            "game_authenticated": False, "data_source_ready": False,
            "google_sign_in_tested": False, "game_installation_attempted": False,
            "account_login_attempted": False, "android_state_persisted": False,
            "notes": ["Play Services package presence does not establish Google sign-in support.",
                      "ABI/native bridge properties do not establish game compatibility.",
                      "This temporary development job is not a continuously running collector."]}


def load_report(destination: Path) -> dict:
    if not destination.exists():
        return initial_report()
    if destination.stat().st_size > REPORT_LIMIT:
        raise RuntimeError("Report exceeds its fixed bound")
    report = json.loads(destination.read_text())
    if not isinstance(report, dict) or report.get("schema_version") != 1:
        raise RuntimeError("Invalid report schema")
    return report


def save_report(destination: Path, report: dict) -> None:
    body = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    if len(body) > REPORT_LIMIT:
        raise RuntimeError("Report exceeds its fixed bound")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".report-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def property_probe(name: str) -> str | None:
    result = command(["adb", "-s", SERIAL, "shell", "getprop", name])
    return result["stdout"][:256] if result["exit_code"] == 0 else None


def package_probe(name: str) -> bool | None:
    result = command(["adb", "-s", SERIAL, "shell", "pm", "path", name])
    if result["exit_code"] is None:
        return None
    return result["exit_code"] == 0 and result["stdout"].startswith("package:")


def observe_android() -> dict:
    return {"sys_boot_completed": property_probe("sys.boot_completed"),
            "api_level": property_probe("ro.build.version.sdk"),
            "abi_list": property_probe("ro.product.cpu.abilist"),
            "abi_list_32": property_probe("ro.product.cpu.abilist32"),
            "abi_list_64": property_probe("ro.product.cpu.abilist64"),
            "native_bridge": property_probe("ro.dalvik.vm.native.bridge"),
            "play_services_package_present": package_probe("com.google.android.gms"),
            "play_store_package_present": package_probe("com.android.vending"),
            "game_package_present": package_probe("com.axlebolt.standoff2"),
            "data_partition_df_k": command(["adb", "-s", SERIAL, "shell", "df", "-k", "/data"])}


def emulator_version() -> dict:
    executable = shutil.which("emulator")
    if not executable:
        sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if sdk:
            executable = str(Path(sdk) / "emulator" / "emulator")
    return command([executable or "emulator", "-version"])


def execute(phase: str) -> int:
    workspace = require_github_vm()
    destination = workspace / "artifacts" / "android-runner" / "report.json"
    if not destination.resolve().is_relative_to(workspace):
        raise RuntimeError("Report path must stay inside the workspace")
    report = initial_report() if phase == "before" else load_report(destination)
    report["updated_utc"] = datetime.now(timezone.utc).isoformat()
    status = 0
    if phase == "before":
        report["host_before"] = host_observation(workspace)
        host = report["host_before"]
        checks = {"kvm_device_present": host["kvm"]["present"],
                  "disk_free_at_least_8_gib": host["disk_bytes"]["free"] >= 8 * GIB,
                  "memory_available_at_least_6_gib": (host["memory_mib"]["available"] or 0) >= 6144}
        report["capacity_checks"] = checks
        status = 0 if all(checks.values()) else 1
    elif phase == "android":
        report["host_during"] = host_observation(workspace)
        report["emulator_version"] = emulator_version()
        report["android"] = android = observe_android()
        report["game_installed"] = android["game_package_present"]
        checks = {"boot_completed": android["sys_boot_completed"] == "1",
                  "requested_api": android["api_level"] == "35",
                  "x86_64_abi_present": "x86_64" in (android["abi_list"] or "").split(","),
                  "play_services_present": android["play_services_package_present"] is True,
                  "play_store_present": android["play_store_package_present"] is True,
                  "no_game_installed": android["game_package_present"] is False,
                  "kvm_accessible": report["host_during"]["kvm"]["readable"] and report["host_during"]["kvm"]["writable"]}
        report["android_checks"] = checks
        report["boot_test_passed"] = all(checks.values())
        status = 0 if report["boot_test_passed"] else 1
    elif phase == "after":
        report["host_after"] = host_observation(workspace)
        outcome = os.environ.get("ANDROID_EMULATOR_STEP_OUTCOME", "unknown")
        report["emulator_step_outcome"] = outcome if outcome in {"success", "failure", "cancelled", "skipped"} else "unknown"
        report["boot_test_passed"] = report["boot_test_passed"] and outcome == "success"
        if "host_before" in report:
            report["disk_bytes_used_delta"] = report["host_after"]["disk_bytes"]["used"] - report["host_before"]["disk_bytes"]["used"]
    save_report(destination, report)
    print(json.dumps({"phase": phase, "boot_test_passed": report["boot_test_passed"],
                      "game_installed": report["game_installed"],
                      "game_authenticated": False, "data_source_ready": False,
                      "report": "artifacts/android-runner/report.json"}))
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "android", "after"), required=True)
    args = parser.parse_args()
    return execute(args.phase)


if __name__ == "__main__":
    raise SystemExit(main())
