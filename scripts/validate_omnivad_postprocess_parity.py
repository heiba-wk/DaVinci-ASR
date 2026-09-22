from __future__ import annotations

# ruff: noqa: E402

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from omnivad import OmniVAD as OmniVADModel

from runtime.audio.vad import (
    _omnivad_parameters,
    postprocess_omnivad_probabilities,
)
from scripts.benchmark_multilingual_subtitles import (
    DEFAULT_CORPUS_DEFINITION,
    DEFAULT_CORPUS_DIR,
    validate_corpus,
)


def _native_regions(
    timestamps: list[tuple[float, float]],
    sample_rate: int,
) -> list[tuple[int, int]]:
    return [
        (
            int(round(float(start) * sample_rate)),
            int(round(float(end) * sample_rate)),
        )
        for start, end in timestamps
    ]


def run(args: argparse.Namespace) -> dict[str, Any]:
    corpus, validation = validate_corpus(args.corpus, args.manifest)
    model = OmniVADModel(**_omnivad_parameters())
    rows: list[dict[str, Any]] = []
    for spec in corpus:
        audio, sample_rate = sf.read(spec.audio, dtype="float32")
        if audio.ndim != 1:
            raise ValueError(f"Expected mono audio: {spec.audio}")
        started = time.perf_counter()
        probabilities = np.asarray(
            model.detect_probs(audio, sample_rate=sample_rate),
            dtype=np.float32,
        )
        probability_ms = (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        native = model.detect(audio, sample_rate=sample_rate, chunk_seconds=0)
        native_ms = (time.perf_counter() - started) * 1000.0
        native_regions = _native_regions(native["timestamps"], sample_rate)
        local_regions = [
            (int(region["start"]), int(region["end"]))
            for region in postprocess_omnivad_probabilities(
                probabilities,
                total_samples=len(audio),
                sample_rate=sample_rate,
            )
        ]
        rows.append(
            {
                "language": spec.slug,
                "audio_duration_seconds": spec.duration_seconds,
                "probability_frame_count": int(probabilities.size),
                "detect_probs_ms": round(probability_ms, 3),
                "native_detect_ms": round(native_ms, 3),
                "native_region_count": len(native_regions),
                "local_region_count": len(local_regions),
                "exact": native_regions == local_regions,
                "native_regions": native_regions,
                "local_regions": local_regions,
            }
        )
    report = {
        "schema_version": 1,
        "purpose": (
            "Validate the one-pass Python postprocessor against the bundled "
            "OmniVAD 0.2.13 detect() output; detect() is not used by production."
        ),
        "corpus_validation": validation,
        "audio_count": len(rows),
        "audio_duration_seconds": round(
            sum(float(row["audio_duration_seconds"]) for row in rows),
            6,
        ),
        "all_exact": all(bool(row["exact"]) for row in rows),
        "detect_probs_ms": round(sum(float(row["detect_probs_ms"]) for row in rows), 3),
        "native_detect_ms": round(sum(float(row["native_detect_ms"]) for row in rows), 3),
        "languages": rows,
    }
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# OmniVAD postprocessor parity",
        "",
        f"- Exact on all audio: {report['all_exact']}",
        f"- Audio: {report['audio_count']} / {report['audio_duration_seconds']:.3f}s",
        f"- detect_probs time: {report['detect_probs_ms']:.3f}ms",
        f"- validation-only native detect time: {report['native_detect_ms']:.3f}ms",
        "",
        "| Language | Native regions | Local regions | Exact |",
        "|---|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['language']} | {row['native_region_count']} | "
            f"{row['local_region_count']} | {row['exact']} |"
        )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_CORPUS_DEFINITION)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    report = run(build_parser().parse_args(argv))
    print(
        json.dumps(
            {
                "all_exact": report["all_exact"],
                "audio_count": report["audio_count"],
                "detect_probs_ms": report["detect_probs_ms"],
                "native_detect_ms": report["native_detect_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["all_exact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
