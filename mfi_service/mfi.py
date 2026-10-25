from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.hazmat.primitives.serialization import pkcs7

from .ch341 import Ch341Error, Ch341I2cTransport, LibusbSession


LOG = logging.getLogger(__name__)


class I2cTransport(Protocol):
    def transaction(self, address: int, write_data: bytes, read_length: int) -> bytes: ...


class MfiError(RuntimeError):
    pass


class MfiInvalidData(MfiError):
    pass


@dataclass(frozen=True)
class SelfTestResult:
    address: str
    device_version_reads: list[int | None]
    protocol_major_reads: list[int | None]
    protocol_major: int
    protocol_minor: int
    device_id: str
    self_test_status: str
    certificate_bytes: int
    signature_bytes: int

    def public_dict(self) -> dict:
        return asdict(self)


class MfiAuthenticationClient:
    MAXIMUM_CERTIFICATE_BYTES = 65_525

    def __init__(self, transport: I2cTransport, address: int):
        self.transport = transport
        self.address = address
        self.lock = threading.RLock()
        self.cached_certificate: bytes | None = None

    def device_version(self) -> int:
        return self._read_byte(0x00)

    def protocol_major(self) -> int:
        return self._read_byte(0x02)

    def protocol_minor(self) -> int:
        return self._read_byte(0x03)

    def device_id(self) -> bytes:
        return self._read_register(0x04, 4)

    def run_self_test(self) -> int:
        self._write_register(0x40, b"\x01")
        time.sleep(0.020)
        return self._read_byte(0x40)

    def read_certificate(self, maximum: int = MAXIMUM_CERTIFICATE_BYTES) -> bytes:
        with self.lock:
            if self.cached_certificate is not None:
                if len(self.cached_certificate) > maximum:
                    raise MfiInvalidData("缓存证书超过允许长度")
                return bytes(self.cached_certificate)
            valid: list[bytes] = []
            last_error: Exception | None = None
            for _ in range(12):
                try:
                    candidate = self._read_certificate_once(maximum)
                    if any(candidate == previous for previous in valid):
                        self.cached_certificate = candidate
                        return bytes(candidate)
                    valid.append(candidate)
                except (MfiError, Ch341Error) as error:
                    last_error = error
                time.sleep(0.020)
            if valid:
                raise MfiInvalidData(f"证书 {len(valid)} 次有效读取未形成两次一致结果") from last_error
            raise MfiInvalidData("未能读取有效证书") from last_error

    def _read_certificate_once(self, maximum: int) -> bytes:
        length = self._read_u16(0x30)
        if not 1 <= length <= maximum:
            raise MfiInvalidData(f"证书长度 {length} 不在 1..{maximum} 范围")
        certificate = bytearray()
        register = 0x31
        while len(certificate) < length:
            count = min(128, length - len(certificate))
            certificate.extend(self._read_register(register, count))
            register += 1
        value = bytes(certificate)
        if len(set(value)) == 1 and value[0] in (0x00, 0xFF):
            raise MfiInvalidData("证书只有总线填充值")
        return value

    def sign_challenge(self, challenge: bytes) -> bytes:
        if not 1 <= len(challenge) <= 128:
            raise MfiInvalidData("challenge 长度必须在 1..128 字节")
        last_error: Exception | None = None
        for _ in range(3):
            try:
                return self._sign_once(challenge)
            except (MfiError, Ch341Error) as error:
                last_error = error
                time.sleep(0.020)
        raise MfiError("芯片签名连续三次失败") from last_error

    def _sign_once(self, challenge: bytes) -> bytes:
        self._write_u16(0x11, 128)
        self._write_register(0x20, len(challenge).to_bytes(2, "big") + challenge)
        self._write_register(0x10, b"\x01")
        time.sleep(0.250)
        deadline = time.monotonic() + 3.0
        while True:
            try:
                status = self._read_byte(0x10)
                if not status & 0x80 and status & 0x70 == 0x10:
                    break
            except Ch341Error:
                pass
            if time.monotonic() >= deadline:
                raise MfiError("等待芯片签名超时")
            time.sleep(0.010)
        length = self._read_u16(0x11)
        if not 1 <= length <= 128:
            raise MfiInvalidData(f"签名长度 {length} 无效")
        signature = self._read_register(0x12, length)
        if len(set(signature)) == 1 and signature[0] in (0x00, 0xFF):
            raise MfiInvalidData("签名只有总线填充值")
        return signature

    def _read_byte(self, register: int) -> int:
        return self._read_register(register, 1)[0]

    def _read_u16(self, register: int) -> int:
        return int.from_bytes(self._read_register(register, 2), "big")

    def _write_u16(self, register: int, value: int) -> None:
        self._write_register(register, value.to_bytes(2, "big"))

    def _read_register(self, register: int, length: int) -> bytes:
        deadline = time.monotonic() + 2.0
        last_error: Exception | None = None
        while True:
            try:
                self.transport.transaction(self.address, bytes((register,)), 0)
                time.sleep(0.015 if 0x30 <= register <= 0x3A else 0.002)
                result = self.transport.transaction(self.address, b"", length)
                if len(result) != length:
                    raise MfiInvalidData(f"寄存器 0x{register:02x} 返回长度错误")
                if all(value == 0xFF for value in result):
                    raise MfiInvalidData(f"寄存器 0x{register:02x} 只返回 0xff")
                return result
            except (MfiError, Ch341Error) as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise MfiError(f"读取寄存器 0x{register:02x} 失败") from last_error
                time.sleep(0.0005)

    def _write_register(self, register: int, data: bytes) -> None:
        request = bytes((register,)) + data
        deadline = time.monotonic() + 2.0
        last_error: Exception | None = None
        while True:
            try:
                # 一次成功写入必须立即返回。芯片开始签名后会暂时 NACK，若在这里
                # 无条件重写启动寄存器，会把“首次已成功、第二次正忙”误判成失败。
                self.transport.transaction(self.address, request, 0)
                return
            except Ch341Error as error:
                last_error = error
                if time.monotonic() >= deadline:
                    raise MfiError(f"写入寄存器 0x{register:02x} 失败") from last_error
                time.sleep(0.0005)


def verify_signature(certificate_bytes: bytes, digest: bytes, signature: bytes) -> None:
    if len(digest) != 20:
        raise MfiInvalidData("Auth 2.0C 本地验签要求 20 字节 SHA-1 digest")
    errors: list[str] = []
    certificates: list[x509.Certificate] = []
    try:
        # Auth 2.0C 的 908 字节证书是 PKCS#7 SignedData，不是裸 X.509 串。
        certificates.extend(pkcs7.load_der_pkcs7_certificates(certificate_bytes))
    except ValueError:
        pass
    if not certificates:
        for encoded in split_der_objects(certificate_bytes):
            try:
                certificates.append(x509.load_der_x509_certificate(encoded))
            except ValueError as error:
                errors.append(type(error).__name__)
    for certificate in certificates:
        try:
            public_key = certificate.public_key()
            if not isinstance(public_key, rsa.RSAPublicKey):
                continue
            public_key.verify(
                signature,
                digest,
                padding.PKCS1v15(),
                utils.Prehashed(hashes.SHA1()),
            )
            return
        except Exception as error:
            errors.append(type(error).__name__)
    raise MfiInvalidData("签名无法通过芯片证书的本地 RSA 校验：" + ",".join(errors[-3:]))


def split_der_objects(data: bytes) -> list[bytes]:
    result: list[bytes] = []
    offset = 0
    while offset < len(data):
        if data[offset] != 0x30 or offset + 2 > len(data):
            raise MfiInvalidData("证书不是有效的 DER 序列")
        first = data[offset + 1]
        if first & 0x80:
            count = first & 0x7F
            if count == 0 or count > 4 or offset + 2 + count > len(data):
                raise MfiInvalidData("证书 DER 长度无效")
            content_length = int.from_bytes(data[offset + 2 : offset + 2 + count], "big")
            header = 2 + count
        else:
            content_length = first
            header = 2
        end = offset + header + content_length
        if end > len(data):
            raise MfiInvalidData("证书 DER 数据不完整")
        result.append(data[offset:end])
        offset = end
    return result


class HardwareManager:
    def __init__(self, session_factory: Callable[[], object] = LibusbSession):
        self.session_factory = session_factory
        self.lock = threading.RLock()
        self.session = None
        self.client: MfiAuthenticationClient | None = None
        self.self_test: SelfTestResult | None = None

    def reset(self) -> SelfTestResult:
        with self.lock:
            self.close()
            try:
                self.session = self.session_factory()
                transport = Ch341I2cTransport(self.session, speed_khz=100)
                candidates: list[tuple[MfiAuthenticationClient, int]] = []
                for address in (0x10, 0x11):
                    candidate = MfiAuthenticationClient(transport, address)
                    try:
                        length = candidate._read_u16(0x30)
                        candidates.append((candidate, length))
                        LOG.info("MFi 地址 0x%02x 证书长度=%d", address, length)
                    except Exception as error:
                        LOG.warning("MFi 地址 0x%02x 探测失败：%s", address, error)
                selected = next((item[0] for item in candidates if 1 <= item[1] <= 1280), None)
                if selected is None:
                    raise MfiInvalidData("0x10/0x11 均未发现带有效证书的 MFi 芯片")
                result = self._run_self_test(selected)
                self.client = selected
                self.self_test = result
                LOG.info("MFi 硬件自检通过：%s", result.public_dict())
                return result
            except Exception:
                self.close()
                raise

    def ensure_ready(self) -> MfiAuthenticationClient:
        with self.lock:
            if self.client is None:
                self.reset()
            return self.client

    def certificate(self) -> bytes:
        with self.lock:
            return self.ensure_ready().read_certificate()

    def protocol_major(self) -> int:
        with self.lock:
            return self.ensure_ready().protocol_major()

    def sign(self, challenge: bytes) -> bytes:
        with self.lock:
            client = self.ensure_ready()
            signature = client.sign_challenge(challenge)
            if len(challenge) == 20:
                verify_signature(client.read_certificate(), challenge, signature)
            return signature

    def _run_self_test(self, client: MfiAuthenticationClient) -> SelfTestResult:
        device_reads = [self._try(client.device_version) for _ in range(5)]
        if 0x05 not in device_reads:
            raise MfiInvalidData(f"寄存器 0x00 未返回 0x05：{device_reads}")
        protocol_reads = [self._try(client.protocol_major) for _ in range(5)]
        if 2 not in protocol_reads:
            raise MfiInvalidData(f"协议主版本未返回 2：{protocol_reads}")
        protocol_minor = client.protocol_minor()
        device_id = client.device_id()
        if device_id != b"\x00\x00\x02\x00":
            raise MfiInvalidData(f"寄存器 0x04 返回错误：{device_id.hex()}")
        status = client.run_self_test()
        if status & 0xC0 != 0xC0:
            raise MfiInvalidData(f"寄存器 0x40 自检失败：0x{status:02x}")
        certificate = client.read_certificate()
        challenge = bytes(range(1, 21))
        signature = client.sign_challenge(challenge)
        verify_signature(certificate, challenge, signature)
        return SelfTestResult(
            address=f"0x{client.address:02x}",
            device_version_reads=device_reads,
            protocol_major_reads=protocol_reads,
            protocol_major=2,
            protocol_minor=protocol_minor,
            device_id=device_id.hex(),
            self_test_status=f"0x{status:02x}",
            certificate_bytes=len(certificate),
            signature_bytes=len(signature),
        )

    @staticmethod
    def _try(operation: Callable[[], int]) -> int | None:
        try:
            return operation()
        except Exception:
            return None

    def close(self) -> None:
        self.client = None
        self.self_test = None
        if self.session is not None:
            self.session.close()
            self.session = None
