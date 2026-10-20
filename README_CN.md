# DaVinci ASR

[English](README.md)

DaVinci ASR 是运行在 DaVinci Resolve 内的本地 AI 自动字幕工具。它会读取当前时间线音频，生成带时间戳的字幕，并将结果直接导入 Resolve。

## 下载安装（macOS 与 Windows）

**Mac 和 Windows 用户都可以直接前往[DaVinci ASR 官方下载页](https://www.heibagen.com/plugins/davinci-asr)下载安装包。**

1. 选择自己系统对应的安装包，按安装说明完成安装。
2. 彻底退出并重新打开 DaVinci Resolve。
3. 在 `Workspace（工作区） > Scripts（脚本） > Utility` 中打开 `DaVinci ASR`。
4. 按下方“使用步骤”下载模型，即可开始生成字幕。

安装包已包含运行组件，日常使用无需另装 Python，也无需配置开发环境。

## 使用的模型

- 语音识别：`Qwen/Qwen3-ASR-0.6B-hf` 或 `Qwen/Qwen3-ASR-1.7B-hf`
- 时间对齐：`Qwen/Qwen3-ForcedAligner-0.6B-hf`

插件只下载当前选择的语音识别模型和共享的时间对齐模型。时间对齐模型已安装后，切换语音识别模型不会重复下载。

## 使用步骤

1. 打开含有音频的 Resolve 项目和时间线，再打开 `DaVinci ASR`。
2. 首次使用点击“模型下载”，选择 `ModelScope` 或 `Hugging Face`，等待模型显示“已就绪”。中国大陆用户建议选择 ModelScope。
3. 使用自动字幕时，选择语言；需要时，可在“短语列表 / 提示”中填写人名或术语。需要文稿匹配时勾选“文稿匹配”，主窗口会隐藏并打开独立的文稿窗口。
4. 粘贴已经按照阅读逻辑分行的完整文稿，点击“使用文稿”或直接关闭文稿窗口。每个非空输入行会严格对应一个最终字幕块。仅当文本非空时才会启用文稿匹配；空白窗口关闭后会自动取消勾选。明确选择中文或英文时，质量门槛通过即可直接使用 Forced Aligner 对齐，不运行 ASR。
5. 点击“创建字幕”。文稿匹配会先保留每个匹配行，再执行可选的字幕后处理。“每行最大字符”会被禁用并忽略，因为文稿换行已经定义字幕块；“字幕无间隙”和“句末无标点”仍可使用，并在匹配后的 SRT block 上生效。文稿只在当前会话中保留，重新打开工具后会清空。
6. 生成的字幕会自动导入当前 Resolve 项目。

模型只需下载一次，后续使用会直接复用本地模型。

## 源码使用与开发（面向开发者）

想修改插件的用户可以按下面的步骤运行源码。请先安装 DaVinci Resolve 和 **Python 3.12**，并准备可用的网络连接。Windows 请使用 64 位 Python。

### 1. 获取源码

```bash
git clone https://github.com/heiba-wk/DaVinci-ASR.git
cd DaVinci-ASR
```

也可以通过 GitHub 的 **Code** 菜单下载并解压 ZIP 压缩包，再在解压后的项目目录中打开终端。

源码和虚拟环境请放在 Resolve 的 `Utility` 目录之外，例如 macOS 的 `~/Projects/DaVinci-ASR` 或 Windows 的 `C:\HEIBA\DaVinci-ASR`。`Utility` 中只放插件的 `DaVinci ASR` 文件夹。

### 2. 安装 Runtime 依赖

在源码根目录执行以下命令。它们会创建独立的 Python 环境，并以可编辑模式安装 Runtime；修改 Python 源码后，重启 Runtime 即可生效。

**macOS：在终端中执行。**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements/direct.txt
.venv/bin/python -m pip install -e .
.venv/bin/DaVinciASRRuntime doctor --cpu
```

**Windows：在 PowerShell 中执行。**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements\direct.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\DaVinciASRRuntime.exe doctor --cpu
```

`doctor --cpu` 会显示环境和模型状态。此时模型显示未安装属于正常情况，稍后可在插件中下载。此命令不会执行完整的语音识别测试。

### 3. 安装 Resolve 插件

把源码中的整个 `DaVinci ASR` 文件夹复制到对应系统的脚本目录；最终路径如下：

| 系统 | 插件文件夹的最终路径 |
| --- | --- |
| macOS | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/DaVinci ASR` |
| Windows | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\DaVinci ASR` |

不要只复制 `DaVinci ASR.lua`。插件会在 Lua 入口所在目录写入模型、临时音频和设置，请确保当前用户对整个 `DaVinci ASR` 文件夹拥有写入权限。

### 4. 指定源码 Runtime

在源码根目录执行对应系统的命令。`DAVINCI_ASR_RUNTIME` 用于告诉 Resolve 使用你刚创建的开发 Runtime。

**macOS：终端。**

```bash
launchctl setenv DAVINCI_ASR_RUNTIME "$(pwd)/.venv/bin/DaVinciASRRuntime"
```

此设置在当前登录会话中生效。注销账户或重启 macOS 后，请重新执行。

**Windows：PowerShell。**

```powershell
$runtime = (Resolve-Path ".\.venv\Scripts\DaVinciASRRuntime.exe").Path
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $runtime, "User")
```

请保持源码目录的位置不变。完成后彻底退出并重新打开 DaVinci Resolve，在 `Workspace（工作区） > Scripts（脚本） > Utility` 中打开 `DaVinci ASR`。首次生成字幕前，在插件中下载模型。

### 5. 修改代码并验证

| 源码位置 | 可以修改的内容 |
| --- | --- |
| `DaVinci ASR/DaVinci ASR.lua` | Resolve 界面、时间线音频导出、字幕导入 |
| `runtime/` | 模型下载、语音识别、时间对齐、字幕生成 |

修改 Lua 后，将改动复制到已安装的插件目录，再重新打开插件。修改 Python 后，关闭并重新打开插件，重启 Runtime 即可加载改动。

在源码根目录检查 Runtime 命令入口：

```bash
# macOS
.venv/bin/DaVinciASRRuntime --help
```

```powershell
# Windows
.\.venv\Scripts\DaVinciASRRuntime.exe --help
```

调整环境后，再运行一次 `doctor --cpu`。最后在 Resolve 中用一段简短时间线生成字幕，检查音频导出、识别和字幕导入是否正常。需要制作安装包时，参阅 [macOS 打包说明](packaging/macos/README.md)和 [Windows 打包说明](packaging/windows/README.md)。

需要恢复安装包自带的 Runtime 时，清除开发路径设置，再重启 Resolve：

```bash
# macOS
launchctl unsetenv DAVINCI_ASR_RUNTIME
```

```powershell
# Windows
[Environment]::SetEnvironmentVariable("DAVINCI_ASR_RUNTIME", $null, "User")
```

## 常见问题

### 菜单中看不到 DaVinci ASR

确认插件文件夹层级正确，并彻底退出后重新打开 Resolve。使用安装包的用户，如果插件文件缺失，可以重新运行安装包。

### 提示组件缺失或 Runtime 启动失败

使用安装包的用户：重新运行安装包。如果曾经运行过源码版，先按上文清除 `DAVINCI_ASR_RUNTIME`，再重启 Resolve。

开发者：重新执行依赖安装步骤，确认 Runtime 可执行文件位于 macOS 的 `.venv/bin` 或 Windows 的 `.venv\Scripts` 中，然后重新指定开发路径。

### 模型下载失败

检查网络、磁盘空间和 `models` 文件夹的写入权限。中国大陆用户优先尝试 ModelScope，其他地区可尝试 Hugging Face。

## 隐私与许可

音频识别、时间对齐和字幕生成在本机完成。模型下载只会访问你在界面中选择的模型来源。

本项目采用 Apache-2.0 许可，详见 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

本仓库包含本项目的公开发布版本。
