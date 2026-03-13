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
_KEYEVENTF_KEYUP   = 0x0002
_KEYEVENTF_SCANCODE = 0x0008

# SendInput: same isolation strategy as ToUnicodeEx in keyboard_hook.py.
# pynput's keyboard Controller also calls windll.user32.SendInput with its own
# INPUT struct; setting argtypes on the shared singleton causes
# "expected LP__INPUT instance instead of pointer to INPUT" when pynput's
# controller.press() runs.  A private WinDLL instance fixes this.
_priv_u32 = ctypes.WinDLL('user32')
_priv_u32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(_INPUT), ctypes.c_int]
_priv_u32.SendInput.restype  = ctypes.c_uint
_send_input = _priv_u32.SendInput

# ---------------------------------------------------------------------------
# Clipboard helpers — used for atomic Unicode text injection via Ctrl+V.
# Character-by-character KEYEVENTF_UNICODE injection is unreliable for
# multi-word text: the space sent as VK_PACKET (U+0020) triggers editor-side
# state changes (autocomplete, RTL/LTR boundary events) that corrupt the
# second word.  Putting the entire translation on the clipboard and pasting
# with a single Ctrl+V avoids all per-character focus issues.
# ---------------------------------------------------------------------------

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE  = 0x0002

_kernel32 = ctypes.WinDLL('kernel32')
_kernel32.GlobalAlloc.argtypes  = [ctypes.c_uint, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype   = ctypes.c_void_p
_kernel32.GlobalLock.argtypes   = [ctypes.c_void_p]
_kernel32.GlobalLock.restype    = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype  = ctypes.c_int
_kernel32.GlobalSize.argtypes   = [ctypes.c_void_p]
_kernel32.GlobalSize.restype    = ctypes.c_size_t

_user32_cb = ctypes.WinDLL('user32')
_user32_cb.OpenClipboard.argtypes    = [ctypes.c_void_p]
_user32_cb.OpenClipboard.restype     = ctypes.c_int
_user32_cb.CloseClipboard.argtypes   = []
_user32_cb.CloseClipboard.restype    = ctypes.c_int
_user32_cb.EmptyClipboard.argtypes   = []
_user32_cb.EmptyClipboard.restype    = ctypes.c_int
_user32_cb.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32_cb.SetClipboardData.restype  = ctypes.c_void_p
_user32_cb.GetClipboardData.argtypes = [ctypes.c_uint]
_user32_cb.GetClipboardData.restype  = ctypes.c_void_p


def _get_clipboard() -> str:
    """Return the current clipboard text (empty string on failure)."""
    if not _user32_cb.OpenClipboard(None):
        return ''
    try:
        handle = _user32_cb.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return ''
        ptr = _kernel32.GlobalLock(handle)
        if not ptr:
            return ''
        try:
            size = _kernel32.GlobalSize(handle)
            # Each wchar_t is 2 bytes; strip the null terminator
            n_chars = size // 2 - 1
            return ctypes.wstring_at(ptr, max(n_chars, 0))
        finally:
            _kernel32.GlobalUnlock(handle)
    finally:
        _user32_cb.CloseClipboard()


def _set_clipboard(text: str) -> None:
    """Place *text* on the clipboard as CF_UNICODETEXT."""
    # Allocate a moveable global block: (len + 1) wchar_t (2 bytes each)
    n_bytes = (len(text) + 1) * 2
    handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, n_bytes)
    if not handle:
        return
    ptr = _kernel32.GlobalLock(handle)
    if not ptr:
        return
    ctypes.memmove(ptr, (text + '\x00').encode('utf-16-le'), n_bytes)
    _kernel32.GlobalUnlock(handle)

    if not _user32_cb.OpenClipboard(None):
        return
    try:
        _user32_cb.EmptyClipboard()
        _user32_cb.SetClipboardData(_CF_UNICODETEXT, handle)
    finally:
        _user32_cb.CloseClipboard()


def _send_ctrl_v() -> None:
    """Send Ctrl+V via SendInput (scan-code based, layout-independent)."""
    _VK_CONTROL = 0x11
    _VK_V       = 0x56
    _SC_CONTROL = 0x1D   # scan code for Left Ctrl
    _SC_V       = 0x2F   # scan code for V

    events: list[tuple[int, int, int]] = [
        (_VK_CONTROL, _SC_CONTROL, 0),
        (_VK_V,       _SC_V,       0),
        (_VK_V,       _SC_V,       _KEYEVENTF_KEYUP),
        (_VK_CONTROL, _SC_CONTROL, _KEYEVENTF_KEYUP),
    ]
    for vk, sc, flags in events:
        ki  = _KEYBDINPUT(wVk=vk, wScan=sc, dwFlags=flags | _KEYEVENTF_SCANCODE,
                          time=0, dwExtraInfo=None)
        inp = _INPUT(type=_INPUT_KEYBOARD, _=_INPUT_UNION(ki=ki))
        _send_input(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


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
      3. Paste the translation   — inject via clipboard + Ctrl+V (atomic)
      4. Call finish_replace()   — clears buffer THEN sets is_replacing=False
                                   (prevents injected chars bleeding into buffer)

    Character-by-character KEYEVENTF_UNICODE injection is avoided because the
    space character sent as VK_PACKET triggers editor-side state changes
    (autocomplete, RTL/LTR boundary events) that corrupt subsequent characters.
    Placing the entire string on the clipboard and sending a single Ctrl+V is
    fully atomic and immune to per-character layout issues.
    """
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
        # SetForegroundWindow requires a top-level HWND; child HWNDs are silently
        # ignored.  GetAncestor(GA_ROOT=2) walks up to the root owner window.
        editor_hwnd = hook._editor_hwnd
        if editor_hwnd:
            _GA_ROOT = 2
            root_hwnd = ctypes.windll.user32.GetAncestor(editor_hwnd, _GA_ROOT)
            target_hwnd = root_hwnd if root_hwnd else editor_hwnd
            ctypes.windll.user32.SetForegroundWindow(target_hwnd)
            # 100 ms gives the focus transition enough time to commit before
            # the first backspace fires.  20 ms was too short on some systems,
            # causing the first few keystrokes to still land on the bubble.
            time.sleep(0.1)

        for _ in range(len(original)):
            controller.press(kb.Key.backspace)
            controller.release(kb.Key.backspace)
            time.sleep(0.01)

        # Brief pause between delete and paste phases so the editor finishes
        # processing the backspaces before receiving the pasted text.
        time.sleep(0.05)

        # Save the current clipboard, paste the translation, then restore.
        saved_clipboard = _get_clipboard()
        try:
            _set_clipboard(translation)
            _send_ctrl_v()
            # Give the paste event time to be processed before restoring the
            # clipboard; too short and the editor may still be reading it.
            time.sleep(0.15)
        finally:
            if saved_clipboard:
                _set_clipboard(saved_clipboard)

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
        # Re-read the hook's live buffer to fix a race condition: the mouse
        # click can schedule _do_replace before the keyboard thread's last
        # _apply_update reaches the tkinter queue, leaving _current_buffer in
        # bubble_ui stale.  hook._buffer is always current (updated
        # synchronously on every keystroke) and is not yet cleared at this
        # point (finish_replace() only runs at the end of _replace_text).
        with hook._lock:
            live_buf = hook._buffer
        if live_buf:
            live_trans = hook._pair.translate(live_buf)
            if live_trans:
                original, translation = live_buf, live_trans
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
