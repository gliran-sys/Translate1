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

import threading
import time
import tkinter as tk

from pynput import keyboard as kb

from bubble_ui import BubbleUI
from keyboard_hook import KeyboardHook
from layout_mapper import build_installed_pairs, DEFAULT_PAIR, LayoutPair
from system_tray import SystemTray


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
    hook.is_replacing = True
    try:
        # Short delay so any in-flight click event resolves before injection
        time.sleep(0.05)

        for _ in range(len(original)):
            controller.press(kb.Key.backspace)
            controller.release(kb.Key.backspace)
            time.sleep(0.01)

        for char in translation:
            controller.type(char)
            time.sleep(0.005)

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

    hook = KeyboardHook(on_change=on_buffer_change, pair=initial_pair)

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
