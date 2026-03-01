"""
ui/toolbar.py
=============
Old-school top toolbar for DIG.EXCAVATION.

Responsibilities
----------------
- Render a fixed-height bar across the top of the screen.
- Display the game title in the centre.
- Provide clickable [≡ MENU] button on the left and [II PAUSE] on the right.
- Return ``ToolbarEvent`` values when buttons are clicked so the game state
  can react without the toolbar knowing anything about game logic.
- Never post to event_queue directly; never import game systems.

Visual style
------------
Same green phosphor palette as terminal.py and status_panel.py.
The bar has a bottom border line and slightly lighter background to
separate it from the game area.  Buttons are bracketed ASCII-style
[≡ MENU] and [II PAUSE] with a hover highlight.
"""

from __future__ import annotations

import enum
from typing import Optional

import pygame


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

_BLACK        = (0,   0,   0)
_BG           = (0,   12,  4)      # very dark green, distinct from pure black
_GREEN_BRIGHT = (0,   255, 70)
_GREEN_DIM    = (0,   180, 50)
_GREEN_FAINT  = (0,   60,  15)
_GREEN_HOVER  = (0,   255, 70)
_BORDER       = (0,   60,  15)

_TOOLBAR_HEIGHT = 36
_BTN_PADDING    = 10   # horizontal padding inside each button


# ---------------------------------------------------------------------------
# Toolbar events
# ---------------------------------------------------------------------------

class ToolbarEvent(enum.Enum):
    """Values returned by ``Toolbar.handle_event()`` on button click."""
    NONE    = "none"
    MENU    = "menu"     # [≡ MENU] clicked
    PAUSE   = "pause"    # [II PAUSE] clicked — toggle


# ---------------------------------------------------------------------------
# Toolbar
# ---------------------------------------------------------------------------

class Toolbar:
    """Renders and handles interaction for the top toolbar.

    Parameters
    ----------
    screen_width:
        Full screen width in pixels.
    font_path:
        Optional path to a .ttf monospace font.
    font_size:
        Font size in points.
    title:
        Centre title text.

    Usage
    -----
        toolbar = Toolbar(screen_width=1000)

        # Each frame:
        action = toolbar.handle_event(pygame_event)
        if action is ToolbarEvent.PAUSE:
            game.toggle_pause()
        elif action is ToolbarEvent.MENU:
            game.go_to_menu()

        toolbar.draw(screen, paused=game.paused)
    """

    HEIGHT: int = _TOOLBAR_HEIGHT   # class-level constant for layout calculations

    def __init__(
        self,
        screen_width: int,
        font_path:    Optional[str] = None,
        font_size:    int = 15,
        title:        str = "DIG.EXCAVATION",
    ) -> None:
        self._width  = screen_width
        self._title  = title
        self._font   = self._load_font(font_path, font_size)

        # Button rects — computed once, updated if screen resizes
        self._btn_menu:  pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self._btn_pause: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self._compute_button_rects()

        # Hover tracking
        self._hover_menu:  bool = False
        self._hover_pause: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def handle_event(self, event: pygame.event.Event) -> ToolbarEvent:
        """Process a pygame event and return a ``ToolbarEvent``.

        Parameters
        ----------
        event:
            A pygame event.  Only ``MOUSEMOTION`` and ``MOUSEBUTTONDOWN``
            are acted on.

        Returns
        -------
        ToolbarEvent
            ``MENU``, ``PAUSE``, or ``NONE``.
        """
        if event.type == pygame.MOUSEMOTION:
            pos = event.pos
            self._hover_menu  = self._btn_menu.collidepoint(pos)
            self._hover_pause = self._btn_pause.collidepoint(pos)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            pos = event.pos
            if self._btn_menu.collidepoint(pos):
                return ToolbarEvent.MENU
            if self._btn_pause.collidepoint(pos):
                return ToolbarEvent.PAUSE

        return ToolbarEvent.NONE

    def draw(self, screen: pygame.Surface, paused: bool = False) -> None:
        """Render the toolbar directly onto *screen*.

        Parameters
        ----------
        screen:
            The main display surface.
        paused:
            If ``True``, the pause button renders as [▶ RESUME].
        """
        # Background bar
        bar_rect = pygame.Rect(0, 0, self._width, _TOOLBAR_HEIGHT)
        pygame.draw.rect(screen, _BG, bar_rect)

        # Bottom border
        pygame.draw.line(
            screen, _BORDER,
            (0, _TOOLBAR_HEIGHT - 1),
            (self._width, _TOOLBAR_HEIGHT - 1),
        )

        # Centre title
        title_surf = self._font.render(self._title, True, _GREEN_DIM)
        tx = (self._width - title_surf.get_width()) // 2
        ty = (_TOOLBAR_HEIGHT - title_surf.get_height()) // 2
        screen.blit(title_surf, (tx, ty))

        # Menu button
        self._draw_button(
            screen,
            self._btn_menu,
            text  = "[=] MENU",
            hover = self._hover_menu,
        )

        # Pause button
        pause_text = "[>] RESUME" if paused else "[||] PAUSE"
        self._draw_button(
            screen,
            self._btn_pause,
            text  = pause_text,
            hover = self._hover_pause,
        )

    # ------------------------------------------------------------------
    # Internal drawing
    # ------------------------------------------------------------------

    def _draw_button(
        self,
        screen: pygame.Surface,
        rect:   pygame.Rect,
        text:   str,
        hover:  bool,
    ) -> None:
        """Render a single toolbar button.

        Parameters
        ----------
        screen:
            Target surface.
        rect:
            Button bounding rect.
        text:
            Button label.
        hover:
            If ``True``, apply hover highlight.
        """
        colour = _GREEN_BRIGHT if hover else _GREEN_DIM

        if hover:
            # Subtle highlight background
            highlight = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
            highlight.fill((0, 255, 70, 20))
            screen.blit(highlight, rect.topleft)

        label = self._font.render(text, True, colour)
        lx    = rect.x + (rect.width  - label.get_width())  // 2
        ly    = rect.y + (rect.height - label.get_height()) // 2
        screen.blit(label, (lx, ly))

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _compute_button_rects(self) -> None:
        """Calculate button positions based on screen width."""
        btn_w = 110
        btn_h = _TOOLBAR_HEIGHT
        margin = 8

        self._btn_menu  = pygame.Rect(margin, 0, btn_w, btn_h)
        self._btn_pause = pygame.Rect(self._width - btn_w - margin, 0, btn_w, btn_h)

    # ------------------------------------------------------------------
    # Font loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_font(font_path: Optional[str], size: int) -> pygame.font.Font:
        """Load font from *font_path* or fall back to system monospace.

        Parameters
        ----------
        font_path:
            Path to a .ttf file, or None.
        size:
            Font size in points.

        Returns
        -------
        pygame.font.Font
        """
        if font_path:
            from pathlib import Path
            p = Path(font_path)
            if p.exists():
                try:
                    return pygame.font.Font(str(p), size)
                except Exception:
                    pass
        return pygame.font.SysFont("monospace", size)