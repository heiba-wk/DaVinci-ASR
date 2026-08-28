from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from fractions import Fraction

from runtime.core.types import SubtitleBlock


@dataclass(frozen=True, slots=True)
class FrameRate:
    numerator: int
    denominator: int

    @property
    def nominal(self) -> int:
        return round(self.numerator / self.denominator)

    @property
    def fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


_DECIMAL_RATES: dict[str, tuple[int, int]] = {
    "23.976": (24_000, 1_001),
    "29.97": (30_000, 1_001),
    "59.94": (60_000, 1_001),
    "24": (24, 1),
    "25": (25, 1),
    "30": (30, 1),
    "50": (50, 1),
    "60": (60, 1),
}


def parse_frame_rate(value: str | int | float) -> FrameRate:
    text = str(value).strip().upper().replace(" DF", "")
    if text in _DECIMAL_RATES:
        numerator, denominator = _DECIMAL_RATES[text]
        return FrameRate(numerator, denominator)
    if "/" in text:
        fraction = Fraction(text)
        allowed = {(24_000, 1_001), (30_000, 1_001), (60_000, 1_001)}
        if (fraction.numerator, fraction.denominator) not in allowed:
            raise ValueError(f"Unsupported timeline frame rate: {value}")
        return FrameRate(fraction.numerator, fraction.denominator)
    raise ValueError(f"Unsupported timeline frame rate: {value}")


def _frame(value: float, rate: FrameRate, rounding: str) -> int:
    exact = Decimal(str(value)) * Decimal(rate.numerator) / Decimal(rate.denominator)
    return int(exact.to_integral_value(rounding=rounding))


def quantize_blocks(
    blocks: list[SubtitleBlock],
    fps: str | int | float,
    *,
    start_frame: int = 0,
    remove_gaps: bool = False,
) -> list[SubtitleBlock]:
    rate = parse_frame_rate(fps)
    output: list[SubtitleBlock] = []
    previous_end: int | None = None
    for block in blocks:
        start = start_frame + _frame(block.start, rate, ROUND_FLOOR)
        end = start_frame + _frame(block.end, rate, ROUND_CEILING)
        if previous_end is not None and start < previous_end:
            start = previous_end
        end = max(end, start + 1)
        relative_start = Fraction(start - start_frame, 1) / rate.fraction
        relative_end = Fraction(end - start_frame, 1) / rate.fraction
        output.append(SubtitleBlock(float(relative_start), float(relative_end), block.text))
        previous_end = end
    if remove_gaps:
        for index in range(len(output) - 1):
            output[index].end = output[index + 1].start
    return output
