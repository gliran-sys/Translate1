"""
system_tray.py
System-tray icon powered by pystray + Pillow.

Provides:
  - Icon in the notification area (green = active, grey = disabled)
  - Right-click menu:
      ✓ Enabled          — toggle translation on/off
      Layout ▶           — submenu listing every available language pair
          ● English ↔ Hebrew   (checked = current)
          ○ English ↔ Russian
          ...
      ──────────
      Exit
"""

from __future__ import annotations

from typing import Callable

from PIL import Image, ImageDraw
import pystray

from layout_mapper import LayoutPair


# ---------------------------------------------------------------------------
# Icon generation
# ---------------------------------------------------------------------------

_ICON_SIZE = 64


def _make_icon(active: bool) -> Image.Image:
    img = Image.new('RGBA', (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bg_color = (44, 44, 84, 230)
    dot_color = (0, 220, 120) if active else (120, 120, 120)

    margin = 4
    draw.ellipse(
        [margin, margin, _ICON_SIZE - margin, _ICON_SIZE - margin],
        fill=bg_color,
    )

    d = _ICON_SIZE // 4
    cx = cy = _ICON_SIZE // 2
    draw.ellipse([cx - d, cy - d, cx + d, cy + d], fill=dot_color)
    draw.text((cx - 5, 8), 'T', fill='white', font_size=18)

    return img


# ---------------------------------------------------------------------------
# SystemTray
# ---------------------------------------------------------------------------

class SystemTray:
    """
    Parameters
    ----------
    available_pairs:
        All language pairs to offer in the Layout submenu.
    initial_pair:
        The pair selected on startup.
    on_toggle:
        Called with the new enabled state when user toggles the hook.
    on_pair_change:
        Called with the newly selected LayoutPair.
    on_exit:
        Called when user chooses Exit.
    """

    def __init__(
        self,
        available_pairs: list[LayoutPair],
        initial_pair: LayoutPair,
        on_toggle: Callable[[bool], None],
        on_pair_change: Callable[[LayoutPair], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._available_pairs = available_pairs
        self._current_pair = initial_pair
        self._on_toggle = on_toggle
        self._on_pair_change = on_pair_change
        self._on_exit = on_exit
        self._enabled = True

        self._icon = pystray.Icon(
            name='Translate1',
            icon=_make_icon(True),
            title=self._make_title(),
            menu=self._build_menu(),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._icon.run()

    def stop(self) -> None:
        self._icon.stop()

    # ------------------------------------------------------------------
    # Menu construction
    # ------------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            # Enable / disable toggle
            pystray.MenuItem(
                text=self._toggle_label,
                action=self._handle_toggle,
                checked=lambda item: self._enabled,
            ),
            # Layout pair submenu
            pystray.MenuItem(
                'Layout',
                pystray.Menu(*[
                    pystray.MenuItem(
                        text=pair.name,
                        action=self._make_pair_handler(pair),
                        checked=self._make_pair_checker(pair),
                        radio=True,
                    )
                    for pair in self._available_pairs
                ]),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit', self._handle_exit),
        )

    def _toggle_label(self, item: pystray.MenuItem) -> str:  # noqa: ARG002
        return 'Enabled' if self._enabled else 'Disabled'

    def _make_pair_checker(self, pair: LayoutPair) -> Callable:
        """Return a lambda that returns True when *pair* is the active one."""
        def checker(item: pystray.MenuItem) -> bool:  # noqa: ARG001
            return self._current_pair.name == pair.name
        return checker

    def _make_pair_handler(self, pair: LayoutPair) -> Callable:
        """Return a handler that selects *pair* and updates the menu/title."""
        def handler(icon: pystray.Icon, item: pystray.MenuItem) -> None:  # noqa: ARG002
            self._current_pair = pair
            icon.title = self._make_title()
            icon.update_menu()
            self._on_pair_change(pair)
        return handler

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_toggle(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:  # noqa: ARG002
        self._enabled = not self._enabled
        icon.icon = _make_icon(self._enabled)
        icon.title = self._make_title()
        icon.update_menu()
        self._on_toggle(self._enabled)

    def _handle_exit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:  # noqa: ARG002
        icon.stop()
        self._on_exit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_title(self) -> str:
        state = 'Active' if self._enabled else 'Disabled'
        return f'Translate1 — {self._current_pair.name} [{state}]'
