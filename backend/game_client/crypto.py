"""Legacy payload encryption, selected explicitly; modern TLS RPC omits it.

RSA-1024 and static initial AES material are dictated by the published legacy
protocol. TLS certificate/hostname verification remains enabled throughout.
"""
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .errors import ProtocolError


class LegacyCipher:
    def __init__(self, key=b"key_abcdefghijkl", iv=b"iv_abcdefghijklm"):
        if len(key) not in (16, 24, 32) or len(iv) != 16:
            raise ProtocolError()
        self._cipher = Cipher(algorithms.AES(key), modes.CBC(iv))

    def encrypt(self, body: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        padded = padder.update(body) + padder.finalize()
        encryptor = self._cipher.encryptor()
        return encryptor.update(padded) + encryptor.finalize()

    def decrypt(self, body: bytes) -> bytes:
        try:
            decryptor = self._cipher.decryptor()
            padded = decryptor.update(body) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            return unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            raise ProtocolError() from None
