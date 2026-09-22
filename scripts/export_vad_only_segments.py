from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dbfs(rms: float) -> float | None:
    if rms <= 0.0:
        return None
    return 20.0 * math.log10(rms)


def export(args: argparse.Namespace) -> dict[str, Any]:
    comparison_path = args.comparison.expanduser().resolve()
    corpus_root = args.corpus.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    clips_root = output_root / "clips"
    clips_root.mkdir(parents=True, exist_ok=True)

    comparison = _load_json(comparison_path)
    rows: list[dict[str, Any]] = []
    for language, metrics in comparison["languages"].items():
        audio_path = corpus_root / f"{language}.wav"
        audio, sample_rate = sf.read(
            audio_path,
            dtype="float32",
            always_2d=False,
        )
        if audio.ndim != 1:
            raise ValueError(f"expected mono audio: {audio_path}")
        total_samples = int(audio.shape[0])
        for index, interval in enumerate(metrics["omnivad_only_regions"], start=1):
            start_seconds = float(interval[0])
            end_seconds = float(interval[1])
            start_sample = max(0, min(total_samples, round(start_seconds * sample_rate)))
            end_sample = max(
                start_sample,
                min(total_samples, round(end_seconds * sample_rate)),
            )
            clip = audio[start_sample:end_sample]
            safe_start = f"{start_sample / sample_rate:.3f}".replace(".", "p")
            safe_end = f"{end_sample / sample_rate:.3f}".replace(".", "p")
            filename = f"{language}-{index:03d}-{safe_start}-{safe_end}.wav"
            clip_path = clips_root / filename
            sf.write(clip_path, clip, sample_rate, subtype="PCM_16")
            rms = float(np.sqrt(np.mean(np.square(clip)))) if clip.size else 0.0
            peak = float(np.max(np.abs(clip))) if clip.size else 0.0
            rows.append(
                {
                    "language": language,
                    "index": index,
                    "source_audio": str(audio_path),
                    "source_audio_sha256": _sha256(audio_path),
                    "start_seconds": round(start_sample / sample_rate, 6),
                    "end_seconds": round(end_sample / sample_rate, 6),
                    "duration_seconds": round((end_sample - start_sample) / sample_rate, 6),
                    "sample_rate": sample_rate,
                    "sample_count": end_sample - start_sample,
                    "rms": round(rms, 8),
                    "rms_dbfs": round(value, 3) if (value := _dbfs(rms)) is not None else None,
                    "peak": round(peak, 8),
                    "clip": str(clip_path),
                    "clip_sha256": _sha256(clip_path),
                }
            )

    csv_path = output_root / "segments.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    per_language: dict[str, dict[str, float | int]] = {}
    for row in rows:
        bucket = per_language.setdefault(
            str(row["language"]),
            {"segment_count": 0, "duration_seconds": 0.0},
        )
        bucket["segment_count"] = int(bucket["segment_count"]) + 1
        bucket["duration_seconds"] = float(bucket["duration_seconds"]) + float(
            row["duration_seconds"]
        )
    for bucket in per_language.values():
        bucket["duration_seconds"] = round(float(bucket["duration_seconds"]), 6)

    report = {
        "schema_version": 1,
        "source_comparison": str(comparison_path),
        "corpus_root": str(corpus_root),
        "segment_count": len(rows),
        "duration_seconds": round(
            sum(float(row["duration_seconds"]) for row in rows),
            6,
        ),
        "label_status": "unlabeled; exported for manual speech/music/silence review",
        "per_language": per_language,
        "segments": rows,
    }
    report_path = output_root / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    readme_lines = [
        "# Omni-only speech coverage clips",
        "",
        f"- Segments: {report['segment_count']}",
        f"- Exported duration: {report['duration_seconds']:.3f} seconds",
        "- Labels: unavailable; these clips are for manual speech/music/silence review.",
        "- Intervals come from the frozen A Current VAD vs B Omni direct comparison.",
        "",
        "| Language | Segments | Duration s |",
        "|---|---:|---:|",
    ]
    for language, bucket in per_language.items():
        readme_lines.append(
            f"| {language} | {bucket['segment_count']} | "
            f"{float(bucket['duration_seconds']):.3f} |"
        )
    (output_root / "README.md").write_text(
        "\n".join(readme_lines) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Omni-only VAD intervals from a frozen comparison report."
    )
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    report = export(parse_args())
    print(
        json.dumps(
            {
                "segment_count": report["segment_count"],
                "duration_seconds": report["duration_seconds"],
                "per_language": report["per_language"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
