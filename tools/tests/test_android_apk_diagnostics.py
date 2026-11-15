"""Regressions from actual Android Build Tools verifier output; no network/APK."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
