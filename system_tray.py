"""
system_tray.py
System-tray icon powered by pystray + Pillow.

Provides:
  - Animated icon in the notification area
  - Right-click menu: Enable / Disable toggle, Exit
  - Tooltip showing current state
"""

from __future__ import annotations

from typing import Callable

from PIL import Image, ImageDraw
import pystray


# --------------------------------------------------------------------------
# Icon generation
# --------------------------------------------------------------------------

_ICON_SIZE = 64


def _make_icon(active: bool) -> Image.Image:
    """Create a simple round icon.  Green = active, grey = disabled."""
    img = Image.new('RGBA', (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_color = (44, 44, 84, 230)   # dark purple background
    dot_color = (0, 220, 120) if active else (120, 120, 120)

    # Background circle
    margin = 4
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=bg_color,
    )

    # State dot
    d = _ICON_SIZE // 4
    cx = cy = _ICON_SIZE // 2
    draw.ellipse([cx - d, cy - d, cx + d, cy + d], fill=dot_color)

    # Small "T" letter for Translate1
    draw.text((cx - 5, 8), 'T', fill='white', font_size=18)

    return img


# --------------------------------------------------------------------------
# SystemTray class
# --------------------------------------------------------------------------

class SystemTray:
    """
    Manages the pystray tray icon and its menu.

    Parameters
    ----------
    on_toggle:
        Called when the user toggles Enable/Disable.
        Receives the new enabled state (True = active).
    on_exit:
        Called when the user chooses Exit.
    """

    def __init__(
        self,
        on_toggle: Callable[[bool], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._on_toggle = on_toggle
        self._on_exit = on_exit
        self._enabled = True

        self._icon = pystray.Icon(
            name='Translate1',
            icon=_make_icon(True),
            title='Translate1 — Active',
            menu=self._build_menu(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Block the calling thread running the tray event loop."""
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                text=self._toggle_label,
                action=self._handle_toggle,
                default=False,
                checked=lambda item: self._enabled,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit', self._handle_exit),
        )

    def _toggle_label(self, item: pystray.MenuItem) -> str:  # noqa: ARG002
        return 'Enabled' if self._enabled else 'Disabled'

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_toggle(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:  # noqa: ARG002
        self._enabled = not self._enabled
        icon.icon = _make_icon(self._enabled)
        icon.title = f'Translate1 — {"Active" if self._enabled else "Disabled"}'
        icon.update_menu()
        self._on_toggle(self._enabled)

    def _handle_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:  # noqa: ARG002
        icon.stop()
        self._on_exit()
