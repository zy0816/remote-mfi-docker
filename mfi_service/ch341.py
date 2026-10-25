from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol

try:
    import usb.core
    import usb.util
except ImportError:  # 允许在无 USB 依赖的开发机上运行纯协议测试。
    usb = None


class Ch341Error(RuntimeError):
    pass


class Ch341Unavailable(Ch341Error):
    pass


class Ch341ProtocolError(Ch341Error):
    pass


class UsbSession(Protocol):
    def write(self, data: bytes, timeout_ms: int) -> None: ...
    def read_at_most(
        self,
        maximum: int,
        first_packet_ms: int,
        quiet_ms: int,
        allow_empty: bool,
    ) -> bytes: ...
    def close(self) -> None: ...


class LibusbSession:
    def __init__(self, vendor_id: int = 0x1A86, product_id: int = 0x5512):
        if usb is None:
            raise Ch341Unavailable("缺少 pyusb/libusb 运行环境")
        device = usb.core.find(idVendor=vendor_id, idProduct=product_id)
        if device is None:
            raise Ch341Unavailable(f"找不到 CH341 USB 设备 {vendor_id:04x}:{product_id:04x}")
        self.device = device
        self.interface = None
        self.input_endpoint = None
        self.output_endpoint = None
        self.detached_kernel_driver = False
        try:
            configuration = device[0]
            for interface in configuration:
                input_endpoint = None
                output_endpoint = None
                for endpoint in interface:
                    if usb.util.endpoint_type(endpoint.bmAttributes) != usb.util.ENDPOINT_TYPE_BULK:
                        continue
                    if usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN:
                        input_endpoint = input_endpoint or endpoint
                    else:
                        output_endpoint = output_endpoint or endpoint
                if input_endpoint is not None and output_endpoint is not None:
                    self.interface = interface
                    self.input_endpoint = input_endpoint
                    self.output_endpoint = output_endpoint
                    break
            if self.interface is None:
                raise Ch341Unavailable("CH341 没有同时具备 Bulk IN/OUT 的 USB 接口")
            interface_number = self.interface.bInterfaceNumber
            try:
                if device.is_kernel_driver_active(interface_number):
                    device.detach_kernel_driver(interface_number)
                    self.detached_kernel_driver = True
            except (NotImplementedError, usb.core.USBError):
                pass
            device.set_configuration(configuration.bConfigurationValue)
            usb.util.claim_interface(device, interface_number)
        except Exception as error:
            usb.util.dispose_resources(device)
            if isinstance(error, Ch341Error):
                raise
            raise Ch341Unavailable(f"无法打开 CH341：{error}") from error

    def write(self, data: bytes, timeout_ms: int) -> None:
        try:
            transferred = self.output_endpoint.write(data, timeout=timeout_ms)
        except usb.core.USBError as error:
            self._clear_halt(self.output_endpoint)
            raise Ch341Unavailable(f"CH341 USB 写入失败：{error}") from error
        if transferred != len(data):
            raise Ch341ProtocolError(f"CH341 只写入 {transferred}/{len(data)} 字节")

    def read_at_most(
        self,
        maximum: int,
        first_packet_ms: int,
        quiet_ms: int,
        allow_empty: bool,
    ) -> bytes:
        if maximum <= 0:
            return b""
        packet_size = min(max(int(self.input_endpoint.wMaxPacketSize), 1), 32)
        result = bytearray()
        while len(result) < maximum:
            request_size = min(maximum - len(result), packet_size)
            timeout = first_packet_ms if not result else quiet_ms
            try:
                packet = bytes(self.input_endpoint.read(request_size, timeout=timeout))
            except usb.core.USBTimeoutError as error:
                if result or allow_empty:
                    break
                self._clear_halt(self.input_endpoint)
                raise Ch341Unavailable("CH341 USB 读取超时") from error
            except usb.core.USBError as error:
                if result or allow_empty:
                    break
                self._clear_halt(self.input_endpoint)
                raise Ch341Unavailable(f"CH341 USB 读取失败：{error}") from error
            if not packet:
                break
            result.extend(packet)
            if len(packet) < request_size:
                break
        return bytes(result)

    def _clear_halt(self, endpoint) -> None:
        try:
            self.device.clear_halt(endpoint.bEndpointAddress)
        except usb.core.USBError:
            pass

    def close(self) -> None:
        if self.device is None:
            return
        interface_number = self.interface.bInterfaceNumber
        try:
            usb.util.release_interface(self.device, interface_number)
            if self.detached_kernel_driver:
                self.device.attach_kernel_driver(interface_number)
        except usb.core.USBError:
            pass
        finally:
            usb.util.dispose_resources(self.device)
            self.device = None


class Ch341StreamEncoder:
    STREAM_START = 0xAA
    START = 0x74
    WRITE = 0x80
    READ = 0xC0
    STOP = 0x75
    END = 0x00
    PACKET_BYTES = 32

    @classmethod
    def configuration(cls, speed_khz: int) -> bytes:
        commands = {20: 0x60, 100: 0x61, 400: 0x62, 750: 0x63}
        if speed_khz not in commands:
            raise ValueError("CH341 I2C 速度必须是 20、100、400 或 750 kHz")
        return bytes((cls.STREAM_START, commands[speed_khz], cls.END))

    @classmethod
    def transaction(cls, address: int, write_data: bytes, read_length: int) -> bytes:
        if not 0 <= address <= 0x7F:
            raise ValueError("I2C 地址必须是 7 位值")
        if read_length < 0 or (not write_data and read_length == 0):
            raise ValueError("I2C 事务必须包含读取或写入")
        legacy = cls._legacy(address, write_data, read_length)
        if legacy is not None:
            return legacy

        segments: list[bytes] = []
        segment = bytearray((cls.STREAM_START,))

        def finish_intermediate() -> None:
            nonlocal segment
            segment.append(cls.END)
            segment.extend(bytes(cls.PACKET_BYTES - len(segment)))
            segments.append(bytes(segment))
            segment = bytearray((cls.STREAM_START,))

        def command(data: bytes) -> None:
            if len(segment) + len(data) + 1 > cls.PACKET_BYTES:
                finish_intermediate()
            segment.extend(data)

        def start_and_write(address_byte: int, data: bytes) -> None:
            if len(segment) + 4 > cls.PACKET_BYTES:
                finish_intermediate()
            segment.append(cls.START)
            if not data:
                command(bytes((cls.WRITE + 1, address_byte)))
                return
            command(bytes((cls.WRITE, address_byte)))
            for value in data:
                command(bytes((cls.WRITE, value)))

        if write_data:
            start_and_write(address << 1, write_data)
        if read_length:
            start_and_write((address << 1) | 1, b"")
            remaining = read_length
            while remaining > 32:
                command(bytes((cls.READ + 32,)))
                finish_intermediate()
                remaining -= 32
            if remaining > 1:
                command(bytes((cls.READ + remaining - 1,)))
            command(bytes((cls.READ,)))
        if len(segment) + 2 > cls.PACKET_BYTES:
            finish_intermediate()
        segment.extend((cls.STOP, cls.END))
        segments.append(bytes(segment))
        return b"".join(segments)

    @classmethod
    def _legacy(cls, address: int, write_data: bytes, read_length: int) -> bytes | None:
        if read_length > 32 or len(write_data) + 1 > 0x3F:
            return None
        stream = bytearray((cls.STREAM_START,))
        if write_data:
            stream.append(cls.START)
            for value in bytes((address << 1,)) + write_data:
                stream.extend((cls.WRITE, value))
        if read_length:
            stream.append(cls.START)
            stream.extend((cls.WRITE, (address << 1) | 1))
            if read_length > 1:
                stream.append(cls.READ + read_length - 1)
            stream.append(cls.READ)
        stream.extend((cls.STOP, cls.END))
        return bytes(stream) if len(stream) <= cls.PACKET_BYTES else None


@dataclass(frozen=True)
class InstructionLengths:
    written: int
    data: int


class Ch341I2cTransport:
    def __init__(self, session: UsbSession, speed_khz: int = 100, timeout_ms: int = 1000):
        self.session = session
        self.speed_khz = speed_khz
        self.timeout_ms = timeout_ms
        self.lock = threading.RLock()

    def transaction(self, address: int, write_data: bytes, read_length: int) -> bytes:
        stream = Ch341StreamEncoder.transaction(address, write_data, read_length)
        with self.lock:
            self.session.write(Ch341StreamEncoder.configuration(self.speed_khz), self.timeout_ms)
            if not write_data and read_length:
                time.sleep(0.005)
            response = bytearray()
            for offset in range(0, len(stream), 32):
                segment = stream[offset : offset + 32]
                self.session.write(segment, self.timeout_ms)
                lengths = self._instruction_lengths(segment)
                maximum = lengths.written + lengths.data
                if maximum == 0:
                    continue
                received = self.session.read_at_most(
                    maximum,
                    self.timeout_ms if lengths.data else min(25, self.timeout_ms),
                    25,
                    lengths.data == 0,
                )
                if len(received) < lengths.data:
                    raise Ch341Unavailable(
                        f"CH341 返回 {len(received)} 字节，预期至少 {lengths.data} 字节数据"
                    )
                data_start = len(received) - lengths.data
                if any(value & 0x80 for value in received[:data_start]):
                    raise Ch341ProtocolError("CH341 报告 I2C NACK")
                response.extend(received[data_start:])
            if len(response) != read_length:
                raise Ch341ProtocolError(f"I2C 返回 {len(response)} 字节，预期 {read_length} 字节")
            return bytes(response)

    @staticmethod
    def _instruction_lengths(segment: bytes) -> InstructionLengths:
        index = 1
        written = 0
        data = 0
        while index < len(segment):
            command = segment[index]
            index += 1
            if command == 0:
                break
            if command in (0x74, 0x75) or 0x60 <= command <= 0x63:
                continue
            if 0x80 <= command <= 0xBF:
                length = (command & 0x3F) or 1
                if command & 0x3F == 0:
                    written += length
                index += length
                continue
            if 0xC0 <= command <= 0xFF:
                data += (command & 0x3F) or 1
                continue
            raise Ch341ProtocolError(f"未知 CH341 指令 0x{command:02x}")
        return InstructionLengths(written, data)
