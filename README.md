# v26_assistant

Windows voice assistant for interview / DevOps-SRE Q&A workflows.

Prepared on: 2026-09-25

## Current local standalone build

A ready-to-run Windows executable was built locally:

```powershell
C:\assistent\v26_assistant.exe
```

A transfer archive was also created locally:

```powershell
C:\assistent\v26_assistant_windows_standalone_20260925_1412.zip
```

Validation performed:

```powershell
python -m py_compile C:\assistent\v26_assistant.py
```

The standalone executable was launched from `C:\assistent\dist` and stayed running after startup.

SHA256 of the built executable:

```text
6130459FC4A28DB695B738F932AA0D8F4FEEF52C9CFDEB665096DAD349A03EA3
```

## Important security note

The local standalone `.exe` intentionally embeds `assistant_secrets.py` at build time so it can run on Windows without creating a separate secrets file.

Do not publish the built `.exe`, `.zip`, `dist/`, `build/`, or `assistant_secrets.py` to GitHub. They are ignored by `.gitignore` because the binary contains API keys and should remain local/private.

The GitHub repository contains source code, documentation, and the PyInstaller spec only.

## Run from source

Create `assistant_secrets.py` next to `v26_assistant.py`:

```python
API_KEY_GEMINI = "your-gemini-key"
API_KEY_GEMMA = "your-gemma-key"
```

Or provide environment variables:

```powershell
$env:GEMINI_API_KEY="your-gemini-key"
$env:GEMMA_API_KEY="your-gemma-key"
```

Install dependencies:

```powershell
python -m pip install pyaudiowpatch sounddevice google-genai numpy pynput pyinstaller
```

Run:

```powershell
cd C:\assistent
python v26_assistant.py
```

## Build standalone `.exe`

The project includes `v26_assistant.spec` for a one-file, windowed PyInstaller build.

Build command:

```powershell
cd C:\assistent
python -m PyInstaller --noconfirm --clean v26_assistant.spec
```

Output:

```powershell
C:\assistent\dist\v26_assistant.exe
```

If `assistant_secrets.py` exists during the build, PyInstaller embeds it into the executable.

## Features

- Tkinter 2x2 answer UI.
- Gemini native audio recognition / response stream.
- Secondary fast Gemini panel for short improved answers.
- Interview Mode for Senior SysAdmin / DevOps / SRE practice.
- WASAPI loopback support through `pyaudiowpatch`.
- Microphone fallback through `sounddevice` / WDM-KS.
- Space hotkey for mute.
- Health monitor and reconnect handling for transient API/session errors.
