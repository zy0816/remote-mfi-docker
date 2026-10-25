import unittest
from collections import deque
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.hazmat.primitives.serialization import pkcs7
from cryptography.x509.oid import NameOID

from mfi_service.mfi import MfiAuthenticationClient, verify_signature


class ScriptedTransport:
    def __init__(self, *steps):
        self.steps = deque(steps)

    def transaction(self, address, write_data, read_length):
        if not self.steps:
            raise AssertionError("出现未预期的 I2C 事务")
        expected_write, expected_length, response = self.steps.popleft()
        assert address in (0x10, 0x11)
        assert expected_write == write_data
        assert expected_length == read_length
        return response


def select(register):
    return bytes((register,)), 0, b""


def read(response):
    return b"", len(response), response


def write(register, payload):
    return bytes((register,)) + payload, 0, b""


class MfiAuthenticationClientTest(unittest.TestCase):
    def test_reads_certificate_twice_before_caching(self):
        certificate = bytes(range(64))
        transport = ScriptedTransport(
            select(0x30), read(len(certificate).to_bytes(2, "big")),
            select(0x31), read(certificate),
            select(0x30), read(len(certificate).to_bytes(2, "big")),
            select(0x31), read(certificate),
        )
        client = MfiAuthenticationClient(transport, 0x11)
        self.assertEqual(certificate, client.read_certificate())
        self.assertEqual(certificate, client.read_certificate())
        self.assertFalse(transport.steps)

    def test_sign_sequence_matches_auth_20c(self):
        challenge = bytes(range(1, 21))
        signature = bytes((0xA0 + index) & 0xFF for index in range(128))
        transport = ScriptedTransport(
            write(0x11, b"\x00\x80"),
            write(0x20, b"\x00\x14" + challenge),
            write(0x10, b"\x01"),
            select(0x10), read(b"\x10"),
            select(0x11), read(b"\x00\x80"),
            select(0x12), read(signature),
        )
        client = MfiAuthenticationClient(transport, 0x11)
        self.assertEqual(signature, client.sign_challenge(challenge))
        self.assertFalse(transport.steps)

    def test_verifies_signature_with_certificate_inside_pkcs7(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MFi test")])
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(1)
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=1))
            .sign(key, hashes.SHA256())
        )
        bundle = (
            pkcs7.PKCS7SignatureBuilder()
            .set_data(b"certificate bundle")
            .add_signer(certificate, key, hashes.SHA256())
            .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
        )
        digest = bytes(range(1, 21))
        signature = key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA1()))

        verify_signature(bundle, digest, signature)


if __name__ == "__main__":
    unittest.main()
