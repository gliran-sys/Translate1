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

import ctypes
import threading
from typing import Callable

from pynput import keyboard as kb
from pynput import mouse as ms

from layout_mapper import LayoutPair, DEFAULT_PAIR


# ---------------------------------------------------------------------------
# Win32 helpers — get the character a key produces in the foreground app's
# keyboard layout.  pynput's listener runs in its own thread and resolves
# characters against that thread's layout (often English even when the user's
# active app has Hebrew/Russian/… set), so we ask Windows explicitly.
# ---------------------------------------------------------------------------

_u32 = ctypes.windll.user32
_u32.GetForegroundWindow.restype = ctypes.c_void_p
_u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_u32.GetWindowThreadProcessId.restype = ctypes.c_ulong
_u32.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
_u32.GetKeyboardLayout.restype = ctypes.c_void_p
_u32.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
_u32.MapVirtualKeyExW.restype = ctypes.c_uint

_MAPVK_VK_TO_CHAR = 2


def _char_for_vk(vk: int) -> str | None:
    """
    Return the (unshifted) character the given virtual-key code produces in
    the keyboard layout of the currently active window, or None if the key
    does not produce a printable character.
    """
    hwnd = _u32.GetForegroundWindow()
    tid = _u32.GetWindowThreadProcessId(hwnd, None)
    hkl = _u32.GetKeyboardLayout(tid)
    result = _u32.MapVirtualKeyExW(vk, _MAPVK_VK_TO_CHAR, hkl)
    # Bit 31 set → dead key; result == 0 → no character
    if result == 0 or (result >> 31):
        return None
    code = result & 0xFFFF
    if code <= 0x1F:          # control characters
        return None
    return chr(code)


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
        is_click_on_bubble: Callable[[int, int], bool] | None = None,
    ) -> None:
        self._on_change = on_change
        self._is_click_on_bubble = is_click_on_bubble
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
        # Only react on button-down, not release, and not during injection.
        # Skip if the click landed on the translation bubble — BubbleUI's own
        # <Button-1> handler will fire next and trigger the replacement.
        if pressed and not self.is_replacing:
            if self._is_click_on_bubble and self._is_click_on_bubble(x, y):
                return
            self._clear_buffer()

    # ------------------------------------------------------------------
    # Keyboard callback
    # ------------------------------------------------------------------

    def _on_key_press(self, key: kb.Key | kb.KeyCode | None) -> None:
        if self.is_replacing or not self._enabled:
            return

        # --- special keys (Key enum members) ---
        if isinstance(key, kb.Key):
            if key == kb.Key.backspace:
                with self._lock:
                    if self._buffer:
                        self._buffer = self._buffer[:-1]
                self._notify()
            elif key in (kb.Key.space, kb.Key.enter, kb.Key.tab,
                         kb.Key.esc, kb.Key.delete):
                self._clear_buffer()
            elif key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                         kb.Key.home, kb.Key.end, kb.Key.page_up, kb.Key.page_down):
                self._clear_buffer()
            return

        # --- regular character key (KeyCode) ---
        if not isinstance(key, kb.KeyCode):
            return

        # Resolve the character using the FOREGROUND WINDOW's keyboard layout.
        # pynput's listener thread may carry a different (e.g. English) layout
        # than the app the user is currently typing in, so key.char would give
        # the wrong character when, say, Hebrew or Russian is active.
        # Fall back to pynput's own resolution if the Win32 lookup fails.
        vk: int | None = getattr(key, 'vk', None)
        char: str | None = (_char_for_vk(vk) if vk else None) or key.char

        if char is None:
            return

        # Normalise to lowercase.  MapVirtualKeyExW returns uppercase for Latin
        # keys (e.g. 'A' for VK_A) but tracked_chars / translate() expect
        # lowercase.  For Hebrew/Cyrillic/Arabic/.lower() is a no-op.
        char = char.lower()

        # Space / tab / newline produced as a character → word boundary
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
