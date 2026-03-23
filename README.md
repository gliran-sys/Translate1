# Translate1 — Keyboard Language Translator

A lightweight Windows background utility that watches your keystrokes and
instantly shows a floating suggestion bubble when it detects that what you
typed makes sense in your other keyboard language.

Click the bubble to replace what you typed with the translation.

---

## How it works

```
You type: a k u o
          ↓ ↓ ↓ ↓
Bubble shows: שלום   <-- click to replace
```

The app maps physical key positions on a QWERTY keyboard to the
corresponding characters in the Hebrew keyboard layout (and vice-versa).

| Typed (EN) | Suggested (HE) |
|------------|----------------|
| `akuo`     | `שלום`          |
| `aprs`     | `שפרד`          |
| `hld`      | `ילד`           |

---

## Download (recommended)

1. Go to the [Releases page](../../releases/latest).
2. Download `Translate1.exe`.
3. Run it — no Python or installation required.
4. Right-click the tray icon and choose **Launch at Startup** to have it start automatically with Windows.

The app checks for newer releases on every launch and updates itself automatically.

---

## Build from source

**Requirements:** Python 3.11+, Windows 10/11

```bash
pip install -r requirements.txt pyinstaller
pyinstaller Translate1.spec
```

Output: `dist/Translate1.exe` — a single standalone executable, no console window.

---

## Run from source (development)

```bash
pip install -r requirements.txt
python main.py
```

The app starts silently — look for the **T** icon in the system tray.

---

## System Tray Menu

Right-click the tray icon for:

| Option | Action |
|--------|--------|
| **Enabled** (checked) | Translation suggestions are active |
| **Disabled** (unchecked) | App keeps running but shows no bubbles |
| **Launch at Startup** | Register/remove from Windows startup |
| **Check for Updates** | Manually trigger an update check |
| **Exit** | Quit the application |

---

## Bubble behaviour

| Event | Result |
|-------|--------|
| Type a word | Bubble appears above cursor with translation |
| Click bubble | Typed text replaced with translation **and** OS input language switches to match |
| Press Space / Enter / Esc | Bubble hides, buffer clears |
| Press Backspace | Buffer shrinks, bubble updates |
| Move cursor (arrow keys) | Buffer clears, bubble hides |
| 3 seconds idle | Bubble auto-hides |

### Automatic language switch on click

When you click the bubble, the app does two things atomically:

1. Replaces the typed text with the translation.
2. Sends `WM_INPUTLANGCHANGEREQUEST` to the active window with the HKL for the
   target language, so the OS input language indicator (taskbar / language bar)
   switches immediately.

**Example:** You are in English mode, accidentally type `akuo`, the bubble shows
`שלום` — clicking it replaces the text *and* switches your keyboard to Hebrew so
you can continue typing in Hebrew without pressing `Alt+Shift` manually.

This works for all supported layout pairs (English ↔ Hebrew, English ↔ Russian,
etc.).  The layout must be installed in Windows (**Settings → Time & language →
Language & region**) for the switch to take effect.

---

## Supported layouts

- **English QWERTY <-> Hebrew** (Standard Israeli keyboard layout)

The mapping table in `layout_mapper.py` is straightforward to extend with
additional language pairs.

---

## Publishing a release

Tag a commit to trigger the GitHub Actions build:

```bash
git tag v1.2.0
git push --tags
```

The workflow (`.github/workflows/release.yml`) runs on `windows-latest`,
builds `Translate1.exe` via PyInstaller, and publishes it as a GitHub Release
with the exe attached. Running instances will pick up the update on next launch.

---

## Project structure

```
Translate1/
├── main.py              # Entry point
├── keyboard_hook.py     # Global keyboard listener + word buffer
├── layout_mapper.py     # EN <-> HE mapping and translation logic
├── bubble_ui.py         # Floating tkinter suggestion bubble
├── system_tray.py       # pystray tray icon and menu
├── startup.py           # Windows registry startup management
├── updater.py           # GitHub Releases auto-updater
├── version.py           # Current version (overwritten at build time)
├── Translate1.spec      # PyInstaller build spec
├── requirements.txt     # Third-party dependencies
└── .github/workflows/
    └── release.yml      # CI: build + publish exe on version tag push
```
