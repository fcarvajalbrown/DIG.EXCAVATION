"""
ui/terminal.py
==============
CRT green-screen terminal renderer for DIG.EXCAVATION.

Responsibilities
----------------
- Render a scrollable output buffer as green phosphor text on a black surface.
- Accept player keyboard input and build an input line with a blinking cursor.
- Return the completed input string when Enter is pressed.
- Apply a scanline overlay and subtle phosphor glow for CRT atmosphere.
- Subscribe to event_queue to auto-print relevant game events.
- Never call game systems directly — it only reads ``CommandResult`` and
  posts nothing (the command handler does that).

Rendering model
---------------
The terminal owns a pygame.Surface it draws onto each frame.  The caller
(game state) blits this surface wherever it wants — the terminal does not
know its own position on screen.

Glow effect
-----------
We fake phosphor glow cheaply: draw the text twice, the second pass at
lower alpha on a slightly larger rect using pygame.transform.scale.
No shaders required.

Font
----
Tries to load a monospace pixel font from ``assets/fonts/``.  Falls back
to pygame's built-in monospace if not found.  Either way the font path is
configurable via ``FONT_PATH`` in ``config.py``.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional

import pygame

from systems.command_handler import CommandResult
from systems.event_queue import EventType, event_queue

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Colour palette — all green phosphor variants
# ---------------------------------------------------------------------------

_BLACK        = (0,   0,   0)
_GREEN_BRIGHT = (0,   255, 70)    # primary text
_GREEN_DIM    = (0,   180, 50)    # secondary / dimmed lines
_GREEN_FAINT  = (0,   80,  20)    # scanline tint, glow base
_GREEN_INPUT  = (80,  255, 120)   # input line highlight
_GREEN_ERROR  = (200, 255, 0)     # yellow-green for errors
_GREEN_CURSOR = (0,   255, 70)    # cursor block

# Scanline alpha (0-255); higher = more visible scanlines
_SCANLINE_ALPHA = 40

# Cursor blink interval in seconds
_CURSOR_BLINK = 0.5

# Maximum lines held in the output buffer
_BUFFER_MAX = 500

# Glow pass alpha
_GLOW_ALPHA = 60


class Terminal:
    """pygame-rendered CRT terminal widget.

    Parameters
    ----------
    width:
        Width of the terminal surface in pixels.
    height:
        Height of the terminal surface in pixels.
    font_path:
        Path to a .ttf monospace font file.  If None or missing, falls back
        to pygame's built-in monospace.
    font_size:
        Font size in points.
    padding:
        Inner padding in pixels on all sides.
    prompt:
        The prompt string shown before the input line (e.g. ``"DIG> "``).

    Usage (inside a game state)
    ---------------------------
        terminal = Terminal(width=800, height=600)

        # Per frame:
        result = terminal.handle_event(pygame_event)
        if result is not None:
            cmd_result = command_handler.execute(result)
            terminal.print_result(cmd_result)
        terminal.update()
        screen.blit(terminal.surface, (0, 0))
    """

    def __init__(
        self,
        width:     int,
        height:    int,
        font_path: Optional[str] = None,
        font_size: int  = 16,
        padding:   int  = 12,
        prompt:    str  = "DIG> ",
    ) -> None:
        self._width   = width
        self._height  = height
        self._padding = padding
        self._prompt  = prompt

        self._font      = self._load_font(font_path, font_size)
        self._line_h    = self._font.get_linesize()
        self._char_w    = self._font.size("M")[0]   # monospace: all chars same width

        # Output buffer — deque for cheap left-pop when full
        self._buffer: deque[tuple[str, tuple[int, int, int]]] = deque(maxlen=_BUFFER_MAX)

        # Current input line (without prompt)
        self._input_line: str = ""

        # Cursor state
        self._cursor_visible: bool  = True
        self._cursor_timer:   float = time.monotonic()

        # Scroll offset (lines from bottom; 0 = showing most recent)
        self._scroll: int = 0

        # Main surface
        self._surface = pygame.Surface((width, height))

        # Scanline overlay — built once, blitted each frame
        self._scanline_surf = self._build_scanline_surface(width, height)

        # Subscribe to events we want to auto-display
        event_queue.subscribe(EventType.DAEMON_ALERT,     self._on_daemon_alert)
        event_queue.subscribe(EventType.RESOURCE_DEPLETED, self._on_resource_depleted)
        event_queue.subscribe(EventType.NODE_CORRUPTED,   self._on_node_corrupted)
        event_queue.subscribe(EventType.ARTIFACT_FOUND,   self._on_artifact_found)

        # Boot message
        self._print_boot_sequence()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def surface(self) -> pygame.Surface:
        """The rendered terminal surface.  Blit this to the screen each frame."""
        return self._surface

    def handle_event(self, event: pygame.event.Event) -> Optional[str]:
        """Process a pygame event and update the input line.

        Parameters
        ----------
        event:
            A pygame event (only ``KEYDOWN`` events are acted on).

        Returns
        -------
        str or None
            The completed input string when Enter is pressed, else None.
        """
        if event.type != pygame.KEYDOWN:
            return None

        if event.key == pygame.K_RETURN:
            completed        = self._input_line.strip()
            self._echo_input(completed)
            self._input_line = ""
            self._scroll     = 0   # snap to bottom on submit
            return completed if completed else None

        elif event.key == pygame.K_BACKSPACE:
            self._input_line = self._input_line[:-1]

        elif event.key == pygame.K_PAGEUP:
            max_scroll = max(0, len(self._buffer) - self._visible_lines())
            self._scroll = min(self._scroll + 3, max_scroll)

        elif event.key == pygame.K_PAGEDOWN:
            self._scroll = max(0, self._scroll - 3)

        elif event.unicode and event.unicode.isprintable():
            self._input_line += event.unicode

        return None

    def print_result(self, result: CommandResult) -> None:
        """Display a ``CommandResult`` in the terminal output buffer.

        Parameters
        ----------
        result:
            The result returned by ``CommandHandler.execute()``.
        """
        colour = _GREEN_BRIGHT if result.success else _GREEN_ERROR
        for line in result.lines:
            self._print(line, colour)

    def print_line(self, text: str, colour: tuple[int, int, int] = _GREEN_BRIGHT) -> None:
        """Print a raw line directly to the output buffer.

        Parameters
        ----------
        text:
            Text to display.
        colour:
            RGB colour tuple.  Defaults to bright green.
        """
        self._print(text, colour)

    def update(self) -> None:
        """Redraw the terminal surface.  Call once per frame."""
        self._update_cursor()
        self._draw()

    def teardown(self) -> None:
        """Unsubscribe from event_queue.  Call from game state's on_exit()."""
        event_queue.unsubscribe(EventType.DAEMON_ALERT,      self._on_daemon_alert)
        event_queue.unsubscribe(EventType.RESOURCE_DEPLETED, self._on_resource_depleted)
        event_queue.unsubscribe(EventType.NODE_CORRUPTED,    self._on_node_corrupted)
        event_queue.unsubscribe(EventType.ARTIFACT_FOUND,    self._on_artifact_found)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:
        """Render everything onto self._surface."""
        surf = self._surface
        surf.fill(_BLACK)

        visible = self._visible_lines()
        # Slice of the buffer to display (newest at bottom)
        buf_list  = list(self._buffer)
        end_idx   = len(buf_list) - self._scroll
        start_idx = max(0, end_idx - visible)
        visible_lines = buf_list[start_idx:end_idx]

        # Reserve bottom two rows: one blank + one input line
        text_rows = visible - 2

        # Draw output lines
        y = self._padding
        for i, (text, colour) in enumerate(visible_lines[-text_rows:]):
            self._draw_text_line(surf, text, y, colour)
            y += self._line_h

        # Separator
        sep_y = self._height - self._padding - self._line_h * 2 - 4
        pygame.draw.line(surf, _GREEN_FAINT, (self._padding, sep_y),
                         (self._width - self._padding, sep_y), 1)

        # Input line
        input_y = self._height - self._padding - self._line_h
        input_text = self._prompt + self._input_line
        self._draw_text_line(surf, input_text, input_y, _GREEN_INPUT)

        # Cursor
        if self._cursor_visible:
            cursor_x = self._padding + len(input_text) * self._char_w
            pygame.draw.rect(
                surf, _GREEN_CURSOR,
                (cursor_x, input_y, self._char_w, self._line_h - 2),
            )

        # Scanline overlay
        surf.blit(self._scanline_surf, (0, 0))

    def _draw_text_line(
        self,
        surf:   pygame.Surface,
        text:   str,
        y:      int,
        colour: tuple[int, int, int],
    ) -> None:
        """Render one line of text with a faint glow pass.

        Parameters
        ----------
        surf:
            Target surface.
        text:
            Text to render.
        y:
            Y coordinate (top of line).
        colour:
            Primary text colour.
        """
        # Glow pass — slightly blurred by scaling up then down
        glow_surf = self._font.render(text, True, _GREEN_FAINT)
        glow_surf.set_alpha(_GLOW_ALPHA)
        gw, gh    = glow_surf.get_size()
        glow_big  = pygame.transform.scale(glow_surf, (gw + 4, gh + 2))
        surf.blit(glow_big, (self._padding - 2, y - 1))

        # Primary text
        text_surf = self._font.render(text, True, colour)
        surf.blit(text_surf, (self._padding, y))

    # ------------------------------------------------------------------
    # Cursor
    # ------------------------------------------------------------------

    def _update_cursor(self) -> None:
        """Toggle cursor visibility based on blink timer."""
        now = time.monotonic()
        if now - self._cursor_timer >= _CURSOR_BLINK:
            self._cursor_visible = not self._cursor_visible
            self._cursor_timer   = now

    # ------------------------------------------------------------------
    # Buffer helpers
    # ------------------------------------------------------------------

    def _print(self, text: str, colour: tuple[int, int, int]) -> None:
        """Append a line to the output buffer, splitting on newlines.

        Parameters
        ----------
        text:
            Text to append.  May contain ``\\n``.
        colour:
            Display colour.
        """
        for line in text.split("\n"):
            self._buffer.append((line, colour))

    def _echo_input(self, text: str) -> None:
        """Echo the player's input back to the buffer before executing.

        Parameters
        ----------
        text:
            The raw input string.
        """
        self._print(f"{self._prompt}{text}", _GREEN_DIM)

    def _visible_lines(self) -> int:
        """Number of text lines that fit in the terminal height."""
        return (self._height - self._padding * 2) // self._line_h

    # ------------------------------------------------------------------
    # Boot sequence
    # ------------------------------------------------------------------

    def _print_boot_sequence(self) -> None:
        """Print the startup banner to the output buffer."""
        lines = [
            "╔══════════════════════════════════════════════╗",
            "║        D I G . E X C A V A T I O N          ║",
            "║     Virtual Artifacts Council — v0.1.0       ║",
            "╚══════════════════════════════════════════════╝",
            "",
            "  Connecting to dig site...",
            "  Filesystem integrity check... OK",
            "  Security sweep... DAEMONS DETECTED",
            "",
            "  Type HELP for available commands.",
            "",
        ]
        for line in lines:
            self._print(line, _GREEN_DIM)

    # ------------------------------------------------------------------
    # Event subscribers
    # ------------------------------------------------------------------

    def _on_daemon_alert(self, event: object) -> None:
        """Display a warning when a daemon enters ALERT state."""
        payload = getattr(event, "payload", {})
        name    = payload.get("name", "UNKNOWN")
        self._print(f"  [!!] ALERT — {name} is actively hunting you!", _GREEN_ERROR)

    def _on_resource_depleted(self, event: object) -> None:
        """Display a warning when a resource hits zero."""
        payload  = getattr(event, "payload", {})
        resource = payload.get("resource", "UNKNOWN")
        self._print(f"  [!!] {resource} DEPLETED", _GREEN_ERROR)

    def _on_node_corrupted(self, event: object) -> None:
        """Display a warning when a node crosses a corruption threshold."""
        payload   = getattr(event, "payload", {})
        name      = payload.get("name", "?")
        threshold = payload.get("threshold", 0)
        if threshold >= 1.0:
            self._print(f"  [!] {name} has fully decayed.", _GREEN_ERROR)
        elif threshold >= 0.75:
            self._print(f"  [!] {name} corruption critical ({threshold:.0%}).", _GREEN_DIM)

    def _on_artifact_found(self, event: object) -> None:
        """Display a notice when an artifact is detected."""
        payload = getattr(event, "payload", {})
        name    = payload.get("name", "?")
        self._print(f"  [*] Artifact signal detected in {name!r}. Run RECON.", _GREEN_BRIGHT)

    # ------------------------------------------------------------------
    # Scanline surface
    # ------------------------------------------------------------------

    @staticmethod
    def _build_scanline_surface(width: int, height: int) -> pygame.Surface:
        """Build a semi-transparent scanline overlay surface.

        Draws a horizontal dark line every two pixels to simulate a CRT
        raster.

        Parameters
        ----------
        width:
            Surface width in pixels.
        height:
            Surface height in pixels.

        Returns
        -------
        pygame.Surface
            A surface with per-pixel alpha ready to blit over the terminal.
        """
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        surf.fill((0, 0, 0, 0))
        for y in range(0, height, 2):
            pygame.draw.line(surf, (0, 0, 0, _SCANLINE_ALPHA), (0, y), (width, y))
        return surf

    # ------------------------------------------------------------------
    # Font loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_font(font_path: Optional[str], size: int) -> pygame.font.Font:
        """Load a font from *font_path*, falling back to pygame's monospace.

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
            path = Path(font_path)
            if path.exists():
                try:
                    return pygame.font.Font(str(path), size)
                except Exception as exc:
                    log.warning("Could not load font %r: %s — using fallback.", font_path, exc)

        # pygame built-in monospace fallback
        return pygame.font.SysFont("monospace", size)