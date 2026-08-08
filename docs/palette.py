"""Measure the committed palette, so docs/design.md states numbers rather than adjectives.

The design document makes four claims that are arithmetic: which series colours clear 3:1
against which surface, which inks are legal on which surface, how far apart two adjacent
slots of the categorical order are once a dichromat's eye has been through them, and how
far apart the worst pair in the whole order is. Those are the claims that decide whether a
ninth token may be added and where a new series colour may go, so they are measured here
rather than remembered.

Nothing here reaches a network, and it imports nothing that is not in the standard library:
it reads `web/src/styles/tokens.css`, which is the file the numbers are about, so a token
edited without re-reading this output is a document that has gone stale visibly.

    .venv/bin/python docs/palette.py

WCAG 2.1 relative luminance and contrast are the published formulae. The dichromacy
simulation is Viénot, Brettel and Mollon (1999), the one-plane projection in LMS that the
accessibility tooling in this space generally uses; it models the three dichromacies and
not the anomalous trichromacies, which are commoner and milder. Separation is reported as
CIE76 dE, which is crude next to CIEDE2000 and is the right kind of crude here: it is being
asked whether two colours are obviously different, not whether they are subtly different.
"""

import math
import re
from pathlib import Path

TOKENS = Path(__file__).resolve().parent.parent / "web" / "src" / "styles" / "tokens.css"

# The surfaces something can be drawn on, and the inks and status colours that get drawn on
# them. Named rather than derived, because which token is a surface is a design decision and
# not something a stylesheet says out loud.
SURFACES = ("surf", "surf-2", "canvas", "chrome", "chrome-2", "chrome-3")
INKS = (
    "ink", "ink-2", "ink-3", "chrome-ink", "chrome-ink-2", "brass",
    "good", "warning", "serious", "bad",
)


def tokens() -> dict[str, str]:
    """Every `--name: #rrggbb` in the stylesheet. The stylesheet is the source; this file
    holds no copy of a colour, so there is no second place for one to go stale."""
    text = TOKENS.read_text(encoding="utf8")
    return {
        name: value.lower()
        for name, value in re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;", text)
    }


def rgb(colour: str) -> tuple[float, float, float]:
    value = colour.lstrip("#")
    return tuple(int(value[at : at + 2], 16) / 255 for at in (0, 2, 4))  # type: ignore[return-value]


def _linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def luminance(colour: str) -> float:
    red, green, blue = (_linear(channel) for channel in rgb(colour))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(one: str, two: str) -> float:
    """WCAG 2.1 contrast ratio, 1 to 21."""
    first, second = luminance(one), luminance(two)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


# Viénot, Brettel and Mollon (1999): to LMS, flatten the axis the missing cone carried, back.
_TO_LMS = (
    (0.31399022, 0.63951294, 0.04649755),
    (0.15537241, 0.75789446, 0.08670142),
    (0.01775239, 0.10944209, 0.87256922),
)
_FROM_LMS = (
    (5.47221206, -4.64196010, 0.16963708),
    (-1.12524190, 2.29317094, -0.16789520),
    (0.02980165, -0.19318073, 1.16364789),
)
_FLATTEN = {
    "protan": ((0.0, 1.05118294, -0.05116099), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "deutan": ((1.0, 0.0, 0.0), (0.9513092, 0.0, 0.04866992), (0.0, 0.0, 1.0)),
    "tritan": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-0.86744736, 1.86727089, 0.0)),
}
VISIONS = ("normal", "protan", "deutan", "tritan")


def _apply(matrix, vector):
    return [sum(row[at] * vector[at] for at in range(3)) for row in matrix]


def seen(colour: str, vision: str) -> tuple[float, float, float]:
    """The colour as one of the three dichromacies receives it, in linear RGB."""
    linear = [_linear(channel) for channel in rgb(colour)]
    if vision == "normal":
        return tuple(linear)  # type: ignore[return-value]
    return tuple(_apply(_FROM_LMS, _apply(_FLATTEN[vision], _apply(_TO_LMS, linear))))  # type: ignore[return-value]


def _lab(linear: tuple[float, float, float]) -> tuple[float, float, float]:
    red, green, blue = (min(1.0, max(0.0, channel)) for channel in linear)
    x = 0.4124 * red + 0.3576 * green + 0.1805 * blue
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = 0.0193 * red + 0.1192 * green + 0.9505 * blue

    def f(ratio: float) -> float:
        return ratio ** (1 / 3) if ratio > 0.008856 else 7.787 * ratio + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y / 1.0), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def separation(one: str, two: str, vision: str) -> float:
    """CIE76 dE between two colours as that vision receives them."""
    first, second = _lab(seen(one, vision)), _lab(seen(two, vision))
    return math.sqrt(sum((first[at] - second[at]) ** 2 for at in range(3)))


def series(palette: dict[str, str]) -> list[str]:
    return [palette[f"series-{slot}"] for slot in range(1, 9) if f"series-{slot}" in palette]


def report() -> str:
    palette = tokens()
    order = series(palette)
    lines: list[str] = []
    write = lines.append

    write("Series colours against every surface (WCAG contrast ratio)")
    write("")
    write("| slot | hex | " + " | ".join(f"--{name}" for name in SURFACES) + " |")
    write("|---|---|" + "---|" * len(SURFACES))
    for slot, colour in enumerate(order, 1):
        ratios = " | ".join(f"{contrast(colour, palette[name]):.2f}" for name in SURFACES)
        write(f"| {slot} | `{colour}` | {ratios} |")

    write("")
    write("Ink and status against the two surfaces they are worn on")
    write("")
    write("| token | hex | on --surf | on --chrome |")
    write("|---|---|---|---|")
    for name in INKS:
        colour = palette[name]
        write(
            f"| `--{name}` | `{colour}` | {contrast(colour, palette['surf']):.2f} "
            f"| {contrast(colour, palette['chrome']):.2f} |"
        )

    write("")
    write("Adjacent slots of the order, separated (CIE76 dE)")
    write("")
    write("| pair | " + " | ".join(VISIONS) + " |")
    write("|---|" + "---|" * len(VISIONS))
    for at in range(len(order) - 1):
        one, two = order[at], order[at + 1]
        measured = " | ".join(f"{separation(one, two, vision):.0f}" for vision in VISIONS)
        write(f"| {at + 1}–{at + 2} | {measured} |")

    write("")
    write("The worst pair anywhere in the order, per vision")
    write("")
    write("| vision | dE | slots |")
    write("|---|---|---|")
    for vision in VISIONS:
        least, where = math.inf, (0, 0)
        for first in range(len(order)):
            for second in range(first + 1, len(order)):
                measured = separation(order[first], order[second], vision)
                if measured < least:
                    least, where = measured, (first + 1, second + 1)
        write(f"| {vision} | {least:.0f} | {where[0]} and {where[1]} |")

    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
