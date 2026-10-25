import unittest

from mfi_service.ch341 import Ch341StreamEncoder


class Ch341StreamEncoderTest(unittest.TestCase):
    def test_write_read_matches_android_stream(self):
        self.assertEqual(
            bytes((0xAA, 0x74, 0x80, 0x22, 0x80, 0x30, 0x74, 0x80, 0x23, 0xC1, 0xC0, 0x75, 0x00)),
            Ch341StreamEncoder.transaction(0x11, b"\x30", 2),
        )

    def test_segmented_read_uses_batched_read_address(self):
        stream = Ch341StreamEncoder.transaction(0x11, b"", 65_535)
        self.assertEqual(bytes((0xAA, 0x74, 0x81, 0x23)), stream[:4])
        self.assertEqual(bytes((0xDE, 0xC0, 0x75, 0x00)), stream[-4:])
        self.assertEqual(1, stream.count(0x75))
        self.assertGreater(len(stream), 32)
        for offset in range(0, len(stream) - 32, 32):
            self.assertEqual(0xAA, stream[offset])
            self.assertEqual(0, stream[offset + 31])


if __name__ == "__main__":
    unittest.main()
