"""Generate a Claude Swap app icon (pure stdlib PNG writer, no deps)."""
import math
import struct
import sys
import zlib

N = 2048  # render size; downsampled by sips afterwards


def lerp(a, b, t):
    return a + (b - a) * t


def rounded_rect_sdf(px, py, cx, cy, hw, hh, r):
    """Signed distance to a rounded rect (negative = inside)."""
    dx = abs(px - cx) - (hw - r)
    dy = abs(py - cy) - (hh - r)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(ax, ay) + min(max(dx, dy), 0.0) - r


def seg_sdf(px, py, x0, y0, x1, y1, half):
    """Signed distance to a thick capsule-less segment (rounded caps off)."""
    vx, vy = x1 - x0, y1 - y0
    wx, wy = px - x0, py - y0
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    return math.hypot(wx - t * vx, wy - t * vy) - half


def tri_sdf(px, py, pts):
    """Signed distance to a triangle (negative = inside)."""
    d = float("inf")
    sign = 1.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        wx, wy = px - x0, py - y0
        t = max(0.0, min(1.0, (wx * ex + wy * ey) / (ex * ex + ey * ey)))
        d = min(d, math.hypot(wx - t * ex, wy - t * ey))
        # winding test
        if (y0 <= py < y1 or y1 <= py < y0) and (
            px < x0 + (py - y0) / (y1 - y0) * ex
        ):
            sign = -sign
    return d * (1.0 if sign > 0 else -1.0)


def arrow_sdf(px, py, y, direction):
    """Horizontal arrow: shaft + head. direction +1 = points right."""
    x_tail, x_tip = 0.215, 0.785
    if direction < 0:
        x_tail, x_tip = x_tip, x_tail
    head = 0.155 * direction          # head length, signed
    x_base = x_tip - head             # where the head starts
    shaft = seg_sdf(px, py, x_tail, y, x_base + 0.02 * direction, y, 0.043)
    tri = tri_sdf(
        px,
        py,
        [(x_tip, y), (x_base, y - 0.098), (x_base, y + 0.098)],
    )
    return min(shaft, tri)


def cov(d, px_size):
    """Antialiased coverage from a signed distance."""
    return max(0.0, min(1.0, 0.5 - d / px_size))


def main(path):
    px_size = 1.0 / N
    # Claude-ish warm palette
    top = (0xE8, 0x8B, 0x62)
    bot = (0xC4, 0x5A, 0x38)

    rows = []
    for j in range(N):
        py = (j + 0.5) / N
        t = py
        br = int(lerp(top[0], bot[0], t))
        bg = int(lerp(top[1], bot[1], t))
        bb = int(lerp(top[2], bot[2], t))
        row = bytearray()
        row.append(0)  # filter type: none
        for i in range(N):
            px = (i + 0.5) / N
            # macOS-style squircle-ish rounded rect with margin
            d_bg = rounded_rect_sdf(px, py, 0.5, 0.5, 0.4395, 0.4395, 0.0985)
            a_bg = cov(d_bg, px_size)
            if a_bg <= 0.0:
                row += b"\x00\x00\x00\x00"
                continue
            d_a = min(
                arrow_sdf(px, py, 0.355, +1),
                arrow_sdf(px, py, 0.645, -1),
            )
            a_fg = cov(d_a, px_size)
            r = int(lerp(br, 255, a_fg))
            g = int(lerp(bg, 255, a_fg))
            b = int(lerp(bb, 255, a_fg))
            row += bytes((r, g, b, int(a_bg * 255)))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", N, N, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    print(f"wrote {path} ({len(png)} bytes, {N}x{N})")


if __name__ == "__main__":
    main(sys.argv[1])
