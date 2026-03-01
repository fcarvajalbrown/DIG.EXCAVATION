"""
gamestates/base_state.py
========================
Abstract base class for all game states in DIG.EXCAVATION.

Every state must implement:
    on_enter()              Called once when the state becomes active.
    on_exit()               Called once when the state is popped.
    update(events, screen)  Called every frame; returns state-specific value.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pygame


class BaseState(ABC):
    """Abstract base for all game states."""

    @abstractmethod
    def on_enter(self) -> None:
        """Called when this state is pushed onto the stack."""

    @abstractmethod
    def on_exit(self) -> None:
        """Called when this state is popped from the stack."""

    @abstractmethod
    def update(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
    ) -> Any:
        """Process events and draw one frame.

        Parameters
        ----------
        events:
            pygame event list from the game loop.
        screen:
            Main display surface.

        Returns
        -------
        Any
            State-specific return value (e.g. MenuAction, bool).
        """