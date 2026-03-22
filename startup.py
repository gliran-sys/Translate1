"""
startup.py
Manages the Windows registry entry that launches Translate1 automatically at
user logon.

Key: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
Value: Translate1 → <path to Translate1.exe>

Using HKCU (current user) rather than HKLM (all users) means no admin rights
are required.
"""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_APP_NAME = "Translate1"


def _exe_path() -> str:
    """Return the absolute path of the running executable."""
    if getattr(sys, "frozen", False):
        # PyInstaller bundle — sys.executable is the .exe itself
        return str(Path(sys.executable).resolve())
    # Running from source — point at the script so users can still test this
    return str(Path(sys.argv[0]).resolve())


def register() -> None:
    """Add Translate1 to the Windows startup registry key."""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _exe_path())


def unregister() -> None:
    """Remove Translate1 from the Windows startup registry key."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _APP_NAME)
    except FileNotFoundError:
        pass  # already absent — nothing to do


def is_registered() -> bool:
    """Return True if Translate1 is in the startup registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _APP_NAME)
        return True
    except FileNotFoundError:
        return False
