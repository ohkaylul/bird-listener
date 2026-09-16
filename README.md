# Bird Listener

Listens to bird sounds from a microphone, identifies species locally with
[BirdNET](https://github.com/kahst/BirdNET-Analyzer) (via `birdnetlib`), and
sends a Windows desktop notification when it hears something worth flagging.
Species can be muted or set to always-alert by common name from a small GUI.

## Setup

Requires Python 3.12 (TensorFlow, a BirdNET dependency, doesn't yet support
newer Python releases on Windows).

```bash
py -3.12 -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## Run

```bash
run.bat
```

or

```bash
venv\Scripts\python main.py
```

Click **Start Listening**, and pick your microphone under Settings → Input
device if it isn't the system default.

## Alert behavior

- **Muted** species never alert.
- **Always alert** species alert on every detection.
- Everything else alerts only the first time, or again after it hasn't been
  heard for a configurable number of minutes ("Re-alert after" setting).

Preferences and per-species last-heard times are stored in `state.json`
(gitignored, machine-local). When running from the packaged .exe, it's kept
in `%APPDATA%\BirdListener\state.json` instead.

## Building a standalone .exe

```bash
venv\Scripts\pyinstaller birdlistener.spec --noconfirm
```

Produces `dist\BirdListener.exe` (~550MB, mostly TensorFlow). It's a
single-file build with no console window; expect a several-second cold
start while it unpacks to a temp directory and loads the model.

## Building the Windows installer

Requires [Inno Setup 6](https://jrsoftware.org/isinfo.php) and a prior
`dist\BirdListener.exe` build (above):

```bash
iscc installer.iss
```

Produces `installer_output\BirdListenerSetup.exe` — a standard install
wizard with Start Menu shortcut, optional desktop shortcut, optional
"launch at sign-in", and an uninstaller.
