"""
core/game.py — Main game class for dig_excavation.

Owns the pygame + OpenGL display, the gamestate stack, and the main loop.
Nothing else in the codebase should call pygame.display or manage the clock
— all of that lives here.

Gamestate stack semantics:
    - The TOP of the stack is the active state (receives input + update + draw).
    - States underneath are paused but kept in memory (e.g. excavation under pause).
    - push_state()  : enter a new state on top (e.g. open pause menu).
    - pop_state()   : return to the state below (e.g. close pause menu).
    - change_state(): replace the entire stack with a single new state
                      (e.g. main menu → excavation, no going back).

Architecture note:
    Game never imports concrete state classes directly — states are pushed in
    from outside (main.py) or by other states via the context reference they
    receive. This keeps game.py decoupled from game logic.
"""

import sys
import pygame
from pygame.locals import DOUBLEBUF, OPENGL, FULLSCREEN, RESIZABLE, VIDEORESIZE

import config
from core.monitor_view import MonitorView
from gamestates.base_state import BaseState


class Game:
    """
    Central controller: display, clock, gamestate stack, and main loop.

    Attributes:
        screen:         The pygame display surface (OpenGL-backed).
        terminal_surf:  Offscreen pygame.Surface that states draw onto.
                        This is what gets texture-mapped onto the 3D quad.
        monitor:        MonitorView instance — owns all OpenGL calls.
        running:        Set to False to exit the main loop cleanly.
    """

    def __init__(self) -> None:
        """
        Initialise pygame and OpenGL display, create core subsystems.
        Does not start the loop — call run() for that.
        """
        pygame.init()
        pygame.display.set_caption(config.WINDOW_TITLE)

        # OpenGL requires DOUBLEBUF | OPENGL flags.
        # FULLSCREEN is the default; resize support is added via RESIZABLE
        # in windowed mode (toggled from settings).
        self.screen: pygame.Surface = pygame.display.set_mode(
            (config.DEFAULT_WIDTH, config.DEFAULT_HEIGHT),
            DOUBLEBUF | OPENGL | FULLSCREEN,
        )

        # The offscreen surface all states draw onto — pure pygame, no OpenGL.
        # MonitorView uploads this as a texture each frame.
        self.terminal_surf: pygame.Surface = pygame.Surface(
            (config.TERMINAL_WIDTH, config.TERMINAL_HEIGHT)
        )

        self.monitor = MonitorView()
        self.monitor.init()

        self.clock = pygame.time.Clock()
        self.running: bool = True

        # Gamestate stack — index -1 is always the active state.
        self._state_stack: list[BaseState] = []

    # ------------------------------------------------------------------
    # Gamestate stack management
    # ------------------------------------------------------------------

    def push_state(self, state: BaseState) -> None:
        """
        Push a new state onto the stack and activate it.
        The previous state is paused (on_pause called) but stays in memory.

        Args:
            state: An initialised BaseState subclass instance.
        """
        if self._state_stack:
            self._state_stack[-1].on_pause()
        self._state_stack.append(state)
        state.on_enter(self)

    def pop_state(self) -> None:
        """
        Remove the active state and resume the one below it.
        Exits the game if the stack becomes empty.
        """
        if not self._state_stack:
            return

        leaving = self._state_stack.pop()
        leaving.on_exit()

        if self._state_stack:
            self._state_stack[-1].on_resume()
        else:
            # Nothing left to show — quit cleanly.
            self.quit()

    def change_state(self, state: BaseState) -> None:
        """
        Clear the entire stack and start fresh with a single new state.
        Use this for transitions where returning to the previous state
        makes no sense (e.g. main menu → new game).

        Args:
            state: An initialised BaseState subclass instance.
        """
        while self._state_stack:
            self._state_stack.pop().on_exit()
        self.push_state(state)

    @property
    def active_state(self) -> BaseState | None:
        """The currently running state, or None if the stack is empty."""
        return self._state_stack[-1] if self._state_stack else None

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def set_fullscreen(self, fullscreen: bool) -> None:
        """
        Toggle between fullscreen and resizable windowed mode at runtime.

        Args:
            fullscreen: True for fullscreen, False for windowed.
        """
        flags = DOUBLEBUF | OPENGL
        if fullscreen:
            flags |= FULLSCREEN
            size = (config.DEFAULT_WIDTH, config.DEFAULT_HEIGHT)
        else:
            flags |= RESIZABLE
            size = (config.DEFAULT_WIDTH, config.DEFAULT_HEIGHT)

        self.screen = pygame.display.set_mode(size, flags)
        self.monitor.resize(*size)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the main game loop. Blocks until self.running is False.

        Loop order each frame:
            1. Event pump  — gather OS + pygame events, dispatch to active state.
            2. Update      — advance game logic by delta time.
            3. Draw        — state draws onto terminal_surf.
            4. Render      — MonitorView uploads terminal_surf and draws the 3D scene.
            5. Flip        — swap OpenGL buffers.
            6. Tick        — cap FPS, compute delta.
        """
        while self.running:
            dt = self.clock.tick(config.DEFAULT_FPS) / 1000.0  # seconds

            self._handle_events()

            if self.active_state:
                self.active_state.update(dt)
                self.active_state.draw(self.terminal_surf)

            self.monitor.render(self.terminal_surf)
            pygame.display.flip()

        self._shutdown()

    def quit(self) -> None:
        """Signal the main loop to exit on the next iteration."""
        self.running = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _handle_events(self) -> None:
        """
        Pump the pygame event queue and dispatch to the active state.
        Handles a small set of global events (quit, resize) before forwarding.
        """
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit()
                return

            if event.type == VIDEORESIZE:
                self.monitor.resize(event.w, event.h)

            if self.active_state:
                self.active_state.handle_event(event)

    def _shutdown(self) -> None:
        """
        Release all resources in reverse-initialisation order and exit.
        Called automatically at the end of run().
        """
        # Exit remaining states cleanly
        while self._state_stack:
            self._state_stack.pop().on_exit()

        self.monitor.cleanup()
        pygame.quit()
        sys.exit(0)