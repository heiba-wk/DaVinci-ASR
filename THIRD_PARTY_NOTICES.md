# Third-party notices

DaVinci ASR is licensed under Apache-2.0. Release builders must retain this
file and the license files emitted by the hashed dependency lock.

## Qwen3-ASR

- Project: <https://github.com/QwenLM/Qwen3-ASR>
- Copyright: The Alibaba Qwen team
- License: Apache License 2.0
- Use in this project: technical behavior reference only; no `qwen_asr` Python
  package is imported by the Runtime.

## Qwen3-ASR-0.6B-hf

- Model: <https://huggingface.co/Qwen/Qwen3-ASR-0.6B-hf>
- Fixed revision: `7f1569a48a89f3e3f4dc3a5c9d28bddd903bc76c`
- License: Apache License 2.0
- Model weights remain outside both the Git source repository and release
  installers; separately supplied files are verified by the model manifest.

## Qwen3-ASR-1.7B-hf

- Model: <https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf>
- Fixed revision: `bcd2b5b7f32b480ab5790554cfa8347f246a14f3`
- License: Apache License 2.0
- Model weights remain outside both the Git source repository and release
  installers; separately supplied files are verified by the model manifest.

## Qwen3-ForcedAligner-0.6B-hf

- Model: <https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B-hf>
- Fixed revision: `c07281df297b9905d24a508279258cccf987a064`
- License: Apache License 2.0
- Model weights remain outside both the Git source repository and release
  installers; separately supplied files are verified by the model manifest.

## Silero VAD

- Project: <https://github.com/snakers4/silero-vad>
- Package: `silero-vad==6.2.1`
- Copyright: Silero Team
- License: MIT
- Use in this project: offline speech-region detection. The pip wheel supplies
  the JIT weight; release locks verify the wheel hash and Runtime never uses
  `torch.hub` to fetch VAD code or weights.

## Hugging Face Transformers

- Project: <https://github.com/huggingface/transformers>
- Fixed source revision: `c7f9c8815610d27e41a6b0b0cc9e2d3c49468d1d`
- License: Apache License 2.0
- This pinned revision supplies Native Qwen3 ASR, Forced Alignment, and the
  `prompt`/hotword form of `apply_transcription_request()`.

## ModelScope Hub

- Project: <https://github.com/modelscope/modelscope_hub>
- Minimum version: `0.1.8`
- License: Apache License 2.0
- Used only by the private Runtime as the China-first repair download source
  for the supported Qwen3 ASR models; inference remains local and
  source-independent.

## nagisa

- Project: <https://github.com/taishi-i/nagisa>
- Version: `0.2.11`
- License: MIT
- Used by the Native Transformers processor for Japanese alignment units.

## jieba

- Project: <https://github.com/fxsjy/jieba>
- Version: `0.42.1`
- License: MIT
- Used locally to protect Mandarin and Cantonese lexical word boundaries while
  planning display-unit-limited subtitles. It does not alter or upload text.

## wcwidth

- Project: <https://github.com/jquast/wcwidth>
- Version: `0.2.13`
- License: MIT
- Used locally to measure Unicode subtitle display width, including wide and
  combining characters. It does not alter or upload text.

The GPLv3 `soynlp` package is intentionally not bundled. Korean alignment uses
the Native processor's Unicode/whitespace path to create Korean eojeol units.
This preserves the Apache-2.0 distribution boundary and does not treat Korean
text as English word tokens.
