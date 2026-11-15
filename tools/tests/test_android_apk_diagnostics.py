"""Regressions from actual Android Build Tools verifier output; no network/APK."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
