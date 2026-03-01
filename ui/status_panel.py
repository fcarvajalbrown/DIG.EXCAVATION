"""
ui/status_panel.py
==================
2D status overlay for DIG.EXCAVATION.

Responsibilities
----------------
- Render a sidebar showing resource bars, daemon threat level, current
  site info, and collected artifact count.
- Read state from ResourceManager, DaemonSystem, ArtifactSystem, and
  Filesystem — never modifies them.
- Subscribe to RESOURCE_CHANGED and DAEMON_ALERT for live updates.
- Never post events; never call command handlers.

Layout (top to bottom)
-----------------------
  [SITE NAME]
  ─────────────────
  POWER   [========  ] 80%
  MEMORY  [====      ] 40%
  ENERGY  [=======   ] 70%
  ─────────────────
  CREDITS   1250
  ARTIFACTS  3 / 12
  ─────────────────
  DAEMONS
    WATCHDOG-7  ALERT
    GHOST-2     IDLE
  ─────────────────
  TURN  42
"""

from __future__ import annotations

import logging
from typing import Optional

import pygame

from systems.artifact import ArtifactSystem
from systems.daemon import AlertState, DaemonSystem
from systems.event_queue import EventType, event_queue
from systems.filesystem import Filesystem
from systems.resource_manager import Resource, ResourceManager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette — matches terminal.py green phosphor theme
# ---------------------------------------------------------------------------

_BLACK        = (0,   0,   0)
_GREEN_BRIGHT = (0,   255, 70)
_GREEN_DIM    = (0,   180, 50)
_GREEN_FAINT  = (0,   80,  20)
_GREEN_ERROR  = (200, 255, 0)
_GREEN_WARN   = (160, 220, 0)
_BORDER       = (0,   60,  15)

# Bar colours per resource
_BAR_COLOURS: dict[Resource, tuple[int, int, int]] = {
    Resource.POWER:  (0, 220, 60),
    Resource.MEMORY: (0, 180, 80),
    Resource.ENERGY: (0, 200, 50),
}

# Alert state colours
_ALERT_COLOURS: dict[AlertState, tuple[int, int, int]] = {
    AlertState.IDLE:       _GREEN_DIM,
    AlertState.SUSPICIOUS: _GREEN_WARN,
    AlertState.ALERT:      _GREEN_ERROR,
}

_BAR_HEIGHT  = 8    # resource bar height in pixels
_BAR_WIDTH   = 130  # resource bar width in pixels
_SECTION_GAP = 14   # vertical gap between sections


class StatusPanel:
    """Renders a fixed-width status sidebar.

    Parameters
    ----------
    width:
        Panel width in pixels.
    height:
        Panel height in pixels.
    resource_manager:
        Source of resource ratios and values.
    daemon_system:
        Source of daemon states.
    artifact_system:
        Source of artifact counts and currency.
    filesystem:
        Source of site name and turn count.
    font_path:
        Optional path to a .ttf monospace font.
    font_size:
        Font size in points.
    padding:
        Inner padding on all sides.

    Usage
    -----
        panel = StatusPanel(width=200, height=600, rm=rm, ds=ds, arts=arts, fs=fs)

        # Each frame:
        panel.update(turn=game.turn)
        screen.blit(panel.surface, (800, 0))

        # On state exit:
        panel.teardown()
    """

    def __init__(
        self,
        width:            int,
        height:           int,
        resource_manager: ResourceManager,
        daemon_system:    DaemonSystem,
        artifact_system:  ArtifactSystem,
        filesystem:       Filesystem,
        font_path:        Optional[str] = None,
        font_size:        int = 14,
        padding:          int = 10,
    ) -> None:
        self._width   = width
        self._height  = height
        self._rm      = resource_manager
        self._ds      = daemon_system
        self._arts    = artifact_system
        self._fs      = filesystem
        self._padding = padding
        self._turn    = 0

        self._font    = self._load_font(font_path, font_size)
        self._small   = self._load_font(font_path, max(10, font_size - 2))
        self._line_h  = self._font.get_linesize()
        self._surface = pygame.Surface((width, height))

        event_queue.subscribe(EventType.RESOURCE_CHANGED, self._on_resource_changed)
        event_queue.subscribe(EventType.DAEMON_ALERT,     self._on_daemon_alert)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def surface(self) -> pygame.Surface:
        """Rendered panel surface — blit to screen each frame."""
        return self._surface

    def update(self, turn: int = 0) -> None:
        """Redraw the panel.  Call once per frame.

        Parameters
        ----------
        turn:
            Current game turn number, passed in from the game loop.
        """
        self._turn = turn
        self._draw()

    def teardown(self) -> None:
        """Unsubscribe from event_queue.  Call from game state's on_exit()."""
        event_queue.unsubscribe(EventType.RESOURCE_CHANGED, self._on_resource_changed)
        event_queue.unsubscribe(EventType.DAEMON_ALERT,     self._on_daemon_alert)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        """Full redraw of the panel surface."""
        surf = self._surface
        surf.fill(_BLACK)

        # Border line on the left edge
        pygame.draw.line(surf, _BORDER, (0, 0), (0, self._height))

        y = self._padding
        y = self._draw_site_name(surf, y)
        y = self._draw_divider(surf, y)
        y = self._draw_resources(surf, y)
        y = self._draw_divider(surf, y)
        y = self._draw_economy(surf, y)
        y = self._draw_divider(surf, y)
        y = self._draw_daemons(surf, y)
        y = self._draw_divider(surf, y)
        self._draw_turn(surf, y)

    def _draw_site_name(self, surf: pygame.Surface, y: int) -> int:
        """Render site name header.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor after drawing.
        """
        name = self._fs.root.metadata.get("site", "UNKNOWN SITE")
        # Word-wrap to panel width
        words     = name.split()
        lines: list[str] = []
        current   = ""
        max_chars = (self._width - self._padding * 2) // max(1, self._small.size("M")[0])
        for word in words:
            if len(current) + len(word) + 1 <= max_chars:
                current = (current + " " + word).strip()
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        for line in lines:
            self._blit_text(surf, line, y, _GREEN_BRIGHT, self._small)
            y += self._small.get_linesize()
        return y + 4

    def _draw_resources(self, surf: pygame.Surface, y: int) -> int:
        """Render the three resource bars.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor.
        """
        for resource in Resource:
            ratio   = self._rm.ratio(resource)
            current = self._rm.current(resource)
            maximum = self._rm.maximum(resource)
            colour  = _BAR_COLOURS[resource]

            # Label
            label = f"{resource.name:<8}"
            self._blit_text(surf, label, y, _GREEN_DIM, self._small)

            # Bar background
            bar_x = self._padding
            bar_y = y + self._small.get_linesize()
            pygame.draw.rect(surf, _GREEN_FAINT,
                             (bar_x, bar_y, _BAR_WIDTH, _BAR_HEIGHT))
            # Bar fill
            fill_w = max(0, int(_BAR_WIDTH * ratio))
            if fill_w:
                pygame.draw.rect(surf, colour,
                                 (bar_x, bar_y, fill_w, _BAR_HEIGHT))

            # Value text
            val_text = f"{current:.0f}/{maximum:.0f}"
            self._blit_text(surf, val_text,
                            bar_y + _BAR_HEIGHT + 2,
                            _GREEN_DIM, self._small)

            y += self._small.get_linesize() + _BAR_HEIGHT + self._small.get_linesize() + 6

        return y + _SECTION_GAP

    def _draw_economy(self, surf: pygame.Surface, y: int) -> int:
        """Render credits and artifact count.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor.
        """
        credits_text = f"CREDITS   {self._arts.currency:.0f}"
        self._blit_text(surf, credits_text, y, _GREEN_BRIGHT, self._small)
        y += self._small.get_linesize() + 4

        collected = len(self._arts.collected())
        total     = len(self._arts.all_artifacts())
        art_text  = f"ARTIFACTS {collected} / {total}"
        self._blit_text(surf, art_text, y, _GREEN_DIM, self._small)
        y += self._small.get_linesize()

        return y + _SECTION_GAP

    def _draw_daemons(self, surf: pygame.Surface, y: int) -> int:
        """Render daemon list with alert state indicators.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor.
        """
        self._blit_text(surf, "DAEMONS", y, _GREEN_DIM, self._small)
        y += self._small.get_linesize() + 2

        daemons = self._ds.all_daemons()
        if not daemons:
            self._blit_text(surf, "  none detected", y, _GREEN_FAINT, self._small)
            y += self._small.get_linesize()
        else:
            for daemon in daemons:
                state_colour = _ALERT_COLOURS[daemon.alert_state]
                # Truncate name to fit
                name = daemon.name[:12]
                state = daemon.alert_state.name if not daemon.pacified else "PACIFIED"
                line  = f"  {name:<12} {state}"
                self._blit_text(surf, line, y, state_colour, self._small)
                y += self._small.get_linesize() + 2

        return y + _SECTION_GAP

    def _draw_turn(self, surf: pygame.Surface, y: int) -> int:
        """Render turn counter.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor.
        """
        self._blit_text(surf, f"TURN  {self._turn}", y, _GREEN_FAINT, self._small)
        return y + self._small.get_linesize()

    def _draw_divider(self, surf: pygame.Surface, y: int) -> int:
        """Draw a horizontal separator line.

        Parameters
        ----------
        surf:
            Target surface.
        y:
            Current Y cursor.

        Returns
        -------
        int
            Updated Y cursor after the divider.
        """
        dy = y + 4
        pygame.draw.line(
            surf, _BORDER,
            (self._padding, dy),
            (self._width - self._padding, dy),
        )
        return dy + 8

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _blit_text(
        self,
        surf:   pygame.Surface,
        text:   str,
        y:      int,
        colour: tuple[int, int, int],
        font:   pygame.font.Font,
    ) -> None:
        """Render *text* at (padding, y) in *colour* using *font*.

        Parameters
        ----------
        surf:
            Target surface.
        text:
            String to render.
        y:
            Y coordinate.
        colour:
            RGB colour.
        font:
            Font to use.
        """
        rendered = font.render(text, True, colour)
        surf.blit(rendered, (self._padding, y))

    # ------------------------------------------------------------------
    # Event subscribers
    # ------------------------------------------------------------------

    def _on_resource_changed(self, event: object) -> None:
        """Trigger a redraw hint on resource change (no-op — draw() is called
        every frame anyway; this exists for future dirty-flag optimisation)."""
        pass

    def _on_daemon_alert(self, event: object) -> None:
        """No visual change needed here — terminal.py handles the alert text."""
        pass

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
            A loaded font object.
        """
        if font_path:
            from pathlib import Path
            p = Path(font_path)
            if p.exists():
                try:
                    return pygame.font.Font(str(p), size)
                except Exception as exc:
                    log.warning("Font load failed %r: %s", font_path, exc)
        return pygame.font.SysFont("monospace", size)