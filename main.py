"""
main.py
Entry point for Translate1 — Keyboard Language Translator.

Architecture
------------
  Thread 1 (main):   tkinter event loop  →  drives BubbleUI
  Thread 2 (daemon): pynput keyboard listener  →  KeyboardHook
  Thread 3 (daemon): pystray tray icon  →  SystemTray

Cross-thread communication:
  KeyboardHook calls BubbleUI.update() which uses root.after() to
  schedule UI changes safely on the main thread.

Usage
-----
  python main.py
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk

from pynput import keyboard as kb

from bubble_ui import BubbleUI
from keyboard_hook import KeyboardHook
from layout_mapper import translate
from system_tray import SystemTray


# --------------------------------------------------------------------------
# Text replacement
# --------------------------------------------------------------------------

def _replace_text(
    hook: KeyboardHook,
    controller: kb.Controller,
    original: str,
    translation: str,
) -> None:
    """
    Replaces *original* with *translation* in the currently focused app.

    Steps:
      1. Suppress the hook so injected keystrokes aren't re-captured.
      2. Press Backspace × len(original) to erase the typed word.
      3. Type the translation.
      4. Re-enable the hook and reset the buffer.
    """
    hook.is_replacing = True
    try:
        # Small delay so the click event has fully resolved before we inject
        time.sleep(0.05)

        for _ in range(len(original)):
            controller.press(kb.Key.backspace)
            controller.release(kb.Key.backspace)
            time.sleep(0.01)    # pace the backspaces for reliability

        # Type each character of the translation
        for char in translation:
            controller.type(char)
            time.sleep(0.005)

    finally:
        hook.is_replacing = False
        hook.clear_buffer()


# --------------------------------------------------------------------------
# Application bootstrap
# --------------------------------------------------------------------------

def main() -> None:
    # Hidden root window — we don't want a visible main window, only the bubble
    root = tk.Tk()
    root.withdraw()
    root.title('Translate1')

    # Keep the process alive even when no other windows are visible
    root.protocol('WM_DELETE_WINDOW', lambda: None)

    # pynput controller for injecting keystrokes
    controller = kb.Controller()

    # Bubble UI (lives on the main thread)
    bubble: BubbleUI | None = None

    def on_replace(original: str, translation: str) -> None:
        """Called when user clicks the bubble; runs in a worker thread."""
        assert hook is not None
        _replace_text(hook, controller, original, translation)

    bubble = BubbleUI(root, on_replace=on_replace)

    # Keyboard hook
    def on_buffer_change(buffer: str, translation: str | None) -> None:
        if bubble is not None:
            bubble.update(buffer, translation)

    hook = KeyboardHook(on_change=on_buffer_change)

    # System tray
    def on_toggle(enabled: bool) -> None:
        hook.set_enabled(enabled)

    def on_exit() -> None:
        hook.stop()
        root.after(0, root.destroy)

    tray = SystemTray(on_toggle=on_toggle, on_exit=on_exit)

    # Start background threads
    hook.start()

    tray_thread = threading.Thread(target=tray.run, daemon=True, name='tray')
    tray_thread.start()

    # Run tkinter main loop (blocks until root.destroy() is called)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        hook.stop()
        tray.stop()


if __name__ == '__main__':
    main()
