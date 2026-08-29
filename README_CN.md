# DaVinci ASR

[English](README.md)

DaVinci ASR 是运行在 DaVinci Resolve 内的本地 AI 自动字幕工具。它会读取当前时间线音频，生成带时间戳的字幕，并将结果直接导入 Resolve。

## 使用的模型

- 语音识别：`Qwen/Qwen3-ASR-0.6B-hf` 或 `Qwen/Qwen3-ASR-1.7B-hf`
- 时间对齐：`Qwen/Qwen3-ForcedAligner-0.6B-hf`

插件只下载当前选择的语音识别模型和共享的时间对齐模型。时间对齐模型已安装后，切换语音识别模型不会重复下载。

## macOS 安装

macOS 安装包已经提供，请前往[官方插件下载页](https://www.heibagen.com/plugins)下载对应的 macOS 安装包。

1. 下载并打开 `.pkg` 安装包。
2. 按安装向导完成安装，然后重新打开 DaVinci Resolve。
3. 在 `Workspace（工作区） > Scripts（脚本） > Utility` 中打开 `DaVinci ASR`。

## Windows 源码安装

Windows 当前需要从源码安装。准备好以下内容：

- DaVinci Resolve
- 64 位 Windows
- Python 3.12
- 本项目源码和可用的网络连接

### 1. 获取源码

```bash
git clone https://github.com/heiba-wk/DaVinci-ASR.git
cd DaVinci-ASR
```

也可以通过 GitHub 的 **Code** 菜单下载并解压 ZIP 压缩包。

### 2. 安装 Runtime 依赖

将源码解压到一个不会随意移动的目录，例如 `C:\HEIBA\DaVinci-ASR`。在该目录打开 PowerShell，执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements\direct.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

### 3. 安装 Resolve 插件

把源码中的整个 `DaVinci ASR` 文件夹复制到下面的 `Utility` 目录中；最终路径应为：

```text
%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\DaVinci ASR
```

复制时不要只复制 `DaVinci ASR.lua`。首次下载模型和处理音频会写入 `models`、`audio_temp` 目录，请确保当前 Windows 用户对整个 `DaVinci ASR` 文件夹拥有修改权限。

### 4. 指定源码 Runtime

仍在源码根目录的 PowerShell 中执行：

```powershell
$runtime = (Resolve-Path ".\.venv\Scripts\DaVinciASRRuntime.exe").Path
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $runtime, "User")
```

源码目录不要移动。完成后彻底退出并重新打开 DaVinci Resolve，在 `Workspace（工作区） > Scripts（脚本） > Utility` 中打开 `DaVinci ASR`。

## 使用步骤

1. 打开含有音频的 Resolve 项目和时间线，再打开 `DaVinci ASR`。
2. 首次使用点击“模型下载”，选择 `ModelScope` 或 `Hugging Face`，等待模型显示“已就绪”。中国大陆用户建议选择 ModelScope。
3. 选择语言后点击“创建字幕”。需要时，可在“短语列表 / 提示”中填写人名或术语。
4. 生成的字幕会自动导入当前 Resolve 项目。

模型只需下载一次，后续使用会直接复用本地模型。

## 常见问题

### 菜单中看不到 DaVinci ASR

确认插件文件夹层级正确、Resolve 已完全重启，并检查 `DAVINCI_ASR_RUNTIME` 是否指向存在的 `DaVinciASRRuntime.exe`。

### 提示组件缺失或 Runtime 启动失败

回到源码目录重新执行依赖安装命令，确认 `.venv\Scripts\DaVinciASRRuntime.exe` 存在，然后重新设置环境变量并重启 Resolve。

### 模型下载失败

检查网络、磁盘空间和 `models` 文件夹的写入权限。中国大陆用户优先尝试 ModelScope，其他地区可尝试 Hugging Face。

## 隐私与许可

音频识别、时间对齐和字幕生成在本机完成。模型下载只会访问你在界面中选择的模型来源。

本项目采用 Apache-2.0 许可，详见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本仓库包含本项目的公开发布版本。
