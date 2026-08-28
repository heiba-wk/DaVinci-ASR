from __future__ import annotations

from pathlib import Path

from runtime.core.types import SubtitleBlock


def format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000.0)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def render_srt(blocks: list[SubtitleBlock]) -> str:
    parts: list[str] = []
    for index, block in enumerate(blocks, 1):
        text = block.text.replace("\r\n", "\n").replace("\r", "\n").strip()
        parts.append(
            f"{index}\n{format_timestamp(block.start)} --> {format_timestamp(block.end)}\n{text}\n"
        )
    return "\n".join(parts)


def write_srt(path: str | Path, blocks: list[SubtitleBlock]) -> Path:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(render_srt(blocks))
        stream.flush()
    temporary.replace(output)
    return output
