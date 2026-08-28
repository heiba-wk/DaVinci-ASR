# macOS Apple Silicon packaging

## 本地测试包

在 Apple Silicon Mac 上运行：

```bash
./packaging/macos/package_macos_arm64.sh
```

脚本会从 `DaVinci ASR.lua` 的 `Config.SCRIPT_VERSION` 自动读取插件版本，
并检查 Python Runtime 与 `pyproject.toml` 版本完全一致。输出固定为：

```text
dist/DaVinciASR-{version}-macos-arm64.pkg
```

本地模式使用项目现有的 `.dev-runtime/venv`，执行 PyInstaller 构建、Runtime
CPU doctor、PKG 展开审计，并生成未签名测试安装包。PyInstaller
工作目录、暂存目录和展开审计目录均位于 `${TMPDIR}/davinci-asr-build`，不会放进
Resolve 的 `Utility` 扫描树；`dist` 中只保留最终 PKG。

## 正式签名发布包

正式发布需要 SHA-256 哈希锁、Developer ID 证书和 `notarytool` keychain
profile：

```bash
export MACOS_APPLICATION_IDENTITY='Developer ID Application: …'
export MACOS_INSTALLER_IDENTITY='Developer ID Installer: …'
export MACOS_NOTARY_PROFILE='davinci-asr-notary'
./packaging/macos/build_macos.sh --signed
```

正式模式使用 Hardened Runtime，签名 PKG，提交 Apple 公证，装订票据并执行
Gatekeeper 检查。可通过 `DAVINCI_ASR_BUILD_ROOT` 和
`DAVINCI_ASR_DIST_ROOT` 覆盖构建与输出路径。

两种模式都不会把插件侧的 `DaVinci ASR/models` 写入 PKG。`postinstall`
仅在该路径完全不存在时创建空目录；已有目录或符号链接的内容、权限和所有权均
保持不变，因此升级安装不会覆盖用户已经下载或手工放置的模型。打包阶段还会验证
默认 `max_chars` 必须为 `42`。
