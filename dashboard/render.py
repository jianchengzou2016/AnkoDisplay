"""Draw simple, crisp RGB weather icons for a 16x16 LED matrix."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from bus import BusArrival


SIZE = (16, 16)
BLACK = (0, 0, 0)

FONT_3X5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
    "+": ("000", "010", "111", "010", "000"),
    # A 5-pixel-wide lowercase m keeps its two arches distinct from an n.
    "m": ("00000", "11111", "10101", "10101", "10101"),
    "C": ("111", "100", "100", "100", "111"),
    "B": ("110", "101", "110", "101", "110"),
}


def _draw_3x5(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, color: tuple[int, int, int], gap: int = 1) -> int:
    cursor = x
    for char in text:
        glyph = FONT_3X5[char]
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    draw.point((cursor + column, y + row), fill=color)
        cursor += len(glyph[0]) + gap
    return cursor - gap


def _text_width(text: str, gap: int = 1) -> int:
    return sum(len(FONT_3X5[char][0]) for char in text) + gap * max(0, len(text) - 1)


def render_icon(condition: str, output_path: Path) -> Path:
    image = Image.new("RGB", SIZE, BLACK)
    draw = ImageDraw.Draw(image)

    if condition == "sunny":
        yellow = (255, 205, 35)
        for point in ((7, 0), (7, 1), (7, 14), (7, 15), (0, 7), (1, 7), (14, 7), (15, 7),
                      (2, 2), (3, 3), (12, 12), (13, 13), (12, 3), (13, 2), (2, 12), (3, 13)):
            draw.point(point, fill=yellow)
        draw.ellipse((4, 4, 11, 11), fill=yellow)
    elif condition == "cloudy":
        grey = (195, 210, 220)
        draw.ellipse((2, 5, 9, 12), fill=grey)
        draw.ellipse((5, 3, 12, 11), fill=grey)
        draw.ellipse((8, 6, 14, 12), fill=grey)
        draw.rectangle((4, 8, 12, 12), fill=grey)
    elif condition == "rain":
        grey, blue = (185, 205, 220), (55, 165, 255)
        draw.ellipse((2, 3, 9, 10), fill=grey)
        draw.ellipse((5, 1, 12, 9), fill=grey)
        draw.ellipse((8, 4, 14, 10), fill=grey)
        draw.rectangle((4, 6, 12, 10), fill=grey)
        for point in ((4, 12), (8, 14), (12, 12), (6, 15), (10, 13)):
            draw.point(point, fill=blue)
    elif condition == "thunderstorm":
        grey, yellow = (175, 195, 210), (255, 220, 30)
        draw.ellipse((2, 2, 9, 9), fill=grey)
        draw.ellipse((5, 1, 12, 9), fill=grey)
        draw.ellipse((8, 4, 14, 10), fill=grey)
        draw.rectangle((4, 6, 12, 10), fill=grey)
        draw.polygon(((8, 8), (5, 12), (8, 12), (6, 15), (11, 10), (8, 10)), fill=yellow)
    else:
        raise ValueError(f"Unsupported weather condition: {condition}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path


def render_bus_status(arrival: BusArrival, output_path: Path) -> Path:
    """Draw the next route 150 arrival using a compact, legible 16x16 layout."""
    image = Image.new("RGB", SIZE, BLACK)
    draw = ImageDraw.Draw(image)
    route_color = (255, 220, 50)
    eta_color = (255, 255, 255)
    direction_color = (70, 190, 255)
    status_color = {
        "realtime": (40, 230, 100),
        "scheduled": (255, 170, 35),
        "unknown": (240, 65, 65),
    }.get(arrival.source, (240, 65, 65))

    # The C/B glyph identifies the destination side; the single pixel is live,
    # timetable, or unavailable status. The larger lower row shows the ETA.
    _draw_3x5(draw, arrival.route[-3:], 0, 1, route_color)
    glyph = "C" if arrival.direction == "inbound" else "B"
    _draw_3x5(draw, glyph, 12, 1, direction_color, gap=0)
    draw.point((15, 5), fill=status_color)

    eta = arrival.eta_minutes
    gap = 1
    if eta is None:
        value = "--m"
    elif eta >= 100:
        # Four compact glyphs fit at 14 pixels when touching: 99+m means
        # the ETA is at least 100 minutes, without suggesting it is exactly 99.
        value = "99+m"
        gap = 0
    else:
        value = f"{eta}m"
    width = _text_width(value, gap=gap)
    _draw_3x5(draw, value, max(0, (16 - width) // 2), 9, eta_color, gap=gap)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path
