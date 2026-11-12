"""A PNG writer for the pictures the modes receive (SSTV), with only the standard library."""

import struct
import zlib

import numpy as np


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode(image: np.ndarray) -> bytes:
    """An RGB (height x width x 3) or grey (height x width) uint8 image as PNG bytes."""
    image = np.ascontiguousarray(image, dtype=np.uint8)
    height, width = image.shape[:2]
    colour = 2 if image.ndim == 3 else 0
    rows = image.reshape(height, -1)
    raw = b"".join(b"\x00" + row.tobytes() for row in rows)
    header = struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")
    )
