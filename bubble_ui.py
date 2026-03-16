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


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",    ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork",    _RECT),
        ("dwFlags",   ctypes.c_ulong),
    ]


def _get_cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _monitor_work_area(x: int, y: int) -> tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the work area of the monitor that
    contains the point (x, y).  Falls back to the primary monitor if the call
    fails.  Using the work area (rcWork) instead of rcMonitor excludes the
    taskbar so the bubble is never hidden behind it."""
    _MONITOR_DEFAULTTONEAREST = 2
    pt = _POINT(x, y)
    hmon = ctypes.windll.user32.MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if hmon and ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        r = info.rcWork
        return r.left, r.top, r.right, r.bottom
    # Fallback: assume a single 1920×1080 primary screen at origin
    return 0, 0, 1920, 1080


# --------------------------------------------------------------------------
# Bubble window
# --------------------------------------------------------------------------

# How many milliseconds of idle time before the bubble auto-hides
_AUTO_HIDE_MS = 3000

# Visual constants — iOS 26 "liquid glass" dark style
_PAD_X = 20
_PAD_Y = 11
_OFFSET_Y_ABOVE = 42        # pixels above the cursor (preferred position)
_OFFSET_Y_BELOW = 24        # pixels below the cursor (fallback when near top)
_FONT = ("Segoe UI Variable Display", 13)   # falls back to Segoe UI on older Win
_BG   = "#1C1C1E"           # iOS dark system background
_FG   = "#FFFFFF"           # pure white
_BORDER_COLOR = "#0A84FF"   # iOS system blue — signals interactivity
_BORDER_WIDTH = 1
_CORNER_RADIUS = 18         # pill-like, matches iOS card radius


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
        # Guards against two replacement threads starting concurrently.
        # Set True when a thread is launched; reset to False in its finally
        # block.  Checked on the tkinter main thread in _do_replace so that
        # whichever of trigger_replace / _on_click fires first "wins" and
        # subsequent calls return immediately — even if _apply_update has since
        # restored _current_buffer between the two _do_replace invocations.
        self._replacing: bool = False

        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._text_id: int | None = None
        # Screen rect of the visible bubble (x, y, w, h), or None when hidden.
        # Written on the tkinter main thread; read from pynput's mouse thread.
        # A tuple assignment is atomic under CPython's GIL so no lock is needed.
        self._bubble_rect: tuple[int, int, int, int] | None = None

        self._build_window()

    # ------------------------------------------------------------------
    # Public API (thread-safe via root.after)
    # ------------------------------------------------------------------

    def contains_point(self, x: int, y: int) -> bool:
        """Return True if the cursor is currently inside the visible bubble.

        Safe to call from any thread (reads a single tuple reference).

        We call GetCursorPos() ourselves rather than trusting pynput's x, y
        because pynput's WH_MOUSE_LL hook delivers *physical* pixels while
        winfo_rootx/y (used to build _bubble_rect) return *logical* pixels.
        GetCursorPos also returns logical pixels, so the two coordinate spaces
        match exactly regardless of DPI scaling.
        """
        rect = self._bubble_rect
        if rect is None:
            return False
        pt = _POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        rx, ry, rw, rh = rect
        return rx <= pt.x < rx + rw and ry <= pt.y < ry + rh

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
        win.attributes('-alpha', 0.92)        # frosted-glass transparency
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
        # Draw border layer first (slightly larger, iOS blue), then fill on top.
        # Using a 1 px inset for the fill keeps the border visible as a thin ring.
        b = _BORDER_WIDTH
        self._round_rect(canvas, 0, 0, w, h, r, fill=_BORDER_COLOR, outline='')
        self._round_rect(canvas, b, b, w - b, h - b, max(r - b, 0), fill=_BG, outline='')

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
        # A late _apply_update queued before _do_replace ran must not show
        # or re-show the bubble while keystroke injection is in progress.
        # deiconify() can steal keyboard focus even with WS_EX_NOACTIVATE on
        # some Windows builds, causing backspaces and typed chars to land on
        # the bubble (silently discarded) instead of the editor.
        if self._replacing:
            return
        self._current_buffer = buffer
        self._current_translation = translation

        if not translation:
            self._hide()
            return

        # Snapshot the currently active window so we can restore focus after
        # deiconify().  WS_EX_NOACTIVATE stops *click*-based activation but
        # deiconify() calls ShowWindow(SW_SHOWNORMAL) which can still steal
        # focus on some Windows versions.
        _u32 = ctypes.windll.user32
        prev_fg: int = _u32.GetForegroundWindow()

        self._draw_bubble(translation)
        self._position_near_cursor()
        # deiconify() must be called first so tkinter's internal state
        # transitions from "withdrawn" to "normal" — bypassing it confuses the
        # event loop, which re-hides the window on the next tick.
        self._win.deiconify()   # type: ignore[union-attr]
        # Immediately override the SW_SHOWNORMAL that deiconify() issued with
        # SW_SHOWNOACTIVATE (4) so the bubble never steals keyboard focus.
        _u32.ShowWindow(self._win.winfo_id(), 4)  # type: ignore[union-attr]
        # If deiconify() stole the foreground, give it back.  This keeps
        # _char_for_vk() in keyboard_hook.py pointing at the real editor window
        # (with the correct keyboard layout) rather than our English-layout
        # bubble window.
        if prev_fg and _u32.GetForegroundWindow() == self._win.winfo_id():  # type: ignore[union-attr]
            _u32.SetForegroundWindow(prev_fg)
        self._win.lift()        # type: ignore[union-attr]

        # Flush all pending Tcl/Tk geometry commands (deiconify + geometry) to
        # Win32 before we read back winfo_rootx/y.  Without this the geometry
        # '+x+y' command may still be queued and winfo_rootx() returns stale
        # coordinates, causing contains_point() to miss every click.
        self._win.update_idletasks()   # type: ignore[union-attr]

        # Store the bubble rect in *logical* pixels (winfo_rootx/y returns
        # logical pixels from tkinter's perspective, consistent with
        # GetCursorPos which also returns logical pixels).
        self._bubble_rect = (
            self._win.winfo_rootx(),    # type: ignore[union-attr]
            self._win.winfo_rooty(),    # type: ignore[union-attr]
            self._win.winfo_width(),    # type: ignore[union-attr]
            self._win.winfo_height(),   # type: ignore[union-attr]
        )

        self._reset_auto_hide()

    def _position_near_cursor(self) -> None:
        assert self._win is not None
        self._win.update_idletasks()
        cx, cy = _get_cursor_pos()
        w = self._win.winfo_width()
        h = self._win.winfo_height()

        mon_left, mon_top, mon_right, mon_bottom = _monitor_work_area(cx, cy)

        # Prefer above the cursor; flip below when there isn't enough room so
        # the bubble doesn't cover the text the user is currently editing.
        if cy - h - _OFFSET_Y_ABOVE >= mon_top:
            y = cy - h - _OFFSET_Y_ABOVE
        else:
            y = cy + _OFFSET_Y_BELOW

        x = cx - w // 2

        # Clamp to the work area of the current monitor (handles multi-screen
        # and taskbar exclusion; winfo_screenwidth/height is primary-only).
        x = max(mon_left, min(x, mon_right - w))
        y = max(mon_top,  min(y, mon_bottom - h))

        self._win.geometry(f'+{x}+{y}')

    def _hide(self) -> None:
        self._bubble_rect = None
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
    # Click handler — two entry points, only one fires the replacement
    # ------------------------------------------------------------------

    def trigger_replace(self) -> None:
        """Called from pynput's mouse-listener thread when a click lands on
        the bubble.  Schedules _do_replace on the tkinter main thread so the
        replacement is triggered even if tkinter's own <Button-1> event never
        arrives (e.g. the window is briefly withdrawn before WM_LBUTTONDOWN
        is processed)."""
        self._root.after(0, self._do_replace)

    def _do_replace(self) -> None:
        """Tkinter-thread handler shared by trigger_replace() and _on_click().

        Only the first call that arrives while no replacement is in progress
        actually starts a thread.  Subsequent calls return immediately, even if
        _apply_update has restored _current_buffer between invocations (which
        can happen when the after-queue drains in an unlucky order: the first
        _do_replace fires with a stale buffer, _apply_update then restores it,
        and the second _do_replace would otherwise start a second thread)."""
        if self._replacing:
            return
        buf = self._current_buffer
        trans = self._current_translation
        if buf and trans:
            self._replacing = True
            self._current_buffer = ''
            self._current_translation = None
            self._hide()

            def _run(b: str = buf, t: str = trans) -> None:
                try:
                    self._on_replace(b, t)
                finally:
                    self._replacing = False

            threading.Thread(target=_run, daemon=True).start()

    def _on_click(self, _event: tk.Event) -> None:  # type: ignore[type-arg]
        # Schedule via after(0) instead of calling directly, so this callback
        # lands at the END of the after-queue — after any pending _apply_update
        # calls that haven't run yet.  Calling _do_replace() directly would
        # race: tkinter can process a <Button-1> event before draining pending
        # after(0, _apply_update, ...) callbacks, causing _do_replace to read a
        # stale buffer.  Meanwhile trigger_replace (mouse thread) also schedules
        # _do_replace via after(0); whichever runs first consumes the buffer and
        # the second call sees an empty buffer and does nothing.
        self._root.after(0, self._do_replace)
