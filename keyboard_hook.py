"""
keyboard_hook.py
Global keyboard listener that maintains a running word buffer and fires
a callback whenever the buffer (and its translation) changes.

The listener runs in its own background thread (managed by pynput).
All public state mutations are protected by a threading.Lock so that the
bubble UI thread can read safely.
"""

from __future__ import annotations

import threading
from typing import Callable

from pynput import keyboard as kb

from layout_mapper import translate

# Characters that end the current word (clear the buffer)
_WORD_BREAK_CHARS = {' ', '\t', '\n', '\r'}

# Printable ASCII range that we track (letters only; digits/symbols clear the buffer)
_TRACKED_CHARS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ')
# Also track Hebrew Unicode block (U+05D0–U+05EA)
_TRACKED_CHARS.update(chr(c) for c in range(0x05D0, 0x05EB))


class KeyboardHook:
    """
    Monitors global keyboard events and notifies a callback on every change.

    Callback signature:
        on_change(buffer: str, translation: str | None) -> None
    """

    def __init__(self, on_change: Callable[[str, str | None], None]) -> None:
        self._on_change = on_change
        self._buffer: str = ''
        self._lock = threading.Lock()

        # When True the hook ignores all events (used during text injection)
        self.is_replacing: bool = False
        self._enabled: bool = True

        self._listener: kb.Listener | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the background keyboard listener thread."""
        self._listener = kb.Listener(
            on_press=self._on_press,
            on_release=None,
        )
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop the listener cleanly."""
        if self._listener:
            self._listener.stop()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable translation monitoring at runtime."""
        self._enabled = enabled
        if not enabled:
            self._clear_buffer()

    def clear_buffer(self) -> None:
        """Public helper so external code can reset the word buffer."""
        self._clear_buffer()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clear_buffer(self) -> None:
        with self._lock:
            self._buffer = ''
        self._notify()

    def _notify(self) -> None:
        with self._lock:
            buf = self._buffer
        translation = translate(buf) if buf else None
        self._on_change(buf, translation)

    # ------------------------------------------------------------------
    # pynput callback
    # ------------------------------------------------------------------

    def _on_press(self, key: kb.Key | kb.KeyCode | None) -> None:
        if self.is_replacing or not self._enabled:
            return

        try:
            # Regular character key
            char: str | None = key.char  # type: ignore[union-attr]
        except AttributeError:
            char = None

        if char is not None:
            if char in _WORD_BREAK_CHARS:
                self._clear_buffer()
                return

            if char in _TRACKED_CHARS:
                with self._lock:
                    self._buffer += char
                self._notify()
            else:
                # Punctuation, digit, symbol → clear buffer (not a pure word)
                self._clear_buffer()
            return

        # Special keys
        if key == kb.Key.backspace:
            with self._lock:
                if self._buffer:
                    self._buffer = self._buffer[:-1]
            self._notify()
            return

        if key in (kb.Key.space, kb.Key.enter, kb.Key.tab,
                   kb.Key.esc, kb.Key.delete):
            self._clear_buffer()
            return

        # Arrow keys, shift, ctrl, etc. → clear buffer to avoid stale suggestions
        if key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                   kb.Key.home, kb.Key.end, kb.Key.page_up, kb.Key.page_down):
            self._clear_buffer()
