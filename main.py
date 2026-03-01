"""
main.py
=======
Entry point for DIG.EXCAVATION.

Desktop run:
    python main.py

HTML5 export (itch.io):
    pip install pygbag
    pygbag .

pygbag requires an async main() with asyncio.sleep(0) each frame so the
browser event loop can breathe. The same code runs on desktop unmodified.

No src/ wrapper — run from the project root directly.
"""

from __future__ import annotations

import asyncio
import logging

import pygame

from gamestates.gameplay import GameplayState
from gamestates.menu import MenuAction, MenuState
from gamestates.tutorial import TutorialState

logging.basicConfig(
    level  = logging.WARNING,
    format = "%(levelname)s %(name)s: %(message)s",
)

SCREEN_W  = 1920
SCREEN_H  = 1080
FONT_PATH = None
FPS       = 60


async def main() -> None:
    """Main async entry point — compatible with both desktop and pygbag."""
    pygame.init()
    pygame.display.set_caption("DIG.EXCAVATION")

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    clock  = pygame.time.Clock()

    menu = MenuState(
        screen_width  = SCREEN_W,
        screen_height = SCREEN_H,
        font_path     = FONT_PATH,
    )
    menu.on_enter()

    phase    = "menu"
    tutorial = None
    gameplay = None
    running  = True

    while running:
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False

        if not running:
            break

        # Menu
        if phase == "menu":
            action = menu.update(events, screen)

            if action is MenuAction.NEW_RUN:
                menu.on_exit()
                tutorial = TutorialState(
                    screen_width  = SCREEN_W,
                    screen_height = SCREEN_H,
                    font_path     = FONT_PATH,
                )
                tutorial.on_enter()
                phase = "tutorial"

            elif action is MenuAction.EXIT:
                running = False

        # Tutorial
        elif phase == "tutorial":
            ready = tutorial.update(events, screen)
            if ready:
                tutorial.on_exit()
                gameplay = GameplayState(
                    screen_width  = SCREEN_W,
                    screen_height = SCREEN_H,
                    font_path     = FONT_PATH,
                )
                gameplay.on_enter()
                phase = "gameplay"

        # Gameplay
        elif phase == "gameplay":
            return_to_menu = gameplay.update(events, screen)
            if return_to_menu:
                gameplay.on_exit()
                gameplay = None
                menu.on_enter()
                phase = "menu"

        pygame.display.flip()
        clock.tick(FPS)

        # Required by pygbag
        await asyncio.sleep(0)

    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())