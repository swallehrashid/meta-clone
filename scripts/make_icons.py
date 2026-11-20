"""Generate the PWA icons (pure stdlib - no Pillow). Run: python scripts/make_icons.py"""
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web" / "public" / "icons"
BG, UP, DOWN, WICK = (0, 0, 0), (46, 189, 107), (229, 83, 75), (150, 150, 150)


def png(size, pixels):
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def draw(size, scale):
    """Three candles; `scale` shrinks the artwork toward the centre (maskable safe zone)."""
    px = [[BG] * size for _ in range(size)]
    def rect(x0, y0, x1, y1, col):
        for y in range(max(0, int(y0)), min(size, int(y1))):
            for x in range(max(0, int(x0)), min(size, int(x1))):
                px[y][x] = col
    u = size * scale / 100.0                 # artwork unit (100 units wide)
    ox = oy = size * (1 - scale) / 2
    # (x centre, wick top, wick bottom, body top, body bottom, colour) in artwork units
    for cx, wt, wb, bt, bb, col in [(22, 38, 90, 50, 80, DOWN), (50, 14, 78, 22, 64, UP), (78, 8, 60, 14, 46, UP)]:
        rect(ox + (cx - 2) * u / 1.0 * 1, oy + wt * u, ox + (cx + 2) * u, oy + wb * u, WICK)
        rect(ox + (cx - 9) * u, oy + bt * u, ox + (cx + 9) * u, oy + bb * u, col)
    return px


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, scale in [("icon-192.png", 192, 0.82), ("icon-512.png", 512, 0.82), ("maskable-512.png", 512, 0.56)]:
        (OUT / name).write_bytes(png(size, draw(size, scale)))
        print("wrote", OUT / name)
