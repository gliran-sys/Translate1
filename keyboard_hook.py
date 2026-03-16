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
#
# We use ToUnicodeEx rather than MapVirtualKeyExW(MAPVK_VK_TO_CHAR) because
# the latter silently returns 0 for many non-Latin layouts (Hebrew, Arabic,
# CJK) on Windows 10/11, whereas ToUnicodeEx is the canonical API for
# "what Unicode character does this VK produce in this HKL?"
# ---------------------------------------------------------------------------

_u32 = ctypes.windll.user32
_u32.GetForegroundWindow.restype = ctypes.c_void_p
_u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
_u32.GetWindowThreadProcessId.restype = ctypes.c_ulong
_u32.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
_u32.GetKeyboardLayout.restype = ctypes.c_void_p
_u32.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
_u32.MapVirtualKeyExW.restype = ctypes.c_uint

# ToUnicodeEx: use a *private* WinDLL instance so our argtypes declaration never
# touches ctypes.windll.user32.ToUnicodeEx.
#
# ctypes.windll.user32 is a module-level singleton; every attribute access
# returns the SAME cached Python function object.  Setting .argtypes on it, or
# passing it to WINFUNCTYPE(...)(func) (which implicitly binds argtypes), makes
# pynput's own ToUnicodeEx call fail with "expected LP_c_byte instance instead
# of pointer to c_ubyte_Array_255".
#
# ctypes.WinDLL('user32') creates a fresh WinDLL Python object with its own
# per-instance function-object cache, so argtypes set here are fully isolated.
_priv_u32 = ctypes.WinDLL('user32')
_priv_u32.ToUnicodeEx.argtypes = [
    ctypes.c_uint,                   # wVirtKey
    ctypes.c_uint,                   # wScanCode
    ctypes.POINTER(ctypes.c_byte),   # lpKeyState (256 bytes)
    ctypes.c_wchar_p,                # pwszBuff
    ctypes.c_int,                    # cchBuff
    ctypes.c_uint,                   # wFlags
    ctypes.c_void_p,                 # dwhkl
]
_priv_u32.ToUnicodeEx.restype = ctypes.c_int
_ToUnicodeEx = _priv_u32.ToUnicodeEx

_MAPVK_VK_TO_CHAR = 2
_MAPVK_VK_TO_VSC  = 0   # VK → scan code (needed by ToUnicodeEx)
_OUR_PID = ctypes.windll.kernel32.GetCurrentProcessId()

# Punctuation characters that may appear within a sentence and pass through
# translation unchanged.  Space is handled separately (Key.space branch).
_SENTENCE_SEPARATORS = frozenset('?!:')


# GUITHREADINFO lets us find the *focused* child-window within the foreground
# thread — necessary because GetForegroundWindow() returns the top-level frame,
# but in multi-threaded apps (browsers, VS Code, terminals …) the actual
# keyboard-input thread belongs to a child HWND whose HKL may differ.
class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize',        ctypes.c_ulong),
        ('flags',         ctypes.c_ulong),
        ('hwndActive',    ctypes.c_void_p),
        ('hwndFocus',     ctypes.c_void_p),   # ← actual focused control
        ('hwndCapture',   ctypes.c_void_p),
        ('hwndMenuOwner', ctypes.c_void_p),
        ('hwndMoveSize',  ctypes.c_void_p),
        ('hwndCaret',     ctypes.c_void_p),
        ('rcCaret',       ctypes.c_long * 4), # RECT (left, top, right, bottom)
    ]

_u32.GetGUIThreadInfo.argtypes = [ctypes.c_ulong, ctypes.POINTER(_GUITHREADINFO)]
_u32.GetGUIThreadInfo.restype  = ctypes.c_int


def _focused_hwnd_and_hkl(exclude_pid: int) -> tuple[int, ctypes.c_void_p]:
    """
    Return (hwnd, hkl) for the truly focused window, skipping our own process.

    GetGUIThreadInfo(0) queries the foreground thread and returns hwndFocus —
    the actual focused control — which differs from the top-level foreground
    window in multi-threaded apps.  Falling back to GetForegroundWindow() when
    GetGUIThreadInfo fails.
    """
    info = _GUITHREADINFO()
    info.cbSize = ctypes.sizeof(_GUITHREADINFO)
    pid = ctypes.c_ulong(0)

    if _u32.GetGUIThreadInfo(0, ctypes.byref(info)):
        # Prefer hwndFocus; fall back to hwndActive within the same call
        for candidate in (info.hwndFocus, info.hwndActive):
            if candidate:
                _u32.GetWindowThreadProcessId(candidate, ctypes.byref(pid))
                if pid.value and pid.value != exclude_pid:
                    tid = _u32.GetWindowThreadProcessId(candidate, None)
                    return int(candidate), _u32.GetKeyboardLayout(tid)

    # Fallback: plain GetForegroundWindow
    fg = _u32.GetForegroundWindow()
    if fg:
        _u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value and pid.value != exclude_pid:
            tid = _u32.GetWindowThreadProcessId(fg, None)
            return int(fg), _u32.GetKeyboardLayout(tid)

    return 0, None


def _char_for_vk(vk: int, hkl: ctypes.c_void_p) -> str | None:
    """
    Return the (unshifted) character the given virtual-key code produces in
    *hkl*.  Returns None if the key does not produce a printable character.
    """

    # --- primary: ToUnicodeEx (works reliably for all layouts) ---
    scan = _u32.MapVirtualKeyExW(vk, _MAPVK_VK_TO_VSC, hkl)
    key_state = (ctypes.c_byte * 256)()
    # Propagate the real Shift state so Shift+key combos resolve to their
    # shifted characters (e.g. Shift+/ → '?', Shift+1 → '!').
    # Uppercase letters produced this way are lowercased by the caller.
    _VK_SHIFT = 0x10
    if _u32.GetKeyState(_VK_SHIFT) & 0x8000:
        key_state[_VK_SHIFT] = ctypes.c_byte(-128)  # high bit = key pressed
    buf = ctypes.create_unicode_buffer(8)
    # wFlags=0: the only value guaranteed to work on all Windows versions.
    # wFlags=4 (DONT_CHANGE_DEAD_KEY_STATE) was added in Windows 10 1703;
    # on older builds it causes ToUnicodeEx to return 0 for every key.
    n = _ToUnicodeEx(vk, scan, key_state, buf, len(buf) - 1, 0, hkl)
    if n == -1:
        # This VK is a dead key — flush the pending dead-key state so the
        # next call isn't affected, then report "no character".
        _ToUnicodeEx(0x20, 0, key_state, buf, len(buf) - 1, 0, hkl)
        return None
    if n == 1:
        ch = buf[0]
        if ch and ord(ch) > 0x1F:
            return ch

    # --- fallback: MapVirtualKeyExW (Latin layouts, older Windows) ---
    result = _u32.MapVirtualKeyExW(vk, _MAPVK_VK_TO_CHAR, hkl)
    if result and not (result >> 31):
        code = result & 0xFFFF
        if code > 0x1F:
            return chr(code)

    return None


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
        on_bubble_click: Callable[[], None] | None = None,
    ) -> None:
        self._on_change = on_change
        self._is_click_on_bubble = is_click_on_bubble
        self._on_bubble_click = on_bubble_click
        self._pair: LayoutPair = pair or DEFAULT_PAIR
        self._buffer: str = ''
        self._lock = threading.Lock()
        # Last non-our-process focused HWND and its HKL.  Updated on every
        # keystroke via GetGUIThreadInfo so we always use the editor's actual
        # keyboard layout (including when our bubble is briefly in the foreground).
        self._editor_hwnd: int = 0
        self._editor_hkl: ctypes.c_void_p | None = None

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
                if self._on_bubble_click:
                    self._on_bubble_click()
                return  # do NOT clear buffer
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
            elif key == kb.Key.space:
                # Ctrl+Space — hotkey to accept the current translation (same
                # as clicking the bubble).  Suppress the keystroke so the space
                # never lands in the editor.  Only intercepts when a non-empty
                # buffer has a valid translation; otherwise falls through to
                # normal space-accumulation so Ctrl+Space still works in apps.
                _VK_CONTROL = 0x11
                if (self._on_bubble_click
                        and ctypes.windll.user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000):
                    with self._lock:
                        buf = self._buffer
                    if buf and self._pair.translate(buf):
                        self._on_bubble_click()
                        return False  # suppress — don't send space to editor

                # Accumulate space so multi-word sentences build up correctly.
                # Never let the buffer start with a space.
                with self._lock:
                    if self._buffer:
                        self._buffer += ' '
                self._notify()
            elif key in (kb.Key.enter, kb.Key.tab, kb.Key.esc, kb.Key.delete):
                self._clear_buffer()
            elif key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                         kb.Key.home, kb.Key.end, kb.Key.page_up, kb.Key.page_down):
                self._clear_buffer()
            return

        # --- regular character key (KeyCode) ---
        if not isinstance(key, kb.KeyCode):
            return

        # GetGUIThreadInfo(0) gives the focused *child* control within the
        # foreground thread — important for multi-threaded apps (browsers,
        # VS Code, terminals) where GetForegroundWindow() returns the top-level
        # frame but the actual input thread (and its HKL) belongs to a child.
        # We cache the last valid (non-our-process) hwnd so that if our bubble
        # momentarily steals the foreground we keep using the editor's HKL.
        hwnd, hkl = _focused_hwnd_and_hkl(_OUR_PID)
        if hwnd:
            self._editor_hwnd = hwnd
            self._editor_hkl  = hkl
        else:
            # Bubble or unknown window is foreground — reuse last known HKL.
            hkl = getattr(self, '_editor_hkl', None)

        vk: int | None = getattr(key, 'vk', None)
        char: str | None = _char_for_vk(vk, hkl) if (vk and hkl) else None

        if char is None:
            return

        # Normalise to lowercase.  MapVirtualKeyExW returns uppercase for Latin
        # keys (e.g. 'A' for VK_A) but tracked_chars / translate() expect
        # lowercase.  For Hebrew/Cyrillic/Arabic/.lower() is a no-op.
        char = char.lower()

        # Space produced as a character → same as Key.space (accumulate).
        if char == ' ':
            with self._lock:
                if self._buffer:
                    self._buffer += ' '
            self._notify()
            return

        # Tab / newline → hard sentence boundary.
        if char in ('\t', '\n', '\r'):
            self._clear_buffer()
            return

        # Layout characters → accumulate and translate.
        # Sentence punctuation → accumulate as a pass-through (translated as-is).
        # Anything else (digit, symbol not in any layout) → clear.
        if char in self._pair.tracked_chars:
            with self._lock:
                self._buffer += char
            self._notify()
        elif char in _SENTENCE_SEPARATORS:
            with self._lock:
                if self._buffer:  # don't start the buffer with punctuation
                    self._buffer += char
            self._notify()
        else:
            self._clear_buffer()
