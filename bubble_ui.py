"""
bubble_ui.py
Floating "suggestion bubble" drawn with tkinter.

The bubble is a borderless, always-on-top Toplevel window that appears
just above the mouse cursor showing the translated text.  Clicking it
triggers text replacement in the active application.

Must be created and driven from the tkinter main thread.
"""

from __future__ import annotations

import ctypes
import threading
import tkinter as tk
from typing import Callable


# --------------------------------------------------------------------------
# Win32 helpers for cursor position
# --------------------------------------------------------------------------

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


# --------------------------------------------------------------------------
# Bubble window
# --------------------------------------------------------------------------

# How many milliseconds of idle time before the bubble auto-hides
_AUTO_HIDE_MS = 3000

# Visual constants
_PAD_X = 14
_PAD_Y = 6
_OFFSET_Y = 38          # pixels above the cursor
_FONT = ("Segoe UI", 13, "bold")
_BG = "#2C2C54"         # dark purple
_FG = "#EFEFEF"         # near-white
_BORDER_COLOR = "#7F5AF0"
_CORNER_RADIUS = 10


class BubbleUI:
    """
    Manages the floating suggestion bubble.

    Parameters
    ----------
    root:
        The root tkinter window (hidden).  All scheduling uses root.after().
    on_replace:
        Called when the user clicks the bubble.
        Signature: on_replace(original_buffer: str, translation: str) -> None
    """

    def __init__(
        self,
        root: tk.Tk,
        on_replace: Callable[[str, str], None],
    ) -> None:
        self._root = root
        self._on_replace = on_replace

        self._current_buffer: str = ''
        self._current_translation: str | None = None
        self._auto_hide_id: str | None = None

        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._text_id: int | None = None

        self._build_window()

    # ------------------------------------------------------------------
    # Public API (thread-safe via root.after)
    # ------------------------------------------------------------------

    def update(self, buffer: str, translation: str | None) -> None:
        """
        Called from the keyboard-hook thread whenever the buffer changes.
        Schedules the actual UI update on the main thread.
        """
        self._root.after(0, self._apply_update, buffer, translation)

    def hide(self) -> None:
        """Hide the bubble immediately (main thread)."""
        self._root.after(0, self._hide)

    # ------------------------------------------------------------------
    # Internal: window construction
    # ------------------------------------------------------------------

    def _build_window(self) -> None:
        win = tk.Toplevel(self._root)
        win.withdraw()                        # start hidden
        win.overrideredirect(True)            # no title bar / borders
        win.attributes('-topmost', True)      # always on top
        win.attributes('-alpha', 0.93)        # slight transparency
        # Make the window background transparent on Windows
        win.configure(bg='black')
        win.attributes('-transparentcolor', 'black')

        canvas = tk.Canvas(
            win,
            bg='black',
            highlightthickness=0,
            cursor='hand2',
        )
        canvas.pack(fill='both', expand=True)

        canvas.bind('<Button-1>', self._on_click)
        win.bind('<Leave>', lambda _e: self._schedule_hide())

        self._win = win
        self._canvas = canvas

        # Prevent clicking the bubble from stealing focus away from the active
        # application.  Without this, backspace/type keystrokes land on the
        # bubble window instead of the app the user was typing in.
        win.update_idletasks()
        _GWL_EXSTYLE = -20
        _WS_EX_NOACTIVATE = 0x08000000
        hwnd = win.winfo_id()
        current = ctypes.windll.user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, current | _WS_EX_NOACTIVATE)

    def _draw_bubble(self, text: str) -> None:
        """Redraw the canvas with a rounded-rectangle bubble and the given text."""
        canvas = self._canvas
        assert canvas is not None

        canvas.delete('all')

        # Use a temporary label to measure
        test_lbl = tk.Label(self._win, text=text, font=_FONT)
        test_lbl.update_idletasks()
        w = test_lbl.winfo_reqwidth() + _PAD_X * 2
        h = test_lbl.winfo_reqheight() + _PAD_Y * 2
        test_lbl.destroy()

        canvas.config(width=w, height=h)
        self._win.geometry(f'{w}x{h}')  # type: ignore[union-attr]

        r = _CORNER_RADIUS
        # Draw rounded rectangle (border then fill)
        self._round_rect(canvas, 0, 0, w, h, r, fill=_BG, outline=_BORDER_COLOR, width=2)

        self._text_id = canvas.create_text(
            w // 2, h // 2,
            text=text,
            font=_FONT,
            fill=_FG,
            anchor='center',
        )

    @staticmethod
    def _round_rect(
        canvas: tk.Canvas,
        x1: int, y1: int, x2: int, y2: int,
        r: int,
        **kwargs,
    ) -> None:
        """Draw a rounded rectangle on *canvas*."""
        canvas.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90,  extent=90,  style='pieslice', **kwargs)
        canvas.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0,   extent=90,  style='pieslice', **kwargs)
        canvas.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90,  style='pieslice', **kwargs)
        canvas.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90,  style='pieslice', **kwargs)
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, **kwargs)
        canvas.create_rectangle(x1, y1 + r, x2, y2 - r, **kwargs)

    # ------------------------------------------------------------------
    # Internal: update / hide logic
    # ------------------------------------------------------------------

    def _apply_update(self, buffer: str, translation: str | None) -> None:
        self._current_buffer = buffer
        self._current_translation = translation

        if not translation:
            self._hide()
            return

        self._draw_bubble(translation)
        self._position_near_cursor()
        self._win.deiconify()   # type: ignore[union-attr]
        self._win.lift()        # type: ignore[union-attr]
        self._reset_auto_hide()

    def _position_near_cursor(self) -> None:
        assert self._win is not None
        self._win.update_idletasks()
        cx, cy = _get_cursor_pos()
        w = self._win.winfo_width()
        h = self._win.winfo_height()

        # Place bubble centred above the cursor
        x = cx - w // 2
        y = cy - h - _OFFSET_Y

        # Keep on-screen
        screen_w = self._win.winfo_screenwidth()
        screen_h = self._win.winfo_screenheight()
        x = max(0, min(x, screen_w - w))
        y = max(0, min(y, screen_h - h))

        self._win.geometry(f'+{x}+{y}')

    def _hide(self) -> None:
        if self._win:
            self._win.withdraw()
        self._cancel_auto_hide()

    def _reset_auto_hide(self) -> None:
        self._cancel_auto_hide()
        self._auto_hide_id = self._root.after(_AUTO_HIDE_MS, self._hide)

    def _cancel_auto_hide(self) -> None:
        if self._auto_hide_id is not None:
            self._root.after_cancel(self._auto_hide_id)
            self._auto_hide_id = None

    def _schedule_hide(self) -> None:
        """Hide after a short delay when the mouse leaves the bubble."""
        self._root.after(300, self._hide)

    # ------------------------------------------------------------------
    # Click handler
    # ------------------------------------------------------------------

    def _on_click(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        buf = self._current_buffer
        trans = self._current_translation
        if buf and trans:
            self._hide()
            # Call the replacement callback in a new thread so we don't
            # block the tkinter main loop while injecting keystrokes.
            threading.Thread(
                target=self._on_replace,
                args=(buf, trans),
                daemon=True,
            ).start()
