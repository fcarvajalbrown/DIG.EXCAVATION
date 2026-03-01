"""
gamestates/menu.py
==================
Main menu game state for DIG.EXCAVATION.

Responsibilities
----------------
- Render a full-screen green phosphor main menu.
- Display ASCII title art and navigation buttons.
- Handle mouse and keyboard input for button selection.
- Signal the game to start a new run or exit via return values from
  ``update()``.
- Never instantiate game systems — that is the game state's job on
  transition to gameplay.

Buttons
-------
  [ PLAY NEW RUN ]     → transitions to gameplay
  [ SETTINGS ]         → greyed out (not yet implemented)
  [ EXIT ]             → posts QUIT_REQUESTED

Visual style
------------
Full black background, centred ASCII title block, bracketed buttons in
bright green.  Hover highlights the button.  Keyboard: arrow keys to
navigate, Enter to select.
"""

from __future__ import annotations

import enum
from typing import Optional

import pygame

from gamestates.base_state import BaseState
from systems.event_queue import EventType, event_queue
from ui.toolbar import Toolbar, ToolbarEvent

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

_BLACK        = (0,   0,   0)
_GREEN_BRIGHT = (0,   255, 70)
_GREEN_DIM    = (0,   180, 50)
_GREEN_FAINT  = (0,   60,  15)
_GREEN_HOVER  = (0,   255, 70)
_GREEN_DISABLED = (0, 80,  25)

# ---------------------------------------------------------------------------
# ASCII title art
# ---------------------------------------------------------------------------

_TITLE_ART = [
    "  ██████╗ ██╗ ██████╗ ███████╗",
    "  ██╔══██╗██║██╔════╝ ██╔════╝",
    "  ██║  ██║██║██║  ███╗███████╗",
    "  ██║  ██║██║██║   ██║╚════██║",
    "  ██████╔╝██║╚██████╔╝███████║",
    "  ╚═════╝ ╚═╝ ╚═════╝ ╚══════╝",
    "",
    "   E X C A V A T I O N",
]

_SUBTITLE = "Virtual Artifacts Council — Field Terminal v0.1"


# ---------------------------------------------------------------------------
# Menu actions
# ---------------------------------------------------------------------------

class MenuAction(enum.Enum):
    """Return values from ``MenuState.update()``."""
    NONE      = "none"
    NEW_RUN   = "new_run"
    EXIT      = "exit"


# ---------------------------------------------------------------------------
# Button descriptor
# ---------------------------------------------------------------------------

class _Button:
    """Internal button descriptor for the menu."""

    def __init__(
        self,
        label:    str,
        action:   MenuAction,
        enabled:  bool = True,
    ) -> None:
        self.label   = label
        self.action  = action
        self.enabled = enabled
        self.rect:   pygame.Rect = pygame.Rect(0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Menu state
# ---------------------------------------------------------------------------

class MenuState(BaseState):
    """Full-screen main menu.

    Parameters
    ----------
    screen_width:
        Display width in pixels.
    screen_height:
        Display height in pixels.
    font_path:
        Optional path to a monospace .ttf font.

    Usage (inside Game)
    -------------------
        state = MenuState(screen_width=1000, screen_height=700)
        state.on_enter()

        # Game loop:
        action = state.update(events, screen)
        if action is MenuAction.NEW_RUN:
            game.push_state(GameplayState(...))
    """

    def __init__(
        self,
        screen_width:  int,
        screen_height: int,
        font_path:     Optional[str] = None,
    ) -> None:
        self._sw      = screen_width
        self._sh      = screen_height

        self._font_lg = self._load_font(font_path, 18)
        self._font_md = self._load_font(font_path, 15)
        self._font_sm = self._load_font(font_path, 12)
        self._font_art = self._load_font(font_path, 14)

        self._toolbar = Toolbar(screen_width, font_path=font_path)

        self._buttons = [
            _Button("[ PLAY NEW RUN ]", MenuAction.NEW_RUN),
            _Button("[ SETTINGS      ]", MenuAction.NONE, enabled=False),
            _Button("[ EXIT          ]", MenuAction.EXIT),
        ]
        self._selected: int = 0   # keyboard-selected button index

        self._hover_idx: Optional[int] = None

    # ------------------------------------------------------------------
    # BaseState interface
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        """Called when this state becomes active."""
        self._selected = 0

    def on_exit(self) -> None:
        """Called when this state is popped."""
        pass

    def update(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
    ) -> MenuAction:
        """Process events, draw the menu, return an action if triggered.

        Parameters
        ----------
        events:
            pygame event list from the game loop.
        screen:
            Main display surface to draw onto.

        Returns
        -------
        MenuAction
            ``NEW_RUN`` or ``EXIT`` if a button was activated, else ``NONE``.
        """
        action = MenuAction.NONE

        for event in events:
            # Toolbar buttons
            tb_action = self._toolbar.handle_event(event)
            if tb_action is ToolbarEvent.MENU:
                pass   # already on menu
            
            # Keyboard nav
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_DOWN, pygame.K_s):
                    self._advance_selection(1)
                elif event.key in (pygame.K_UP, pygame.K_w):
                    self._advance_selection(-1)
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    action = self._activate(self._selected)

            # Mouse
            elif event.type == pygame.MOUSEMOTION:
                self._hover_idx = self._button_at(event.pos)
                if self._hover_idx is not None:
                    self._selected = self._hover_idx

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                idx = self._button_at(event.pos)
                if idx is not None:
                    action = self._activate(idx)

        self._draw(screen)
        return action

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, screen: pygame.Surface) -> None:
        """Full redraw of the menu screen.

        Parameters
        ----------
        screen:
            Main display surface.
        """
        screen.fill(_BLACK)

        # Toolbar drawn on top at the end
        content_top = Toolbar.HEIGHT
        content_h   = self._sh - content_top

        # Centre point of content area
        cx = self._sw // 2
        cy = content_top + content_h // 2

        # Title art block
        art_line_h = self._font_art.get_linesize()
        art_total  = len(_TITLE_ART) * art_line_h
        art_y      = cy - art_total // 2 - 60

        for i, line in enumerate(_TITLE_ART):
            surf = self._font_art.render(line, True, _GREEN_DIM)
            screen.blit(surf, (cx - surf.get_width() // 2, art_y + i * art_line_h))

        # Subtitle
        sub_surf = self._font_sm.render(_SUBTITLE, True, _GREEN_FAINT)
        screen.blit(sub_surf, (cx - sub_surf.get_width() // 2,
                               art_y + art_total + 8))

        # Horizontal divider
        div_y = art_y + art_total + 30
        pygame.draw.line(screen, _GREEN_FAINT,
                         (cx - 180, div_y), (cx + 180, div_y))

        # Buttons
        btn_y = div_y + 28
        btn_gap = 44
        btn_w = 260
        btn_h = 34

        for i, btn in enumerate(self._buttons):
            rect = pygame.Rect(cx - btn_w // 2, btn_y + i * btn_gap, btn_w, btn_h)
            btn.rect = rect
            self._draw_button(screen, btn, selected=(i == self._selected))

        # Footer hint
        hint = "↑↓ navigate    ENTER select"
        hint_surf = self._font_sm.render(hint, True, _GREEN_FAINT)
        screen.blit(hint_surf, (cx - hint_surf.get_width() // 2, self._sh - 30))

        # Toolbar last (draws over content)
        self._toolbar.draw(screen, paused=False)

    def _draw_button(
        self,
        screen:   pygame.Surface,
        btn:      _Button,
        selected: bool,
    ) -> None:
        """Render a single menu button.

        Parameters
        ----------
        screen:
            Target surface.
        btn:
            Button descriptor (rect must be set before calling).
        selected:
            Whether this button is currently highlighted.
        """
        if not btn.enabled:
            colour = _GREEN_DISABLED
        elif selected:
            colour = _GREEN_BRIGHT
            # Selection background
            bg = pygame.Surface((btn.rect.width, btn.rect.height), pygame.SRCALPHA)
            bg.fill((0, 255, 70, 18))
            screen.blit(bg, btn.rect.topleft)
            # Selection bracket lines
            pygame.draw.line(screen, _GREEN_FAINT,
                             btn.rect.topleft, btn.rect.topright)
            pygame.draw.line(screen, _GREEN_FAINT,
                             btn.rect.bottomleft, btn.rect.bottomright)
        else:
            colour = _GREEN_DIM

        label_surf = self._font_md.render(btn.label, True, colour)
        lx = btn.rect.x + (btn.rect.width  - label_surf.get_width())  // 2
        ly = btn.rect.y + (btn.rect.height - label_surf.get_height()) // 2
        screen.blit(label_surf, (lx, ly))

        # "Coming soon" tag for disabled buttons
        if not btn.enabled:
            tag = self._font_sm.render("coming soon", True, _GREEN_FAINT)
            screen.blit(tag, (btn.rect.right + 8, ly))

    # ------------------------------------------------------------------
    # Input helpers
    # ------------------------------------------------------------------

    def _advance_selection(self, delta: int) -> None:
        """Move keyboard selection by *delta*, skipping disabled buttons.

        Parameters
        ----------
        delta:
            +1 for down, -1 for up.
        """
        n = len(self._buttons)
        for _ in range(n):
            self._selected = (self._selected + delta) % n
            if self._buttons[self._selected].enabled:
                break

    def _button_at(self, pos: tuple[int, int]) -> Optional[int]:
        """Return the index of the button under *pos*, or None.

        Parameters
        ----------
        pos:
            Mouse position (x, y).
        """
        for i, btn in enumerate(self._buttons):
            if btn.enabled and btn.rect.collidepoint(pos):
                return i
        return None

    def _activate(self, idx: int) -> MenuAction:
        """Trigger the action for button at *idx*.

        Parameters
        ----------
        idx:
            Index into self._buttons.

        Returns
        -------
        MenuAction
        """
        btn = self._buttons[idx]
        if not btn.enabled:
            return MenuAction.NONE

        if btn.action is MenuAction.EXIT:
            event_queue.post_immediate(EventType.QUIT_REQUESTED, source="MenuState")

        return btn.action

    # ------------------------------------------------------------------
    # Font loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_font(font_path: Optional[str], size: int) -> pygame.font.Font:
        """Load font or fall back to system monospace.

        Parameters
        ----------
        font_path:
            Path to a .ttf file, or None.
        size:
            Font size in points.
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