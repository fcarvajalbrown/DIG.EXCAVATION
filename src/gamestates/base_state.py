"""
gamestates/base_state.py — Abstract base class for all game states.

Every concrete state (menu, excavation, pause, etc.) must subclass BaseState
and implement its abstract methods. The Game class only ever talks to states
through this interface — no concrete state classes are imported by the engine.

Lifecycle order for a state:
    on_enter()  → called once when the state is pushed onto the stack.
    update()    → called every frame with delta time (seconds).
    draw()      → called every frame to render onto the terminal surface.
    handle_event() → called for each pygame event before update().
    on_pause()  → called when another state is pushed on top of this one.
    on_resume() → called when the state on top is popped and this one is active again.
    on_exit()   → called once when this state is popped or the stack is cleared.

States hold a reference to the Game instance (set in on_enter) so they can
call game.push_state(), game.pop_state(), or game.change_state() to drive
their own transitions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pygame

if TYPE_CHECKING:
    # Imported only for type hints to avoid circular imports at runtime.
    from core.game import Game


class BaseState(ABC):
    """
    Abstract interface every game state must satisfy.

    Attributes:
        game: Reference to the Game instance. Set by on_enter(); None before that.
    """

    def __init__(self) -> None:
        """Initialise the state. The Game reference is not available yet."""
        self.game: Game | None = None

    # ------------------------------------------------------------------
    # Lifecycle hooks — override as needed
    # ------------------------------------------------------------------

    def on_enter(self, game: Game) -> None:
        """
        Called once when this state is pushed onto the stack.
        Store the game reference here; set up any state-specific resources.

        Args:
            game: The central Game instance.
        """
        self.game = game

    def on_exit(self) -> None:
        """
        Called once when this state is removed from the stack (popped or
        stack cleared). Release any resources acquired in on_enter().
        """

    def on_pause(self) -> None:
        """
        Called when another state is pushed on top of this one.
        Use to pause timers, mute audio, or freeze animations.
        """

    def on_resume(self) -> None:
        """
        Called when the state above this one is popped and this state
        becomes active again. Use to resume timers, audio, etc.
        """

    # ------------------------------------------------------------------
    # Per-frame methods — must be implemented by every concrete state
    # ------------------------------------------------------------------

    @abstractmethod
    def handle_event(self, event: pygame.event.Event) -> None:
        """
        Process a single pygame event (keyboard, mouse, window, etc.).
        Called for every event in the queue before update().

        Args:
            event: A pygame event object.
        """

    @abstractmethod
    def update(self, dt: float) -> None:
        """
        Advance game logic by one frame.

        Args:
            dt: Time elapsed since the last frame, in seconds.
                Use this for all time-based calculations to ensure
                frame-rate independent behaviour.
        """

    @abstractmethod
    def draw(self, surface: pygame.Surface) -> None:
        """
        Render the current frame onto the provided surface.
        Do NOT call pygame.display.flip() here — Game owns that.

        Args:
            surface: The shared terminal surface (TERMINAL_WIDTH × TERMINAL_HEIGHT).
                     Clear it first (surface.fill(config.COLOR_BG)) then draw on top.
        """