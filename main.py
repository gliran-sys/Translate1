"""
main.py
Entry point for Translate1 — Keyboard Language Translator.

Architecture
------------
  Thread 1 (main):      tkinter event loop  →  drives BubbleUI
  Thread 2 (daemon):    pynput keyboard listener  →  KeyboardHook
  Thread 3 (daemon):    pynput mouse listener     →  KeyboardHook
  Thread 4 (daemon):    pystray tray icon         →  SystemTray

Cross-thread communication:
  KeyboardHook calls BubbleUI.update() which uses root.after() to schedule
  UI changes safely on the main thread.

Usage
-----
  python main.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import threading
import time
import tkinter as tk

from pynput import keyboard as kb

from bubble_ui import BubbleUI
from keyboard_hook import KeyboardHook
from layout_mapper import build_installed_pairs, DEFAULT_PAIR, LayoutPair
from system_tray import SystemTray


# ---------------------------------------------------------------------------
# Unicode-safe keystroke injection (bypasses the active keyboard layout)
# ---------------------------------------------------------------------------

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx',          ctypes.c_long),
        ('dy',          ctypes.c_long),
        ('mouseData',   ctypes.c_ulong),
        ('dwFlags',     ctypes.c_ulong),
        ('time',        ctypes.c_ulong),
        ('dwExtraInfo', ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk',         ctypes.c_ushort),
        ('wScan',       ctypes.c_ushort),
        ('dwFlags',     ctypes.c_ulong),
        ('time',        ctypes.c_ulong),
        ('dwExtraInfo', ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg',    ctypes.c_ulong),
        ('wParamL', ctypes.c_ushort),
        ('wParamH', ctypes.c_ushort),
    ]


class _INPUT_UNION(ctypes.Union):
    # All three members must be present so the union's size matches the largest
    # member (MOUSEINPUT).  Without MOUSEINPUT, sizeof(_INPUT) is too small and
    # SendInput silently rejects the call (returns 0, ERROR_INVALID_PARAMETER).
    _fields_ = [
        ('mi', _MOUSEINPUT),
        ('ki', _KEYBDINPUT),
        ('hi', _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [('type', ctypes.c_ulong), ('_', _INPUT_UNION)]


_INPUT_KEYBOARD   = 1
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_KEYUP   = 0x0002

# SendInput: same isolation strategy as ToUnicodeEx in keyboard_hook.py.
# pynput's keyboard Controller also calls windll.user32.SendInput with its own
# INPUT struct; setting argtypes on the shared singleton causes
# "expected LP__INPUT instance instead of pointer to INPUT" when pynput's
# controller.press() runs.  A private WinDLL instance fixes this.
_priv_u32 = ctypes.WinDLL('user32')
_priv_u32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
_priv_u32.SendInput.restype  = ctypes.c_uint
_send_input = _priv_u32.SendInput


def _type_unicode(text: str) -> None:
    """Inject *text* via KEYEVENTF_UNICODE so the active keyboard layout is
    bypassed entirely.  pynput's controller.type() resolves characters through
    the calling thread's layout (often English), which causes 's' to arrive as
    'ד' in the text editor when Hebrew layout is active.  Using VK_PACKET +
    KEYEVENTF_UNICODE sends the raw Unicode scalar directly."""
    for char in text:
        scan = ord(char)
        for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
            ki  = _KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
            inp = _INPUT(type=_INPUT_KEYBOARD, _=_INPUT_UNION(ki=ki))
            _send_input(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        time.sleep(0.005)


# ---------------------------------------------------------------------------
# Text replacement
# ---------------------------------------------------------------------------

def _replace_text(
    hook: KeyboardHook,
    controller: kb.Controller,
    original: str,
    translation: str,
) -> None:
    """
    Replace *original* with *translation* in the currently focused application.

    Ordering is critical:
      1. Set is_replacing=True   — stop capturing keystrokes
      2. Send backspaces         — erase the typed word
      3. Type the translation    — inject the new text
      4. Call finish_replace()   — clears buffer THEN sets is_replacing=False
                                   (prevents injected chars bleeding into buffer)
    """
    # DEBUG — remove after confirming replacement works
    print(f"[DEBUG] _replace_text: '{original}' -> '{translation}'")
    hook.is_replacing = True
    try:
        # Short delay so any in-flight click event resolves before injection
        time.sleep(0.05)

        # Re-focus the editor window.  Clicking the bubble can steal keyboard
        # focus on some Windows builds even with WS_EX_NOACTIVATE set; if focus
        # is on our bubble window, backspace and typed characters land there
        # (and are silently discarded) instead of in the text editor.
        # Our process received the click input event, so Windows grants us
        # permission to call SetForegroundWindow here.
        editor_hwnd = hook._editor_hwnd
        if editor_hwnd:
            ok = ctypes.windll.user32.SetForegroundWindow(editor_hwnd)
            print(f"[DEBUG] SetForegroundWindow({editor_hwnd:#x}) = {ok}")
            time.sleep(0.02)

        for _ in range(len(original)):
            controller.press(kb.Key.backspace)
            controller.release(kb.Key.backspace)
            time.sleep(0.01)

        _type_unicode(translation)

    finally:
        # Always restore state — even if injection raised an exception.
        # finish_replace() clears the buffer BEFORE re-opening the hook.
        hook.finish_replace()


# ---------------------------------------------------------------------------
# Application bootstrap
# ---------------------------------------------------------------------------

def main() -> None:
    available_pairs = build_installed_pairs()
    # Use the first installed pair as default; fall back to DEFAULT_PAIR if
    # the installed set happens not to include English↔Hebrew.
    initial_pair = next(
        (p for p in available_pairs if p.name == DEFAULT_PAIR.name),
        available_pairs[0],
    )

    # Hidden root window; only the bubble Toplevel is ever visible
    root = tk.Tk()
    root.withdraw()
    root.title('Translate1')
    root.protocol('WM_DELETE_WINDOW', lambda: None)

    controller = kb.Controller()

    def on_replace(original: str, translation: str) -> None:
        """Runs in a worker thread (spawned by BubbleUI on click)."""
        _replace_text(hook, controller, original, translation)

    bubble = BubbleUI(root, on_replace=on_replace)

    def on_buffer_change(buffer: str, translation: str | None) -> None:
        bubble.update(buffer, translation)

    hook = KeyboardHook(
        on_change=on_buffer_change,
        pair=initial_pair,
        is_click_on_bubble=bubble.contains_point,
        on_bubble_click=bubble.trigger_replace,
    )

    def on_toggle(enabled: bool) -> None:
        hook.set_enabled(enabled)

    def on_pair_change(pair: LayoutPair) -> None:
        hook.set_pair(pair)

    def on_exit() -> None:
        hook.stop()
        root.after(0, root.destroy)

    tray = SystemTray(
        available_pairs=available_pairs,
        initial_pair=initial_pair,
        on_toggle=on_toggle,
        on_pair_change=on_pair_change,
        on_exit=on_exit,
    )

    hook.start()

    tray_thread = threading.Thread(target=tray.run, daemon=True, name='tray')
    tray_thread.start()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        hook.stop()
        tray.stop()


if __name__ == '__main__':
    main()
