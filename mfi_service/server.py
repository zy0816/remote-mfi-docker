from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import signal
import threading
import time
from collections import OrderedDict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .ch341 import Ch341Error
from .mfi import HardwareManager, MfiError


LOG = logging.getLogger("nas-mfi-service")
REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
MAXIMUM_BODY_BYTES = 4096
MAXIMUM_CHALLENGE_BYTES = 128


class SignatureCache:
    def __init__(self, maximum_entries: int = 128, ttl_seconds: int = 300):
        self.maximum_entries = maximum_entries
        self.ttl_seconds = ttl_seconds
        self.entries: OrderedDict[str, tuple[float, bytes, bytes]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, request_id: str, challenge: bytes) -> bytes | None:
        now = time.monotonic()
        with self.lock:
            self._expire(now)
            item = self.entries.get(request_id)
            if item is None:
                return None
            _, saved_challenge, signature = item
            if not hmac.compare_digest(saved_challenge, challenge):
                raise ValueError("同一个 requestId 不能对应不同 challenge")
            self.entries.move_to_end(request_id)
            return bytes(signature)

    def put(self, request_id: str, challenge: bytes, signature: bytes) -> None:
        with self.lock:
            self.entries[request_id] = (time.monotonic(), bytes(challenge), bytes(signature))
            self.entries.move_to_end(request_id)
            while len(self.entries) > self.maximum_entries:
                self.entries.popitem(last=False)

    def clear(self) -> None:
        with self.lock:
            self.entries.clear()

    def _expire(self, now: float) -> None:
        stale = [key for key, (created, _, _) in self.entries.items() if now - created > self.ttl_seconds]
        for key in stale:
            self.entries.pop(key, None)


class Application:
    def __init__(self, manager: HardwareManager, token: str):
        self.manager = manager
        self.token = token.encode("utf-8")
        self.signatures = SignatureCache()

    def authorized(self, header: str | None) -> bool:
        if header is None or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].encode("utf-8"), self.token)


class MfiRequestHandler(BaseHTTPRequestHandler):
    server_version = f"NasMfi/{__version__}"

    @property
    def app(self) -> Application:
        return self.server.application

    def do_GET(self) -> None:
        if self.path == "/healthz":
            status = self.app.manager.self_test
            self._json(
                HTTPStatus.OK if status is not None else HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "service": "nas-mfi-service",
                    "version": __version__,
                    "ready": status is not None,
                },
            )
            return
        if not self._require_authorization():
            return
        if self.path == "/mfi/certificate":
            self._certificate()
            return
        self._json(HTTPStatus.NOT_FOUND, {"detail": "接口不存在"})

    def do_POST(self) -> None:
        if not self._require_authorization():
            return
        if self.path == "/mfi/reset":
            self._reset()
            return
        if self.path == "/mfi/sign":
            self._sign()
            return
        self._json(HTTPStatus.NOT_FOUND, {"detail": "接口不存在"})

    def _reset(self) -> None:
        try:
            self._read_json()
            result = self.app.manager.reset()
            self.app.signatures.clear()
            self._json(HTTPStatus.OK, {"detail": "", "hardware": result.public_dict()})
        except ValueError as error:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
        except (MfiError, Ch341Error) as error:
            LOG.exception("MFi 重置/自检失败")
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"detail": str(error)})

    def _certificate(self) -> None:
        try:
            certificate = self.app.manager.certificate()
            self._json(
                HTTPStatus.OK,
                {
                    "type": "mfi",
                    "protocolMajor": self.app.manager.protocol_major(),
                    "certificate": base64.b64encode(certificate).decode("ascii"),
                    "certificateSha256": hashlib.sha256(certificate).hexdigest(),
                },
            )
        except (MfiError, Ch341Error) as error:
            LOG.exception("MFi 证书读取失败")
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"detail": str(error)})

    def _sign(self) -> None:
        try:
            request = self._read_json()
            encoded = request.get("challenge")
            request_id = request.get("requestId")
            if not isinstance(encoded, str):
                raise ValueError("challenge 必须是 Base64 字符串")
            if not isinstance(request_id, str) or REQUEST_ID.fullmatch(request_id) is None:
                raise ValueError("requestId 格式无效")
            try:
                challenge = base64.b64decode(encoded, validate=True)
            except Exception as error:
                raise ValueError("challenge 不是有效 Base64") from error
            if not 1 <= len(challenge) <= MAXIMUM_CHALLENGE_BYTES:
                raise ValueError("challenge 长度必须在 1..128 字节")
            signature = self.app.signatures.get(request_id, challenge)
            if signature is None:
                signature = self.app.manager.sign(challenge)
                self.app.signatures.put(request_id, challenge, signature)
            self._json(
                HTTPStatus.OK,
                {"signature": base64.b64encode(signature).decode("ascii")},
            )
        except ValueError as error:
            self._json(HTTPStatus.BAD_REQUEST, {"detail": str(error)})
        except (MfiError, Ch341Error) as error:
            LOG.exception("MFi challenge 签名失败")
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"detail": str(error)})

    def _read_json(self) -> dict:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ValueError("缺少 Content-Length")
        try:
            length = int(content_length)
        except ValueError as error:
            raise ValueError("Content-Length 无效") from error
        if not 0 <= length <= MAXIMUM_BODY_BYTES:
            raise ValueError("请求体过大")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("请求体不是有效 JSON") from error
        if not isinstance(value, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return value

    def _require_authorization(self) -> bool:
        if self.app.authorized(self.headers.get("Authorization")):
            return True
        self._json(
            HTTPStatus.UNAUTHORIZED,
            {"detail": "Bearer token 无效"},
            extra_headers={"WWW-Authenticate": "Bearer"},
        )
        return False

    def _json(self, status: HTTPStatus, payload: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        LOG.info("%s %s", self.address_string(), format % args)


class MfiHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, application: Application):
        super().__init__(address, MfiRequestHandler)
        self.application = application


def main() -> None:
    parser = argparse.ArgumentParser(description="NAS CH341 Remote MFi Service")
    parser.add_argument("--host", default=os.environ.get("MFI_BIND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MFI_BIND_PORT", "8787")))
    parser.add_argument("--check", action="store_true", help="只运行一次硬件自检")
    arguments = parser.parse_args()
    logging.basicConfig(
        level=os.environ.get("MFI_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    token = os.environ.get("MFI_AUTH_TOKEN", "")
    if not arguments.check and len(token) < 24:
        raise SystemExit("MFI_AUTH_TOKEN 必须至少 24 个字符")
    manager = HardwareManager()
    if arguments.check:
        result = manager.reset()
        print(json.dumps(result.public_dict(), ensure_ascii=False, indent=2))
        manager.close()
        return
    try:
        manager.reset()
    except (MfiError, Ch341Error) as error:
        # NAS 可以先启动服务，再插入模块；客户端的 /mfi/reset 会重新探测硬件。
        LOG.warning("MFi 硬件尚未就绪，服务以等待状态启动：%s", error)
    application = Application(manager, token)
    server = MfiHttpServer((arguments.host, arguments.port), application)

    def stop(_signum, _frame) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    LOG.info("NAS MFi 服务已启动：http://%s:%d", arguments.host, arguments.port)
    try:
        server.serve_forever()
    finally:
        manager.close()
        server.server_close()


if __name__ == "__main__":
    main()
