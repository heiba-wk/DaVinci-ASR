# DaVinci ASR

[简体中文](README_CN.md)

DaVinci ASR is a local AI subtitle tool for DaVinci Resolve. It reads the audio on the current timeline, creates timed subtitles, and imports them back into Resolve.

## Download and install (macOS and Windows)

Both macOS and Windows installers are available from the [official DaVinci ASR download page](https://www.heibagen.com/plugins/davinci-asr).

1. Download the installer for your system and follow its installation instructions.
2. Completely quit and reopen DaVinci Resolve.
3. Open `DaVinci ASR` from `Workspace > Scripts > Utility`.
4. Follow the steps below to download the models and create your first subtitles.

The installer includes the Runtime. You do not need to install Python or set up a development environment for normal use.

## Models used

- Speech recognition: `Qwen/Qwen3-ASR-0.6B-hf` or `Qwen/Qwen3-ASR-1.7B-hf`
- Forced alignment: `Qwen/Qwen3-ForcedAligner-0.6B-hf`

The plugin downloads the selected speech-recognition model and the shared
Forced Aligner. Switching speech-recognition models does not download the
Forced Aligner again when it is already installed.

## How to use

1. Open a Resolve project and timeline containing audio, then open `DaVinci ASR`.
2. The first time, click `Download Models`, choose `ModelScope` or `Hugging Face`, and wait until the models are ready. ModelScope is recommended in mainland China.
3. For automatic subtitles, select a language and optionally enter names or terms in `Phrases / Prompt`. To use Script Match, check `Script Match`; the main window is hidden while a separate script window is open.
4. Paste the complete, line-broken script and click `Use Script`, or close the script window. Every non-empty input line becomes exactly one final subtitle block. Script Match is enabled only when the text is non-empty; closing an empty window automatically clears the checkbox. Selecting Chinese or English enables direct Forced Aligner timing without ASR when the quality gate passes.
5. Click `Create Subtitles`. Script Match preserves each matched line before optional subtitle post-processing. `Max Characters` is disabled and ignored because the supplied line breaks define the blocks; `No Gaps` and `No End Marks` remain available and are applied after the matched SRT blocks are built. The script is session-only and is cleared when the utility is reopened.
6. The generated subtitles are imported into the current Resolve project.

The models only need to be downloaded once. Later uses reuse the local files.

## Run from source (for developers)

Use this section if you want to modify the plugin. Prepare DaVinci Resolve, **Python 3.12**, and a network connection. Use 64-bit Python on Windows.

### 1. Get the source

```bash
git clone https://github.com/heiba-wk/DaVinci-ASR.git
cd DaVinci-ASR
```

You can also download and extract a ZIP archive from the GitHub **Code** menu, then open a terminal in the extracted project folder.

Keep the source and its virtual environment outside Resolve's `Utility` directory, for example `~/Projects/DaVinci-ASR` on macOS or `C:\HEIBA\DaVinci-ASR` on Windows. Only the `DaVinci ASR` plugin folder goes in `Utility`.

### 2. Install the Runtime dependencies

Run these commands from the source root. They create a separate Python environment and install the Runtime in editable mode, so your Python source changes take effect when the Runtime restarts.

**macOS — Terminal:**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements/direct.txt
.venv/bin/python -m pip install -e .
.venv/bin/DaVinciASRRuntime doctor --cpu
```

**Windows — PowerShell:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements\direct.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\DaVinciASRRuntime.exe doctor --cpu
```

`doctor --cpu` reports the environment and model status. Models can show as not installed at this stage; download them from the plugin later. This command does not run a full transcription test.

### 3. Install the Resolve plugin

Copy the complete `DaVinci ASR` folder from the source to the corresponding location:

| System | Final plugin folder |
| --- | --- |
| macOS | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/DaVinci ASR` |
| Windows | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\DaVinci ASR` |

Do not copy only `DaVinci ASR.lua`. The plugin writes models, temporary audio, and settings beside the Lua entry, so your user account needs write permission for the complete `DaVinci ASR` folder.

### 4. Select the source Runtime

Run the command for your system from the source root. `DAVINCI_ASR_RUNTIME` tells Resolve to use your development Runtime.

**macOS — Terminal:**

```bash
launchctl setenv DAVINCI_ASR_RUNTIME "$(pwd)/.venv/bin/DaVinciASRRuntime"
```

This setting applies to the current login session. Run it again after logging out or restarting macOS.

**Windows — PowerShell:**

```powershell
$runtime = (Resolve-Path ".\.venv\Scripts\DaVinciASRRuntime.exe").Path
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $runtime, "User")
```

Keep the source folder at this location. Completely quit and reopen DaVinci Resolve, then open `DaVinci ASR` from `Workspace > Scripts > Utility`. Download the models in the plugin before creating subtitles.

### 5. Modify and check your changes

| Source | What to change |
| --- | --- |
| `DaVinci ASR/DaVinci ASR.lua` | Resolve UI, timeline audio export, and subtitle import |
| `runtime/` | Model downloads, speech recognition, alignment, and subtitle generation |

After changing Lua, copy the changed files into the installed plugin folder and reopen the plugin. After changing Python, close and reopen the plugin to restart its Runtime.

Check the Runtime command entry point from the source root:

```bash
# macOS
.venv/bin/DaVinciASRRuntime --help
```

```powershell
# Windows
.\.venv\Scripts\DaVinciASRRuntime.exe --help
```

Run `doctor --cpu` again after changing the environment. Then create subtitles from a short timeline in Resolve to check audio export, transcription, and subtitle import. Packaging instructions are in [macOS packaging](packaging/macos/README.md) and [Windows packaging](packaging/windows/README.md).

To return to the installed Runtime, clear the development override and restart Resolve:

```bash
# macOS
launchctl unsetenv DAVINCI_ASR_RUNTIME
```

```powershell
# Windows
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $null, "User")
```

## Troubleshooting

### DaVinci ASR is not listed in the menu

Check the plugin folder hierarchy and fully restart Resolve. Installer users can run the installer again if the plugin files are missing.

### The Runtime is missing or fails to start

Installer users: run the installer again. If you previously used a source Runtime, clear `DAVINCI_ASR_RUNTIME` using the commands above and restart Resolve.

Developers: repeat the dependency installation steps, check that the Runtime executable exists in `.venv/bin` on macOS or `.venv\Scripts` on Windows, and set the override again.

### Model download fails

Check the network connection, free disk space, and write permission for the `models` folder. Try ModelScope in mainland China and Hugging Face elsewhere.

## Privacy and license

Audio transcription, alignment, and subtitle generation run locally. Model downloads only access the source selected in the UI.

Licensed under Apache-2.0. See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

This repository contains the public release of the project.
