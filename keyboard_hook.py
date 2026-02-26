"""
keyboard_hook.py
Global keyboard + mouse listener that maintains a running word buffer and fires
a callback whenever the buffer (and its translation) changes.

Key design decisions
--------------------
* tracked_chars is derived dynamically from the active LayoutPair so that
  every character either layout can produce is treated as a word character.
  Previously this was a hardcoded set that missed Hebrew's '/' and "'"
  (physical Q/W keys), causing the buffer to clear mid-word in Hebrew mode.

* A pynput mouse listener clears the buffer on any mouse button press.
  Without this, clicking to reposition the cursor left the buffer stale,
  making the bubble suggest translations for words already committed.

* is_replacing must be set to False AFTER clear_buffer(), not before.
  See KeyboardHook.finish_replace() which is called from main.py.
"""

from __future__ import annotations

import threading
from typing import Callable

from pynput import keyboard as kb
from pynput import mouse as ms

from layout_mapper import LayoutPair, DEFAULT_PAIR


class KeyboardHook:
    """
    Monitors global keyboard and mouse events.

    Callback signature:
        on_change(buffer: str, translation: str | None) -> None
    """

    def __init__(
        self,
        on_change: Callable[[str, str | None], None],
        pair: LayoutPair | None = None,
    ) -> None:
        self._on_change = on_change
        self._pair: LayoutPair = pair or DEFAULT_PAIR
        self._buffer: str = ''
        self._lock = threading.Lock()

        # Set True during text injection so injected keystrokes are ignored.
        # Reset via finish_replace() which clears the buffer first.
        self.is_replacing: bool = False
        self._enabled: bool = True

        self._kb_listener: kb.Listener | None = None
        self._mouse_listener: ms.Listener | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start background keyboard and mouse listener threads."""
        self._kb_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=None,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

        self._mouse_listener = ms.Listener(on_click=self._on_mouse_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

    def stop(self) -> None:
        """Stop both listeners cleanly."""
        if self._kb_listener:
            self._kb_listener.stop()
        if self._mouse_listener:
            self._mouse_listener.stop()

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable translation monitoring at runtime."""
        self._enabled = enabled
        if not enabled:
            self._clear_buffer()

    def set_pair(self, pair: LayoutPair) -> None:
        """Switch the active language pair; resets the buffer immediately."""
        self._pair = pair
        self._clear_buffer()

    def clear_buffer(self) -> None:
        """Public helper so external code can reset the word buffer."""
        self._clear_buffer()

    def finish_replace(self) -> None:
        """
        Called after text injection completes.
        Clears the buffer BEFORE re-enabling the hook so that no injected
        keystroke bleeds into the next word's buffer.
        """
        self._clear_buffer()        # flush while is_replacing is still True
        self.is_replacing = False   # only then re-open the gate

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
        translation = self._pair.translate(buf) if buf else None
        self._on_change(buf, translation)

    # ------------------------------------------------------------------
    # Mouse callback — clears buffer when cursor is repositioned
    # ------------------------------------------------------------------

    def _on_mouse_click(
        self,
        x: int,         # noqa: ARG002
        y: int,         # noqa: ARG002
        button: ms.Button,  # noqa: ARG002
        pressed: bool,
    ) -> None:
        # Only react on button-down, not release, and not during injection
        if pressed and not self.is_replacing:
            self._clear_buffer()

    # ------------------------------------------------------------------
    # Keyboard callback
    # ------------------------------------------------------------------

    def _on_key_press(self, key: kb.Key | kb.KeyCode | None) -> None:
        if self.is_replacing or not self._enabled:
            return

        try:
            char: str | None = key.char  # type: ignore[union-attr]
        except AttributeError:
            char = None

        if char is not None:
            # Space, tab, newline → word boundary
            if char in (' ', '\t', '\n', '\r'):
                self._clear_buffer()
                return

            # Accumulate only characters that belong to the active pair;
            # anything else (digit, punctuation not in either layout) clears.
            if char in self._pair.tracked_chars:
                with self._lock:
                    self._buffer += char
                self._notify()
            else:
                self._clear_buffer()
            return

        # --- special keys ---
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

        # Cursor movement keys: context is lost, reset buffer
        if key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                   kb.Key.home, kb.Key.end, kb.Key.page_up, kb.Key.page_down):
            self._clear_buffer()
