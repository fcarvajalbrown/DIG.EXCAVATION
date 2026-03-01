"""
gamestates/tutorial.py
======================
Tutorial screen shown after PLAY NEW RUN, before gameplay begins.

The player reads the instructions and presses SPACE or ENTER to start.
"""

from __future__ import annotations

import math
import time
from typing import Optional

import pygame

from gamestates.base_state import BaseState
from ui.toolbar import Toolbar

_BLACK        = (0,   0,   0)
_GREEN_BRIGHT = (0,   255, 70)
_GREEN_DIM    = (0,   180, 50)
_GREEN_FAINT  = (0,   60,  15)

_LINES = [
    "WELCOME, ARCHAEOLOGIST",
    "",
    "You are connected to a decaying computer system.",
    "Your mission: recover artifacts before corruption destroys them.",
    "Security daemons patrol the system and will hunt you down.",
    "",
    "HOW TO PLAY",
    "────────────────────────────────────────",
    "1.  LS              List files in the current directory",
    "2.  CD <dir>        Enter a directory",
    "3.  SCAN <name>     Reveal a file or debris node",
    "4.  CARVE <name>    Convert debris into a readable file",
    "5.  RECON <name>    Extract the artifact inside a file",
    "6.  SELL <id>       Sell a collected artifact for credits",
    "7.  STATUS          Check your power, memory, and energy",
    "",
    "TIPS",
    "────────────────────────────────────────",
    "- Every command costs resources and advances one turn.",
    "- Corruption spreads every turn — act fast.",
    "- Less-corrupted artifacts sell for more credits.",
    "- Daemons grow more alert the longer you stay.",
    "- CARVE fails if debris corruption is too high.",
]


class TutorialState(BaseState):
    """Full-screen tutorial shown once before the first run.

    Parameters
    ----------
    screen_width:
        Display width in pixels.
    screen_height:
        Display height in pixels.
    font_path:
        Optional path to a monospace .ttf font.

    Returns
    -------
    bool
        ``True`` when the player presses SPACE or ENTER to proceed.
    """

    def __init__(
        self,
        screen_width:  int,
        screen_height: int,
        font_path:     Optional[str] = None,
    ) -> None:
        self._sw      = screen_width
        self._sh      = screen_height
        self._font    = self._load_font(font_path, 15)
        self._font_hd = self._load_font(font_path, 18)
        self._font_sm = self._load_font(font_path, 12)
        self._toolbar = Toolbar(screen_width, font_path=font_path)
        self._start   = time.monotonic()

    # ------------------------------------------------------------------
    # BaseState interface
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        self._start = time.monotonic()

    def on_exit(self) -> None:
        pass

    def update(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
    ) -> bool:
        """Draw tutorial and return True when player is ready to begin.

        Parameters
        ----------
        events:
            pygame event list.
        screen:
            Main display surface.

        Returns
        -------
        bool
            True when SPACE or ENTER is pressed.
        """
        for event in events:
            self._toolbar.handle_event(event)
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_SPACE, pygame.K_RETURN):
                    return True

        self._draw(screen)
        return False

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, screen: pygame.Surface) -> None:
        screen.fill(_BLACK)

        toolbar_h  = Toolbar.HEIGHT
        content_y  = toolbar_h + 20
        line_h     = self._font.get_linesize()
        cx         = self._sw // 2

        y = content_y
        for line in _LINES:
            if line == "":
                y += line_h // 2
                continue

            # Section headers
            if line in ("HOW TO PLAY", "TIPS", "WELCOME, ARCHAEOLOGIST"):
                surf = self._font_hd.render(line, True, _GREEN_BRIGHT)
            elif line.startswith("─"):
                surf = self._font_sm.render(line, True, _GREEN_FAINT)
            else:
                surf = self._font.render(line, True, _GREEN_DIM)

            screen.blit(surf, (cx - surf.get_width() // 2, y))
            y += line_h

        # Blinking prompt at bottom
        blink = math.sin((time.monotonic() - self._start) * 3) > 0
        if blink:
            prompt = "[ PRESS SPACE OR ENTER TO BEGIN ]"
            surf   = self._font_hd.render(prompt, True, _GREEN_BRIGHT)
            py     = self._sh - 50
            screen.blit(surf, (cx - surf.get_width() // 2, py))

        self._toolbar.draw(screen, paused=False)

    # ------------------------------------------------------------------
    # Font loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_font(font_path: Optional[str], size: int) -> pygame.font.Font:
        if font_path:
            from pathlib import Path
            p = Path(font_path)
            if p.exists():
                try:
                    return pygame.font.Font(str(p), size)
                except Exception:
                    pass
        return pygame.font.SysFont("monospace", size)