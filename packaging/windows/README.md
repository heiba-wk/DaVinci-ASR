# Windows packaging

Run from a clean x64 Windows build machine with Python 3.12 and Inno Setup 6:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build_windows.ps1 -Variant all -Version 1.0.1
```

Build and release outputs default outside Resolve's `Utility` scan tree under
`%LOCALAPPDATA%\HEIBA\DaVinciASRBuild` and
`%USERPROFILE%\Documents\DaVinciASR-Releases`. Automation can override them with
`DAVINCI_ASR_BUILD_ROOT` and `DAVINCI_ASR_DIST_ROOT`.

The build fails unless both hashed platform locks exist. Model weights are not
embedded; the installer creates a user-writable `DaVinci ASR\models` folder
beside the Lua entry for UI downloads or manually supplied archives. The
installer contains two bundled runtimes. At install time it queries
`Win32_VideoController`: an NVIDIA machine receives the CUDA runtime; every
other machine receives the CPU runtime. The selected PyTorch wheel must bundle
its CUDA dependencies, so the user does not install Python, CUDA Toolkit,
cuDNN, pip, or Conda.

After both Windows x64 Runtime directories have been built on their native
targets, macOS can reuse the same pinned, offline Docker/Inno Setup compiler as
HEIBA AI Studio for the final installer layer:

```bash
packaging/windows/build_installer_macos.sh 1.0.1
```

On macOS, point `DAVINCI_ASR_WINDOWS_BUILD_ROOT` at the transferred native
Windows runtime directories; the default is `~/DaVinciASR-Build/windows`.

This macOS step only compiles the installer around prebuilt Windows runtimes; it
does not cross-compile Python, PyTorch, CPU, or CUDA binaries. The script fails
closed if either runtime is missing or any model weight is present.
