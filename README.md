# DaVinci ASR

[简体中文](README_CN.md)

DaVinci ASR is a local AI subtitle tool for DaVinci Resolve. It reads the audio on the current timeline, creates timed subtitles, and imports them back into Resolve.

## Models used

- Speech recognition: `Qwen/Qwen3-ASR-0.6B-hf`
- Forced alignment: `Qwen/Qwen3-ForcedAligner-0.6B-hf`

Both models are downloaded together from the plugin's model download dialog. The first download requires approximately 3.4 GB of disk space.

## macOS installation

The macOS installer is available from the [official plugin download page](https://www.heibagen.com/plugins).

1. Download and open the `.pkg` installer.
2. Follow the installer, then reopen DaVinci Resolve.
3. Open `DaVinci ASR` from `Workspace > Scripts > Utility`.

## Windows source installation

Windows currently requires a source installation. Prepare:

- DaVinci Resolve
- 64-bit Windows
- Python 3.12
- The project source and a working network connection

### 1. Get the source

```bash
git clone https://github.com/heiba-wk/DaVinci-ASR.git
cd DaVinci-ASR
```

You can also download and extract a ZIP archive from the GitHub **Code** menu.

### 2. Install the Runtime dependencies

Extract the source into a stable folder, for example `C:\HEIBA\DaVinci-ASR`. Open PowerShell in that folder and run:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements\direct.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

### 3. Install the Resolve plugin

Copy the complete `DaVinci ASR` folder from the source into the `Utility` directory below. The final path should be:

```text
%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\DaVinci ASR
```

Do not copy only `DaVinci ASR.lua`. The first model download and audio processing write to the `models` and `audio_temp` folders, so the current Windows user needs modify permission for the complete `DaVinci ASR` folder.

### 4. Select the source Runtime

Still in PowerShell at the source root, run:

```powershell
$runtime = (Resolve-Path ".\.venv\Scripts\DaVinciASRRuntime.exe").Path
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $runtime, "User")
```

Do not move the source folder after this step. Completely quit and reopen DaVinci Resolve, then open `DaVinci ASR` from `Workspace > Scripts > Utility`.

## How to use

1. Open a Resolve project and timeline containing audio, then open `DaVinci ASR`.
2. The first time, click `Download Models`, choose `ModelScope` or `Hugging Face`, and wait until the models are ready. ModelScope is recommended in mainland China.
3. Select a language and click `Create Subtitles`. You can optionally enter names or terms in the phrases/prompt field.
4. The generated subtitles are imported into the current Resolve project.

The models only need to be downloaded once. Later uses reuse the local files.

## Troubleshooting

### DaVinci ASR is not listed in the menu

Check the plugin folder hierarchy, fully restart Resolve, and confirm that `DAVINCI_ASR_RUNTIME` points to an existing `DaVinciASRRuntime.exe`.

### The Runtime is missing or fails to start

Return to the source folder, run the dependency installation commands again, confirm that `.venv\Scripts\DaVinciASRRuntime.exe` exists, reset the environment variable, and restart Resolve.

### Model download fails

Check the network connection, free disk space, and write permission for the `models` folder. Try ModelScope in mainland China and Hugging Face elsewhere.

## Privacy and license

Audio transcription, alignment, and subtitle generation run locally. Model downloads only access the source selected in the UI.

Licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This repository contains the public release of the project.
