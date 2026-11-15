"""Regressions from actual Android Build Tools verifier output; no network/APK."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = spec_from_file_location("apk_compat", Path(__file__).resolve().parents[1] / "android-game-compat.py")
probe = module_from_spec(spec)
spec.loader.exec_module(probe)


class SignerOutputTests(unittest.TestCase):
    def test_build_tools_37_actual_v2_label(self):
        observed = (
            "Verifies\nVerified using v2 scheme (APK Signature Scheme v2): true\n"
            "Number of signers: 1\n"
            f"V2 Signer: certificate SHA-256 digest: {probe.PUBLISHER_SHA256}\n"
            "V2 Signer: public key SHA-256 digest: " + "a" * 64 + "\n"
        )
        self.assertEqual(probe.signer_certificate_digests(observed), ({probe.PUBLISHER_SHA256}, 1))

    def test_legacy_label_remains_supported(self):
        observed = f"Signer #1 certificate SHA-256 digest: {probe.PUBLISHER_SHA256.upper()}\n"
        self.assertEqual(probe.signer_certificate_digests(observed), ({probe.PUBLISHER_SHA256}, 1))

    def test_public_key_hash_is_never_a_certificate(self):
        observed = f"V2 Signer: public key SHA-256 digest: {probe.PUBLISHER_SHA256}\n"
        self.assertEqual(probe.signer_certificate_digests(observed), (set(), 0))

    def test_additional_different_signer_is_retained_for_rejection(self):
        observed = (f"V2 Signer: certificate SHA-256 digest: {probe.PUBLISHER_SHA256}\n"
                    "V3 Signer: certificate SHA-256 digest: " + "b" * 64 + "\n")
        digests, count = probe.signer_certificate_digests(observed)
        self.assertEqual(count, 2)
        self.assertNotEqual(digests, {probe.PUBLISHER_SHA256})


class OfflineRouteTests(unittest.TestCase):
    def test_explicit_nonusable_policy_defaults_are_not_egress(self):
        for route_type in ("unreachable", "throw", "prohibit", "blackhole"):
            for family, destination in ((4, "default"), (4, "0.0.0.0/0"), (6, "default"), (6, "::/0")):
                with self.subTest(route_type=route_type, family=family, destination=destination):
                    result = probe.route_diagnostics(f"{route_type} {destination} dev lo table 1000 proto static metric 32767\n", family)
                    self.assertTrue(result["classification_complete"])
                    self.assertEqual(result["nonusable_default_count"], 1)
                    self.assertEqual(result["usable_or_uncertain_default_count"], 0)

    def test_real_default_in_any_table_still_blocks_even_with_policy_reject(self):
        for route in ("default via 10.0.2.2 dev eth0 table 1002",
                      "unicast 0.0.0.0/0 dev eth0 linkdown",
                      "local default dev lo table 1003"):
            with self.subTest(route=route):
                result = probe.route_diagnostics("unreachable default table 1000\n" + route, 4)
                self.assertEqual(result["nonusable_default_count"], 1)
                self.assertEqual(result["usable_or_uncertain_default_count"], 1)

    def test_table_named_default_is_not_a_default_destination(self):
        result = probe.route_diagnostics("local 127.0.0.1 dev lo table default proto kernel scope host\n", 4)
        self.assertTrue(result["classification_complete"])
        self.assertEqual(result["usable_or_uncertain_default_count"], 0)

    def test_unknown_or_malformed_routes_and_family_mismatch_are_incomplete(self):
        for route in ("new_route_type default dev eth0", "unreachable", "unreachable unknown_destination",
                      "unreachable ::/0 table 1000", "Error: cannot read routing table"):
            with self.subTest(route=route):
                result = probe.route_diagnostics(route, 4)
                self.assertFalse(result["classification_complete"])
                self.assertGreater(result["unparsed_line_count"], 0)

    def test_oversized_output_is_bounded_and_never_verified(self):
        result = probe.route_diagnostics("local 127.0.0.1 dev lo table local\n" * 300, 4)
        self.assertTrue(result["output_truncated"])
        self.assertFalse(result["classification_complete"])
        self.assertLessEqual(len(result["stdout"].encode("utf-8")), 4096)

    def offline_with(self, ipv4, ipv6="unreachable default dev lo table 1000", route_code=0):
        def adb(*args, **kwargs):
            if args[-1] == "airplane_mode_on":
                return 0, "1\n", ""
            if args[-1] == "wifi_on":
                return 0, "0\n", ""
            return route_code, ipv4 if "-4" in args else ipv6, ""
        with patch.object(probe, "adb", side_effect=adb):
            return probe.offline_status()

    def test_offline_status_accepts_only_reject_defaults_and_keeps_diagnostics(self):
        result = self.offline_with("local 127.0.0.1 dev lo table local\nthrow default table 1000\n")
        self.assertTrue(result["verified"])
        self.assertFalse(result["default_route_present"])
        self.assertEqual(result["route_diagnostics"]["ipv6"]["nonusable_default_count"], 1)

    def test_offline_status_blocks_usable_default_failed_query_and_unknown_output(self):
        for result in (self.offline_with("default via 10.0.2.2 dev eth0 table 1002"),
                       self.offline_with("", route_code=1),
                       self.offline_with("unrecognized output")):
            with self.subTest(result=result):
                self.assertFalse(result["verified"])
        self.assertTrue(self.offline_with("default via 10.0.2.2 dev eth0")["default_route_present"])

    def test_dummy_defaults_require_positive_kernel_kind_proof_and_no_forwarding_modifiers(self):
        route = "default dev dummy0 table 1002 proto static scope link"
        self.assertEqual(probe.route_diagnostics(route, 4)["usable_or_uncertain_default_count"], 1)
        verified = probe.route_diagnostics(route, 4, verified_dummy_interface=True)
        self.assertEqual(verified["verified_dummy_default_count"], 1)
        self.assertEqual(verified["usable_or_uncertain_default_count"], 0)
        for extra in ("\ndefault dev eth0 table 1003", " via 10.0.2.2", " nexthop dev eth0", " encap mpls 100"):
            with self.subTest(extra=extra):
                result = probe.route_diagnostics(route + extra, 4, verified_dummy_interface=True)
                self.assertGreater(result["usable_or_uncertain_default_count"], 0)

    def test_dummy_link_proof_rejects_spoofed_name_unknown_kind_and_failed_query(self):
        header = "9: dummy0: <BROADCAST,NOARP,UP,LOWER_UP> mtu 1500 qdisc noqueue state UNKNOWN\n"
        link = "    link/ether 4a:5c:0b:ff:e7:e4 brd ff:ff:ff:ff:ff:ff\n"
        valid = header + link + "    dummy numtxqueues 1 numrxqueues 1\n"
        self.assertTrue(probe.dummy_link_diagnostics(0, valid, "")["kernel_kind_dummy_verified"])
        for code, output in ((0, header + link + "    veth numtxqueues 1\n"),
                             (0, header + link), (1, valid), (None, valid),
                             (0, valid.replace("dummy0:", "wlan0:")), (0, valid + "x" * 2049)):
            with self.subTest(code=code, output=output[:60]):
                result = probe.dummy_link_diagnostics(code, output, "")
                self.assertFalse(result["kernel_kind_dummy_verified"])
                self.assertLessEqual(len(result["stdout"].encode()), 2048)

    def test_offline_status_queries_kernel_kind_before_accepting_android_dummy_routes(self):
        link = ("9: dummy0: <BROADCAST,NOARP,UP,LOWER_UP> mtu 1500\n"
                "    link/ether 4a:5c:0b:ff:e7:e4 brd ff:ff:ff:ff:ff:ff\n"
                "    dummy numtxqueues 1 numrxqueues 1\n")
        for kind_output, expected in ((link, True), (link.replace("    dummy ", "    veth "), False), ("unknown output", False)):
            def adb(*args, **kwargs):
                if args[-1] == "airplane_mode_on":
                    return 0, "1\n", ""
                if args[-1] == "wifi_on":
                    return 0, "0\n", ""
                if "link" in args:
                    return 0, kind_output, ""
                return 0, "default dev dummy0 table 1002 proto static\n", ""
            with self.subTest(expected=expected), patch.object(probe, "adb", side_effect=adb) as calls:
                result = probe.offline_status()
                self.assertEqual(result["verified"], expected)
                self.assertEqual(result["dummy_link_diagnostics"]["kernel_kind_dummy_verified"], expected)
                calls.assert_any_call("shell", "ip", "-d", "link", "show", "dev", "dummy0")

    def test_install_and_launch_command_diagnostics_are_bounded_and_launch_failure_retained(self):
        detail = probe.command_failure_detail("adb-install", 1, "Ж" * 3000, "INSTALL_FAILED_NO_MATCHING_ABIS")
        self.assertLessEqual(len(detail["stdout"].encode()), 2048)
        self.assertEqual(detail["stderr"], "INSTALL_FAILED_NO_MATCHING_ABIS")
        with patch.object(probe, "offline_status", return_value={"verified": True}), patch.object(
                probe, "adb", return_value=(1, "Error type 3", "Activity class does not exist")):
            with self.assertRaises(probe.CompatError) as caught:
                probe.launch_offline("com.unity3d.player.UnityPlayerActivity", Path("unused"))
        self.assertEqual(caught.exception.category, "activity_launch_failed")
        self.assertEqual(caught.exception.detail["exit_code"], 1)
        self.assertEqual(caught.exception.detail["stdout"], "Error type 3")


class StartupNetworkTests(unittest.TestCase):
    def observe_startup(self, mode, own_foreground=True):
        with tempfile.TemporaryDirectory(prefix="startup-network-test-") as temporary:
            directory = Path(temporary)
            with patch.object(probe, "disable_android_network", return_value={"verified": True}) as disable, patch.object(
                    probe, "offline_status", return_value={"verified": True}) as offline, patch.object(
                    probe, "adb", return_value=(0, "Status: ok", "")), patch.object(
                    probe, "process_id", return_value="123"), patch.object(
                    probe, "foreground_package", return_value=probe.PACKAGE if own_foreground else "com.google.android.gms"), patch.object(
                    probe.time, "sleep"), patch.object(
                    probe, "command", return_value=(0, b"\x89PNG\r\n\x1a\nfixture", b"")) as capture:
                prepared = probe.prepare_startup_network(mode)
                observed = probe.launch_game("com.unity3d.player.UnityPlayerActivity", directory, network_mode=mode)
                return (prepared, observed, disable.call_count, offline.call_count,
                        capture.call_count, (directory / "startup-game.png").exists())

    def test_online_startup_does_not_disable_or_enforce_offline_network(self):
        prepared, observed, disable_calls, offline_calls, captures, screenshot = self.observe_startup("online")
        self.assertFalse(prepared["network_modified"])
        self.assertEqual(disable_calls, 0)
        self.assertEqual(offline_calls, 0)
        self.assertIsNone(observed["offline_after_launch"])
        self.assertTrue(observed["process_alive_after_30s"] and observed["app_foreground"])
        self.assertEqual(captures, 2)
        self.assertTrue(screenshot)

    def test_offline_startup_still_disables_and_verifies_network(self):
        prepared, observed, disable_calls, offline_calls, _, _ = self.observe_startup("offline")
        self.assertTrue(prepared["verified"])
        self.assertEqual(disable_calls, 1)
        self.assertEqual(offline_calls, 3)
        self.assertTrue(observed["offline_after_launch"]["verified"])

    def test_online_screenshot_is_not_captured_outside_own_foreground(self):
        _, observed, _, _, captures, screenshot = self.observe_startup("online", own_foreground=False)
        self.assertFalse(observed["screenshot_saved"])
        self.assertEqual(captures, 0)
        self.assertFalse(screenshot)

    def test_unknown_network_mode_is_rejected_before_any_device_operation(self):
        with patch.object(probe, "adb", side_effect=AssertionError("must not touch Android")):
            for call in (lambda: probe.prepare_startup_network("unknown"),
                         lambda: probe.launch_game("Activity", Path("unused"), network_mode="unknown")):
                with self.assertRaises(probe.CompatError) as caught:
                    call()
                self.assertEqual(caught.exception.category, "unknown_startup_network")

    def test_online_report_keeps_authentication_unknown_and_account_input_absent(self):
        with patch.dict(os.environ, {"GAME_STARTUP_NETWORK": "online"}):
            report = probe.initial_report()
        self.assertEqual(report["startup_network"], "online")
        self.assertIsNone(report["game_authenticated"])
        self.assertIsNone(report["guest_authentication_requested"])
        self.assertFalse(report["game_authentication_verified"])
        self.assertFalse(report["account_login_attempted"])
        self.assertFalse(report["data_source_ready"])

    def test_foreground_diagnostic_retains_only_the_validated_component_package(self):
        output = ("topResumedActivity=ActivityRecord{abc u0 com.google.android.gms/.auth.SignInActivity t3}\n"
                  "Intent extras and URI values must never be returned\n")
        with patch.object(probe, "adb", return_value=(0, output, "")):
            self.assertEqual(probe.foreground_package(), "com.google.android.gms")
            self.assertFalse(probe.foreground())
        with patch.object(probe, "adb", return_value=(0, "topResumedActivity=unknown format\ncom.example.unrelated/Activity", "")):
            self.assertIsNone(probe.foreground_package())

    def test_online_execute_requires_own_foreground_after_successful_install(self):
        with tempfile.TemporaryDirectory(prefix="startup-execute-test-") as temporary:
            root = Path(temporary).resolve()
            with patch.dict(os.environ, {"APK_SOURCE_PROFILE": "apkpure-203982", "GAME_STARTUP_NETWORK": "online", "ALLOW_OFFLINE_LAUNCH": "true"}), patch.object(
                    probe, "guard", return_value=root), patch.object(probe, "host_report", return_value={}), patch.object(
                    probe, "emulator_package_version", return_value="test"), patch.object(
                    probe, "download_apk", return_value={"actual_sha256": "unit-test-fixture"}), patch.object(
                    probe, "verify_apk", return_value=({"package": probe.PACKAGE}, "Activity")), patch.object(
                    probe, "adb", side_effect=[(0, "1\n", ""), (0, "Success\n", "")]), patch.object(
                    probe, "disable_android_network", side_effect=AssertionError("online must not disable network")), patch.object(
                    probe, "offline_status", side_effect=AssertionError("online must not enforce offline")), patch.object(
                    probe, "launch_game", return_value={"process_alive_after_30s": True, "app_foreground": False, "offline_after_launch": None}):
                with patch("builtins.print"):
                    self.assertEqual(probe.execute("game"), 1)
            report = json.loads((root / "artifacts/android-game-compat/report.json").read_text())
            self.assertEqual(report["failure_category"], "game_not_foreground")
            self.assertTrue(report["game_installed"])
            self.assertIsNone(report["game_authenticated"])
            self.assertFalse(report["game_authentication_verified"])


class XapkStartupOrderingTests(unittest.TestCase):
    def run_fixture(self, failure=None):
        """Synthetic tiny files and mocked Android; never download or run a game."""
        events = []
        profile = probe.SOURCE_PROFILES["apkpure-xapk-203982"]
        obb_data = b"tiny synthetic OBB fixture"
        obb_sha256 = probe.hashlib.sha256(obb_data).hexdigest()
        with tempfile.TemporaryDirectory(prefix="xapk-order-test-") as temporary:
            root = Path(temporary).resolve()

            def download(url, destination):
                events.append("download-hash")
                return {"bytes": profile["bytes"], "metadata_hash_verified": True,
                        "actual_sha256": "0" * 64 if failure == "hash" else profile["metadata_hash"]["value"]}

            def extract(source, destination, selected_profile):
                events.append("extract")
                destination.mkdir()
                apk = destination / (probe.PACKAGE + ".apk")
                apk.write_bytes(b"synthetic APK fixture")
                obb = destination / probe.OBB_NAME
                obb.write_bytes(obb_data)
                return SimpleNamespace(apk_path=apk, obb_path=obb, metadata={
                    "extracted_bytes": len(obb_data) + 30,
                    "files": {probe.PACKAGE + ".apk": {"bytes": 21, "sha256": profile["apk_sha256"]},
                              probe.OBB_MEMBER: {"bytes": len(obb_data), "sha256": obb_sha256},
                              "manifest.json": {"bytes": 9, "sha256": "a" * 64}}})

            def verify(path, **kwargs):
                events.append("publisher-signature-manifest-abi")
                self.assertTrue(Path(path).is_file())
                if failure == "signature":
                    raise probe.CompatError("publisher_signature_mismatch")
                return {"package": probe.PACKAGE, "manifest_checked": True, "zip_abi_checked": True}, "Activity"

            def adb(*args, **kwargs):
                if args[:2] == ("shell", "getprop"):
                    return 0, "1\n", ""
                if args[0] == "install":
                    self.assertEqual(args[:3], ("install", "-g", "--no-streaming"))
                    self.assertEqual(kwargs.get("timeout"), 180)
                    events.append("install")
                    if failure == "install":
                        return 1, "", "INSTALL_FAILED_TEST_FIXTURE"
                    return 0, "Success\n", ""
                if args[:2] == ("shell", "mkdir"):
                    self.assertEqual(args[-1], probe.DEVICE_OBB_DIRECTORY)
                    events.append("own-obb-directory")
                    return 0, "", ""
                if args[0] == "push":
                    self.assertEqual(args[-1], probe.DEVICE_OBB_PATH)
                    events.append("obb-push")
                    return 0, "1 file pushed", ""
                if args[:2] == ("shell", "stat"):
                    events.append("device-size")
                    return 0, str(len(obb_data)), ""
                if args[:2] == ("shell", "sha256sum"):
                    events.append("device-sha256")
                    digest = "0" * 64 if failure == "checksum" else obb_sha256
                    return 0, digest + "  " + probe.DEVICE_OBB_PATH + "\n", ""
                raise AssertionError("Unexpected Android command")

            def launch(*args, **kwargs):
                events.append("launch")
                if failure == "startup":
                    return {"startup_failed": True, "initial_pid_present": False, "startup_retry_attempted": True,
                            "process_alive_after_30s": None, "app_foreground": None}
                return {"process_alive_after_30s": True, "app_foreground": True, "offline_after_launch": None}

            def settled(*args, **kwargs):
                events.append("settled-90s")
                return {"process_alive": True, "app_foreground": True, "offline_status": None, "screenshot_saved": False}

            with patch.dict(os.environ, {"GAME_STARTUP_NETWORK": "online", "ALLOW_OFFLINE_LAUNCH": "true",
                                        "APK_SOURCE_PROFILE": "apkpure-xapk-203982", "APK_VERIFY_ONLY": "false"}), patch.object(
                    probe, "guard", return_value=root), patch.object(probe, "host_report", return_value={}), patch.object(
                    probe, "emulator_package_version", return_value="test"), patch.object(
                    probe, "download_apk", side_effect=download), patch.object(probe, "extract_source_bundle", side_effect=extract), patch.object(
                    probe, "verify_apk", side_effect=verify), patch.object(probe, "adb", side_effect=adb), patch.object(
                    probe, "launch_game", side_effect=launch), patch.object(
                    probe, "settled_startup_observation", side_effect=settled), patch("builtins.print"):
                status = probe.execute("game")
            return status, json.loads((root / "artifacts/android-game-compat/report.json").read_text()), events

    def test_outer_hash_signature_install_and_verified_obb_transfer_precede_launch(self):
        status, report, events = self.run_fixture()
        self.assertEqual(status, 0)
        self.assertEqual(events, ["download-hash", "extract", "publisher-signature-manifest-abi", "install",
                                  "own-obb-directory", "obb-push", "device-size", "device-sha256", "launch", "settled-90s"])
        self.assertTrue(report["obb_push_verified"])
        self.assertTrue(report["runtime_permissions_granted_at_install"])
        self.assertEqual(report["source_format"], "xapk")
        self.assertEqual(report["expected_source_bytes"], 2102952039)
        self.assertEqual(report["expected_apk_bytes"], 103739086)
        self.assertNotIn("path", str(report["bundle_extraction"]).lower())
        self.assertIsNone(report["game_authenticated"])
        self.assertFalse(report["data_source_ready"])

    def test_failed_outer_hash_prevents_extraction_signature_and_install(self):
        status, report, events = self.run_fixture("hash")
        self.assertEqual(status, 1)
        self.assertEqual(report["failure_category"], "xapk_outer_integrity_unverified")
        self.assertEqual(events, ["download-hash"])
        self.assertFalse(report["game_installed"])
        self.assertFalse(report["runtime_permissions_granted_at_install"])

    def test_failed_publisher_signature_prevents_install_obb_push_and_launch(self):
        status, report, events = self.run_fixture("signature")
        self.assertEqual(status, 1)
        self.assertEqual(report["failure_category"], "publisher_signature_mismatch")
        self.assertNotIn("install", events)
        self.assertNotIn("obb-push", events)
        self.assertNotIn("launch", events)
        self.assertFalse(report["authenticity_gate"]["verified"])
        self.assertFalse(report["runtime_permissions_granted_at_install"])

    def test_failed_install_never_claims_runtime_permissions_granted(self):
        status, report, events = self.run_fixture("install")
        self.assertEqual(status, 1)
        self.assertEqual(report["failure_category"], "apk_install_failed")
        self.assertFalse(report["game_installed"])
        self.assertFalse(report["runtime_permissions_granted_at_install"])
        self.assertNotIn("obb-push", events)
        self.assertNotIn("launch", events)

    def test_device_obb_checksum_mismatch_prevents_launch(self):
        status, report, events = self.run_fixture("checksum")
        self.assertEqual(status, 1)
        self.assertEqual(report["failure_category"], "obb_device_hash_unverified")
        self.assertTrue(report["game_installed"])
        self.assertFalse(report["obb_push_verified"])
        self.assertFalse(report["launch_attempted"])
        self.assertNotIn("launch", events)

    def test_missing_process_after_bounded_retry_fails_before_90s_observation(self):
        status, report, events = self.run_fixture("startup")
        self.assertEqual(status, 1)
        self.assertEqual(report["failure_category"], "game_process_missing_after_startup_retry")
        self.assertTrue(report["launch"]["startup_retry_attempted"])
        self.assertIsNone(report["launch"]["process_alive_after_30s"])
        self.assertNotIn("settled-90s", events)

    def test_full_bundle_verify_only_is_rejected_without_download(self):
        with tempfile.TemporaryDirectory(prefix="xapk-verify-test-") as temporary:
            root = Path(temporary).resolve()
            with patch.dict(os.environ, {"APK_SOURCE_PROFILE": "apkpure-xapk-203982", "APK_VERIFY_ONLY": "true"}), patch.object(
                    probe, "guard", return_value=root), patch.object(probe, "download_apk") as download, patch("builtins.print"):
                self.assertEqual(probe.execute("verify"), 1)
            download.assert_not_called()
            report = json.loads((root / "artifacts/android-game-compat/report.json").read_text())
            self.assertEqual(report["failure_category"], "xapk_verify_only_not_supported")

    def test_full_bundle_capacity_requires_16gib_disk_and_6gib_available_memory(self):
        for disk, memory, expected in ((15, 8192, 1), (16, 6143, 1), (16, 6144, 0)):
            with self.subTest(disk=disk, memory=memory), tempfile.TemporaryDirectory(prefix="xapk-capacity-test-") as temporary:
                root = Path(temporary).resolve()
                host = {"disk_bytes": {"free": disk * probe.GIB}, "memory_mib": {"available": memory}, "kvm_present": True}
                with patch.dict(os.environ, {"APK_SOURCE_PROFILE": "apkpure-xapk-203982", "APK_VERIFY_ONLY": "false"}), patch.object(
                        probe, "guard", return_value=root), patch.object(probe, "host_report", return_value=host), patch("builtins.print"):
                    self.assertEqual(probe.execute("before"), expected)


class ExitAndLaunchDiagnosticsTests(unittest.TestCase):
    def test_settled_checkpoint_waits_only_extra_60s_and_keeps_own_app_guard(self):
        for own_foreground in (True, False):
            with self.subTest(own_foreground=own_foreground), patch.object(probe.time, "sleep") as sleep, patch.object(
                    probe, "process_id", return_value="123"), patch.object(
                    probe, "foreground_package", return_value=probe.PACKAGE if own_foreground else "com.android.launcher"), patch.object(
                    probe, "offline_status", side_effect=AssertionError("online observation must not enforce offline")), patch.object(
                    probe, "game_screenshot", return_value=True) as screenshot:
                result = probe.settled_startup_observation(Path("unused"), network_mode="online")
            sleep.assert_called_once_with(60)
            self.assertEqual(result["screenshot_saved"], own_foreground)
            self.assertEqual(screenshot.call_count, 1 if own_foreground else 0)

    def test_immersive_confirmation_is_changed_only_via_fixed_setting_and_verified(self):
        with patch.object(probe, "adb", side_effect=[(0, "", ""), (0, "confirmed\n", "")]) as adb:
            result = probe.configure_immersive_confirmation()
        self.assertTrue(result["confirmed"])
        self.assertEqual(adb.call_args_list[0].args, ("shell", "settings", "put", "secure", "immersive_mode_confirmations", "confirmed"))
        self.assertEqual(adb.call_args_list[1].args, ("shell", "settings", "get", "secure", "immersive_mode_confirmations"))
        for written, readback in (((1, "", "denied"), (0, "confirmed", "")), ((0, "", ""), (0, "null", ""))):
            with patch.object(probe, "adb", side_effect=[written, readback]):
                result = probe.configure_immersive_confirmation()
                self.assertFalse(result["confirmed"])
                self.assertTrue(all(isinstance(value, bool) for value in result.values()))

    def test_latest_exit_info_returns_only_numeric_latest_fields(self):
        output = ("Historical Process Exit for com.axlebolt.standoff2:\n"
                  "  ApplicationExitInfo #0:\n"
                  "    timestamp=private pid=123 process=com.axlebolt.standoff2 reason=2 (SIGNALED) subreason=0 (UNKNOWN) status=9\n"
                  "    description=private diagnostic token=never-return\n"
                  "  ApplicationExitInfo #1:\n"
                  "    process=com.axlebolt.standoff2 reason=6 (ANR) subreason=7 status=0\n")
        self.assertEqual(probe.parse_latest_exit_info(output), {"parsed": True, "pid": 123, "reason": 2, "status": 9, "subreason": 0})
        absent_subreason = "ApplicationExitInfo #0:\n process=com.axlebolt.standoff2 reason=1 (EXIT_SELF) status=0\n"
        self.assertEqual(probe.parse_latest_exit_info(absent_subreason), {"parsed": True, "pid": None, "reason": 1, "status": 0, "subreason": None})

    def test_unknown_oversized_or_ambiguous_exit_info_does_not_invent_a_reason(self):
        for output in ("Permission denied", "ApplicationExitInfo #1:\n reason=2 status=9\n",
                       "ApplicationExitInfo #0:\n reason=2 reason=6 status=9\n", "x" * 65537):
            with self.subTest(output=output[:70]):
                self.assertEqual(probe.parse_latest_exit_info(output), {"parsed": False, "pid": None, "reason": None, "status": None, "subreason": None})

    def test_fixed_log_categories_detect_resource_graphics_translation_and_quit_markers(self):
        samples = {
            "missing_game_assets_observed": ["OBB expansion file main.203982.com.axlebolt.standoff2.obb missing",
                "Unable to open /storage/emulated/0/Android/obb/com.axlebolt.standoff2/main.203982.com.axlebolt.standoff2.obb",
                "Unity data not found at assets/bin/Data", "No OBB files found", "OBB path is empty"],
            "graphics_initialization_failure_observed": ["Failed to initialize Vulkan device", "EGL_BAD_ALLOC"],
            "native_translation_failure_observed": ["libndk_translation: native bridge failed to load"],
            "explicit_unity_quit_observed": ["Unity: Application.Quit() requested"],
            "virtual_environment_rejection_observed": ["emulator environment is not supported"],
            "integrity_failure_observed": ["Play Integrity verification failed"],
        }
        for category, messages in samples.items():
            for message in messages:
                with self.subTest(category=category, message=message):
                    result = probe.log_error_categories(message + "\nprivate-token=never-return")
                    self.assertTrue(result[category])
                    self.assertTrue(all(isinstance(value, bool) for value in result.values()))
                    self.assertNotIn("never-return", str(result))
        benign = "Vulkan initialized successfully; OBB expansion loaded; emulator detected; Play Integrity passed"
        self.assertFalse(any(probe.log_error_categories(benign).values()))

    def test_early_screenshot_survives_later_process_exit_and_uses_2_plus_28_second_wait(self):
        with tempfile.TemporaryDirectory(prefix="early-startup-test-") as temporary:
            directory = Path(temporary)
            with patch.object(probe, "adb", return_value=(0, "Status: ok", "")), patch.object(
                    probe, "process_id", side_effect=["123", "123", "123", "123", None]), patch.object(
                    probe, "foreground_package", side_effect=[probe.PACKAGE, probe.PACKAGE, probe.PACKAGE, "com.google.android.apps.nexuslauncher"]), patch.object(
                    probe.time, "sleep") as sleep, patch.object(probe, "latest_exit_info", return_value={"parsed": True, "reason": 1, "status": 0, "subreason": 0}), patch.object(
                    probe, "sdk_graphics_summary", return_value={}), patch.object(
                    probe, "command", return_value=(0, b"\x89PNG\r\n\x1a\nfixture", b"")):
                result = probe.launch_game("com.unity3d.player.UnityPlayerActivity", directory, network_mode="online")
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 28])
            self.assertTrue(result["early_screenshot_saved"])
            self.assertFalse(result["process_alive_after_30s"])
            self.assertFalse(result["screenshot_saved"])
            self.assertTrue((directory / "early-startup-game.png").exists())
            self.assertFalse((directory / "startup-game.png").exists())

    def test_screenshot_guard_requires_own_foreground_and_live_process_before_and_after_capture(self):
        with tempfile.TemporaryDirectory(prefix="screen-guard-test-") as temporary:
            directory = Path(temporary)
            with patch.object(probe, "process_id", return_value=None), patch.object(probe, "command") as capture:
                self.assertFalse(probe.game_screenshot(directory, "early-startup-game.png"))
                capture.assert_not_called()
            with patch.object(probe, "process_id", return_value="123"), patch.object(
                    probe, "foreground", return_value=False), patch.object(probe, "command") as capture:
                self.assertFalse(probe.game_screenshot(directory, "early-startup-game.png"))
                capture.assert_not_called()
            with patch.object(probe, "process_id", return_value="123"), patch.object(
                    probe, "foreground", side_effect=[True, False]), patch.object(
                    probe, "command", return_value=(0, b"\x89PNG\r\n\x1a\nfixture", b"")):
                self.assertFalse(probe.game_screenshot(directory, "early-startup-game.png"))
            self.assertFalse((directory / "early-startup-game.png").exists())

    def test_graphics_summary_is_fixed_property_only_and_bounded(self):
        def adb(*args, **kwargs):
            if args[-1] == "ro.opengles.version":
                return 1, "", "unavailable"
            return 0, "x" * 300, ""
        with patch.object(probe, "adb", side_effect=adb) as calls:
            result = probe.sdk_graphics_summary()
        self.assertIsNone(result["opengles_version"])
        self.assertTrue(all(value is None or len(value.encode()) <= 128 for value in result.values()))
        self.assertTrue(all(call.args[:2] == ("shell", "getprop") for call in calls.call_args_list))


class StartupRetryTests(unittest.TestCase):
    baseline = {"query_succeeded": True, "parsed": True, "pid": 111, "reason": 10, "subreason": 21, "status": 0}

    def cold_start(self, second_pid, current_exit):
        with tempfile.TemporaryDirectory(prefix="cold-start-test-") as temporary:
            directory = Path(temporary)
            pids = [None] + ([second_pid] * 7 if second_pid is not None else [None])
            with patch.object(probe, "configure_immersive_confirmation", return_value={"confirmed": True}) as immersive, patch.object(
                    probe, "latest_exit_info", side_effect=[dict(self.baseline), current_exit]), patch.object(
                    probe, "process_id", side_effect=pids), patch.object(probe, "foreground_package", return_value=probe.PACKAGE), patch.object(
                    probe, "sdk_graphics_summary", return_value={}), patch.object(probe.time, "sleep") as sleep, patch.object(
                    probe, "adb", return_value=(0, "Status: ok\n", "")) as adb, patch.object(
                    probe, "command", return_value=(0, b"\x89PNG\r\n\x1a\nfixture", b"")) as capture:
                result = probe.launch_game("Activity", directory, network_mode="online")
            starts = [call for call in adb.call_args_list if call.args[0] == "shell" and call.args[1].startswith("am start ")]
            self.assertEqual(len(starts), 2)
            self.assertEqual(starts[0].args, starts[1].args)
            self.assertTrue(all(call.kwargs["timeout"] == 60 for call in starts))
            self.assertFalse(any("force-stop" in str(call.args) for call in adb.call_args_list))
            immersive.assert_called_once()
            return result, [call.args[0] for call in sleep.call_args_list], capture.call_count

    def test_second_cold_start_can_supply_live_pid_with_exactly_one_retry(self):
        current = {**self.baseline, "pid": 222, "reason": 1, "subreason": 0}
        result, waits, _ = self.cold_start("222", current)
        self.assertFalse(result["startup_failed"])
        self.assertFalse(result["initial_pid_present"])
        self.assertTrue(result["startup_retry_attempted"])
        self.assertEqual(result["startup_attempt_count"], 2)
        self.assertEqual(result["startup_retry_limit"], 1)
        self.assertTrue(result["process_alive_after_30s"])
        self.assertTrue(result["exit_record_changed_since_launch"])
        self.assertEqual(waits, [3, 2, 28])

    def test_both_missing_pids_stop_without_unperformed_30s_claim_or_more_retries(self):
        result, waits, captures = self.cold_start(None, dict(self.baseline))
        self.assertTrue(result["startup_failed"])
        self.assertFalse(result["exit_record_changed_since_launch"])
        self.assertEqual(waits, [3])
        self.assertEqual(captures, 0)
        self.assertIsNone(result["process_alive_after_2s"])
        self.assertIsNone(result["process_alive_after_30s"])
        self.assertFalse(result["filtered_logcat_inspected"])

    def test_exit_record_correlation_compares_only_valid_numeric_record_fields(self):
        self.assertFalse(probe.exit_record_changed(self.baseline, dict(self.baseline)))
        for field, value in (("pid", 222), ("reason", 2), ("status", 9), ("subreason", 0)):
            with self.subTest(field=field):
                self.assertTrue(probe.exit_record_changed(self.baseline, {**self.baseline, field: value}))
        self.assertIsNone(probe.exit_record_changed({**self.baseline, "parsed": False}, self.baseline))
        self.assertIsNone(probe.exit_record_changed({**self.baseline, "pid": None}, self.baseline))
        self.assertIsNone(probe.exit_record_changed({**self.baseline, "pid": "111"}, self.baseline))

    def test_am_start_requires_explicit_status_ok_before_checking_pid_or_retrying(self):
        with patch.object(probe, "configure_immersive_confirmation", return_value={"confirmed": True}), patch.object(
                probe, "latest_exit_info", return_value=self.baseline), patch.object(
                probe, "adb", return_value=(0, "Starting: Intent ...\n", "")), patch.object(probe, "process_id") as pid:
            with self.assertRaises(probe.CompatError) as caught:
                probe.launch_game("Activity", Path("unused"), network_mode="online")
        self.assertEqual(caught.exception.category, "activity_launch_failed")
        pid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
