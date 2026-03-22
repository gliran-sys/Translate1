"""
updater.py
Checks the GitHub Releases API for a newer version of Translate1 and, if one
exists, downloads the new executable, replaces the current one, and restarts.

Only runs when the app is packaged as a PyInstaller bundle (sys.frozen is set).
In plain-Python development mode the check is skipped entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from version import __version__

GITHUB_REPO    = "gliran-sys/Translate1"
_RELEASES_URL  = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_EXE_NAME      = "Translate1.exe"
_UPDATE_TIMEOUT = 8   # seconds for each network call


def _fetch_latest() -> dict | None:
    """Return the latest release metadata dict, or None on any error."""
    req = urllib.request.Request(
        _RELEASES_URL,
        headers={"User-Agent": f"Translate1/{__version__}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_UPDATE_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def _version_tuple(tag: str) -> tuple[int, ...]:
    """Convert 'v1.2.3' or '1.2.3' to (1, 2, 3) for comparison."""
    return tuple(int(x) for x in tag.lstrip("v").split(".") if x.isdigit())


def check_and_apply(silent: bool = True) -> None:
    """
    Check GitHub for a newer release.  If one exists, download it, schedule
    a self-replacement via a temporary batch script, and exit.

    Parameters
    ----------
    silent:
        If True (default), silently skip on network errors.
        If False, print a status message (used when triggered manually from
        the tray menu).
    """
    # Only self-update when running as a frozen PyInstaller bundle.
    if not getattr(sys, "frozen", False):
        if not silent:
            print("[updater] Running from source — skipping self-update.")
        return

    if not silent:
        print("[updater] Checking for updates…")

    data = _fetch_latest()
    if data is None:
        if not silent:
            print("[updater] Could not reach GitHub — skipping update check.")
        return

    latest_tag = data.get("tag_name", "")
    if not latest_tag:
        return

    try:
        current_tuple = _version_tuple(__version__)
        latest_tuple  = _version_tuple(latest_tag)
    except ValueError:
        return

    if latest_tuple <= current_tuple:
        if not silent:
            print(f"[updater] Already on the latest version ({__version__}).")
        return

    # Find the exe asset in the release
    assets = data.get("assets", [])
    exe_asset = next(
        (a for a in assets if a.get("name", "").lower() == _EXE_NAME.lower()),
        None,
    )
    if exe_asset is None:
        if not silent:
            print(f"[updater] No {_EXE_NAME} asset found in release {latest_tag}.")
        return

    if not silent:
        print(f"[updater] Downloading v{latest_tag}…")

    exe_dir   = Path(sys.executable).parent
    new_exe   = exe_dir / f"{_EXE_NAME}.update"
    cur_exe   = exe_dir / _EXE_NAME
    bat_path  = exe_dir / "_translate1_update.bat"

    try:
        urllib.request.urlretrieve(exe_asset["browser_download_url"], new_exe)
    except (urllib.error.URLError, OSError) as exc:
        if not silent:
            print(f"[updater] Download failed: {exc}")
        return

    # Write a batch script that:
    #   1. Waits for this process to exit
    #   2. Replaces the exe
    #   3. Relaunches
    #   4. Deletes itself
    bat_path.write_text(
        "@echo off\n"
        ":wait\n"
        f'  tasklist /fi "PID eq %1" 2>nul | find /i "Translate1" >nul\n'
        "  if not errorlevel 1 (timeout /t 1 /nobreak >nul & goto wait)\n"
        f'move /y "{new_exe}" "{cur_exe}"\n'
        f'start "" "{cur_exe}"\n'
        'del "%~f0"\n',
        encoding="ascii",
    )

    subprocess.Popen(
        ["cmd", "/c", str(bat_path), str(subprocess.os.getpid())],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sys.exit(0)
