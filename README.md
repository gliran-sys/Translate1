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

## Requirements

- **Windows 10 / 11** (uses Win32 APIs via `ctypes`)
- **Python 3.11+**

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Running

```bash
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
| **Exit** | Quit the application |

---

## Bubble behaviour

| Event | Result |
|-------|--------|
| Type a word | Bubble appears above cursor with translation |
| Click bubble | Typed text replaced with translation |
| Press Space / Enter / Esc | Bubble hides, buffer clears |
| Press Backspace | Buffer shrinks, bubble updates |
| Move cursor (arrow keys) | Buffer clears, bubble hides |
| 3 seconds idle | Bubble auto-hides |

---

## Supported layouts (v1)

- **English QWERTY <-> Hebrew** (Standard Israeli keyboard layout)

The mapping table in `layout_mapper.py` is straightforward to extend with
additional language pairs.

---

## Project structure

```
Translate1/
├── main.py          # Entry point
├── keyboard_hook.py # Global keyboard listener + word buffer
├── layout_mapper.py # EN <-> HE mapping and translation logic
├── bubble_ui.py     # Floating tkinter suggestion bubble
├── system_tray.py   # pystray tray icon and menu
└── requirements.txt # Third-party dependencies
```
