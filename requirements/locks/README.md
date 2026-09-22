# Platform locks

Release builds require one fully resolved, SHA-256 hashed lock per target:

- `windows-cpu-x64.txt`
- `windows-cuda-x64.txt`
- `macos-arm64.txt`

These files are intentionally absent from the source bootstrap. Generate each
lock on its target platform only after the unit tests and real Qwen ASR + Forced
Aligner smoke test pass with the selected PyTorch wheel. A release lock must pin
every transitive dependency and every artifact hash. Build scripts invoke pip
with `--require-hashes`, so an incomplete or floating lock fails closed.

Do not copy a lock between CPU, CUDA, and MPS builds. The Windows CUDA lock must
resolve a PyTorch wheel that bundles its CUDA runtime; users must never install a
CUDA Toolkit or cuDNN separately.

Every regenerated lock must include `jieba==0.42.1`, `wcwidth==0.2.13`,
`modelscope-hub>=0.1.8`, `omnivad==0.2.13`, `torchaudio==2.9.0`, and all of
their transitive dependencies. TorchAudio must match the pinned `torch==2.9.0`
ABI. The lock's wheel hash covers OmniVAD's native library and bundled VAD
model. The macOS and Windows PyInstaller builds collect these packages from
their private Runtime environments; never install them into Resolve's or the
system's Python.
