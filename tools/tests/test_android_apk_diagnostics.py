"""Regressions from actual Android Build Tools verifier output; no network/APK."""
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import tempfile
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
        self.assertEqual(captures, 1)
        self.assertTrue(screenshot)

    def test_offline_startup_still_disables_and_verifies_network(self):
        prepared, observed, disable_calls, offline_calls, _, _ = self.observe_startup("offline")
        self.assertTrue(prepared["verified"])
        self.assertEqual(disable_calls, 1)
        self.assertEqual(offline_calls, 2)
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
            with patch.dict(os.environ, {"GAME_STARTUP_NETWORK": "online", "ALLOW_OFFLINE_LAUNCH": "true"}), patch.object(
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


if __name__ == "__main__":
    unittest.main()
