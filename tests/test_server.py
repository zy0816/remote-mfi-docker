import base64
import json
import threading
import unittest
import urllib.error
import urllib.request

from mfi_service.mfi import SelfTestResult
from mfi_service.server import Application, MfiHttpServer


class FakeManager:
    def __init__(self):
        self.certificate_value = b"certificate"
        self.signature_value = b"signature"
        self.sign_calls = 0
        self.self_test = None

    def reset(self):
        self.self_test = SelfTestResult(
            address="0x11",
            device_version_reads=[5],
            protocol_major_reads=[2],
            protocol_major=2,
            protocol_minor=0,
            device_id="00000200",
            self_test_status="0xc0",
            certificate_bytes=len(self.certificate_value),
            signature_bytes=len(self.signature_value),
        )
        return self.self_test

    def certificate(self):
        return self.certificate_value

    def protocol_major(self):
        return 2

    def sign(self, challenge):
        self.sign_calls += 1
        return self.signature_value + challenge[:1]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manager = FakeManager()
        cls.application = Application(cls.manager, "a" * 32)
        cls.server = MfiHttpServer(("127.0.0.1", 0), cls.application)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, method="GET", body=None, authorized=True):
        headers = {}
        if authorized:
            headers["Authorization"] = "Bearer " + "a" * 32
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)

    def test_requires_bearer_token(self):
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request("/mfi/certificate", authorized=False)
        self.assertEqual(401, context.exception.code)

    def test_complete_remote_client_contract(self):
        status, reset = self.request("/mfi/reset", "POST", {})
        self.assertEqual(200, status)
        self.assertEqual("0x11", reset["hardware"]["address"])

        status, certificate = self.request("/mfi/certificate")
        self.assertEqual(200, status)
        self.assertEqual("mfi", certificate["type"])
        self.assertEqual(self.manager.certificate_value, base64.b64decode(certificate["certificate"]))

        challenge = b"\x01\x02\x03"
        body = {"challenge": base64.b64encode(challenge).decode(), "requestId": "same-request"}
        _, first = self.request("/mfi/sign", "POST", body)
        _, second = self.request("/mfi/sign", "POST", body)
        self.assertEqual(first, second)
        self.assertEqual(1, self.manager.sign_calls)


if __name__ == "__main__":
    unittest.main()
