"""
core/game.py
============
Central game loop and state stack manager for DIG.EXCAVATION.

Responsibilities
----------------
- Initialise pygame and create the display surface.
- Own a state stack; delegate update/draw to the active (top) state.
- Handle the pygame quit event and the QUIT_REQUESTED event.
- Run at a fixed FPS cap; pass delta time if states need it later.
- Never contain gameplay logic.

State stack
-----------
    push_state(state)   Activate a new state on top.
    pop_state()         Return to the previous state.

The active state is always stack[-1].  When the stack is empty the loop
exits.
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

import pygame

from systems.event_queue import EventType, event_queue

log = logging.getLogger(__name__)

_FPS = 60


class Game:
    """Pygame application shell with a state stack.

    Parameters
    ----------
    screen_width:
        Display width in pixels.
    screen_height:
        Display height in pixels.
    title:
        Window caption.
    font_path:
        Optional shared font path passed to states.

    Usage
    -----
        game = Game(screen_width=1000, screen_height=700)
        game.push_state(MenuState(...))
        game.run()
    """

    def __init__(
        self,
        screen_width:  int = 1000,
        screen_height: int = 700,
        title:         str = "DIG.EXCAVATION",
        font_path:     Optional[str] = None,
    ) -> None:
        pygame.init()
        pygame.display.set_caption(title)

        self._screen = pygame.display.set_mode((screen_width, screen_height))
        self._clock  = pygame.time.Clock()
        self._sw     = screen_width
        self._sh     = screen_height
        self._font_path = font_path

        self._stack:   list = []
        self._running: bool = False

        event_queue.subscribe(EventType.QUIT_REQUESTED, self._on_quit_requested)

    # ------------------------------------------------------------------
    # State stack
    # ------------------------------------------------------------------

    def push_state(self, state) -> None:
        """Push *state* onto the stack and call its on_enter().

        Parameters
        ----------
        state:
            Any object implementing ``on_enter()``, ``on_exit()``, and
            ``update(events, screen) -> any``.
        """
        if self._stack:
            log.debug("Suspending state %r", self._stack[-1])
        self._stack.append(state)
        state.on_enter()
        log.debug("Pushed state %r", state)

    def pop_state(self) -> None:
        """Pop the top state and call its on_exit().

        If the stack becomes empty the game loop will exit on the next
        iteration.
        """
        if not self._stack:
            return
        state = self._stack.pop()
        state.on_exit()
        log.debug("Popped state %r", state)
        if self._stack:
            log.debug("Resuming state %r", self._stack[-1])

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the game loop.  Blocks until the game exits."""
        self._running = True
        log.info("Game loop started")

        while self._running and self._stack:
            events = pygame.event.get()

            # Handle window close button
            for event in events:
                if event.type == pygame.QUIT:
                    self._running = False

            if not self._running:
                break

            # Flush any deferred events from last frame
            event_queue.flush()

            # Delegate to active state
            active = self._stack[-1]
            result = active.update(events, self._screen)

            # GameplayState returns True when it wants to go back to menu
            if result:
                self.pop_state()
                # If stack is now empty, re-push the menu
                if not self._stack:
                    self._running = False

            pygame.display.flip()
            self._clock.tick(_FPS)

        self._shutdown()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_quit_requested(self, event: object) -> None:
        """Handle QUIT_REQUESTED from event_queue.

        Parameters
        ----------
        event:
            The event object (unused).
        """
        self._running = False

    def _shutdown(self) -> None:
        """Teardown pygame and exit cleanly."""
        log.info("Shutting down")
        while self._stack:
            self.pop_state()
        event_queue.unsubscribe(EventType.QUIT_REQUESTED, self._on_quit_requested)
        pygame.quit()
        sys.exit(0)