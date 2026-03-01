"""
gamestates/gameplay.py
======================
Core gameplay state for DIG.EXCAVATION.

Responsibilities
----------------
- Instantiate and own all game systems for a single run.
- Own the terminal, status panel, and toolbar UI widgets.
- Drive the per-turn loop: advance_turn → tick systems → flush events.
- Handle pause and menu-return via toolbar.
- Pass player input to the terminal and command handler.
- Never contain rendering logic beyond layout — delegate to UI widgets.

Layout
------
    ┌─────────────────────────────────────────────┐
    │                  toolbar                    │  Toolbar.HEIGHT px
    ├────────────────────────────┬────────────────┤
    │                            │                │
    │        terminal            │  status panel  │
    │      (left portion)        │  (right 200px) │
    │                            │                │
    └────────────────────────────┴────────────────┘

Turn flow (per player command)
------------------------------
    1. Terminal receives keypress → returns completed input string.
    2. CommandHandler.execute() → CommandResult.
    3. Terminal.print_result() displays output.
    4. event_queue.advance_turn() promotes deferred events.
    5. Filesystem.tick(), ResourceManager.tick(), DaemonSystem.tick().
    6. event_queue.flush() delivers all events (terminal auto-prints alerts).
    7. StatusPanel redraws with updated state.
"""

from __future__ import annotations

import logging
from typing import Optional

import pygame

from gamestates.base_state import BaseState
from systems.artifact import Artifact, ArtifactRarity, ArtifactSystem
from systems.command_handler import CommandHandler
from systems.daemon import Daemon, DaemonPersonality, DaemonSystem
from systems.event_queue import EventType, event_queue
from systems.filesystem import Filesystem
from systems.resource_manager import ResourceManager
from ui.status_panel import StatusPanel
from ui.terminal import Terminal
from ui.toolbar import Toolbar, ToolbarEvent
from world.node import Node, NodeType, NodeVisibility
from world.site_generator import PROFILE_CORPORATE, SiteGenerator

log = logging.getLogger(__name__)

# Width reserved for the status panel on the right
_PANEL_WIDTH = 200


class GameplayState(BaseState):
    """Owns all systems and UI for one dig-site run.

    Parameters
    ----------
    screen_width:
        Display width in pixels.
    screen_height:
        Display height in pixels.
    font_path:
        Optional path to a monospace .ttf font shared across all widgets.
    seed:
        RNG seed for site generation.  Pass None for a random seed.

    Usage (inside Game)
    -------------------
        state = GameplayState(screen_width=1000, screen_height=700)
        state.on_enter()

        # Game loop:
        done = state.update(events, screen)
        if done:
            game.pop_state()   # return to menu
    """

    def __init__(
        self,
        screen_width:  int,
        screen_height: int,
        font_path:     Optional[str] = None,
        seed:          Optional[int] = None,
    ) -> None:
        self._sw        = screen_width
        self._sh        = screen_height
        self._font_path = font_path
        self._seed      = seed if seed is not None else _random_seed()

        self._paused:      bool = False
        self._return_menu: bool = False   # set True to signal Game to pop state

        # Systems — initialised in on_enter()
        self._rm:  Optional[ResourceManager] = None
        self._fs:  Optional[Filesystem]      = None
        self._ds:  Optional[DaemonSystem]    = None
        self._arts: Optional[ArtifactSystem] = None
        self._cmd: Optional[CommandHandler]  = None

        # UI widgets — initialised in on_enter()
        self._toolbar: Optional[Toolbar]     = None
        self._terminal: Optional[Terminal]   = None
        self._panel:    Optional[StatusPanel] = None

        # Layout rects (computed in on_enter)
        self._terminal_rect: pygame.Rect = pygame.Rect(0, 0, 0, 0)
        self._panel_rect:    pygame.Rect = pygame.Rect(0, 0, 0, 0)

    # ------------------------------------------------------------------
    # BaseState interface
    # ------------------------------------------------------------------

    def on_enter(self) -> None:
        """Initialise all systems and UI for a new run."""
        log.info("GameplayState.on_enter — seed=%d", self._seed)

        # --- Systems ---
        self._rm   = ResourceManager(power=100, memory=50, energy=80)
        gen        = SiteGenerator(profile=PROFILE_CORPORATE, seed=self._seed)
        self._fs   = gen.generate()
        self._ds   = DaemonSystem(
            resource_manager=self._rm,
            nodes={n.node_id: n for n in self._fs.all_nodes()},
            rng_seed=self._seed,
        )
        self._arts = ArtifactSystem(resource_manager=self._rm)
        self._cmd  = CommandHandler(
            filesystem       = self._fs,
            resource_manager = self._rm,
            artifact_system  = self._arts,
        )

        # Seed artifacts into the artifact system from the filesystem
        self._register_site_artifacts()

        # Spawn a few daemons
        self._spawn_daemons()

        # --- Layout ---
        toolbar_h = Toolbar.HEIGHT
        terminal_w = self._sw - _PANEL_WIDTH
        content_h  = self._sh - toolbar_h

        self._terminal_rect = pygame.Rect(0, toolbar_h, terminal_w, content_h)
        self._panel_rect    = pygame.Rect(terminal_w, toolbar_h, _PANEL_WIDTH, content_h)

        # --- UI ---
        self._toolbar  = Toolbar(self._sw, font_path=self._font_path)
        self._terminal = Terminal(
            width     = terminal_w,
            height    = content_h,
            font_path = self._font_path,
            prompt    = "DIG> ",
        )
        self._panel = StatusPanel(
            width            = _PANEL_WIDTH,
            height           = content_h,
            resource_manager = self._rm,
            daemon_system    = self._ds,
            artifact_system  = self._arts,
            filesystem       = self._fs,
            font_path        = self._font_path,
        )

        # Subscribe to quit event
        event_queue.subscribe(EventType.QUIT_REQUESTED, self._on_quit)

    def on_exit(self) -> None:
        """Teardown UI subscriptions when leaving this state."""
        if self._terminal:
            self._terminal.teardown()
        if self._panel:
            self._panel.teardown()
        event_queue.unsubscribe(EventType.QUIT_REQUESTED, self._on_quit)
        log.info("GameplayState.on_exit")

    def update(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
    ) -> bool:
        """Process events, run one frame, draw everything.

        Parameters
        ----------
        events:
            pygame event list from the game loop.
        screen:
            Main display surface.

        Returns
        -------
        bool
            ``True`` if the state requests to return to the main menu.
        """
        for event in events:
            # Toolbar first
            tb = self._toolbar.handle_event(event)
            if tb is ToolbarEvent.MENU:
                self._return_menu = True
            elif tb is ToolbarEvent.PAUSE:
                self._paused = not self._paused

            # Pass remaining input to terminal (only when unpaused)
            if not self._paused:
                completed = self._terminal.handle_event(event)
                if completed:
                    self._process_command(completed)

        # Draw
        self._draw(screen)

        result = self._return_menu
        self._return_menu = False
        return result

    # ------------------------------------------------------------------
    # Command processing (one turn per command)
    # ------------------------------------------------------------------

    def _process_command(self, raw: str) -> None:
        """Execute *raw* input and advance the game turn.

        Parameters
        ----------
        raw:
            The raw string the player typed.
        """
        # Execute command
        result = self._cmd.execute(raw)
        self._terminal.print_result(result)

        if self._paused:
            return

        # Advance turn
        event_queue.advance_turn()

        # Tick all systems
        self._fs.tick()
        self._rm.tick()
        self._ds.tick(
            player_node_id = self._fs.cwd.node_id,
            noise_node_id  = self._cmd.last_action_node_id,
        )

        # Deliver all events (terminal auto-prints alerts via subscriptions)
        event_queue.flush()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, screen: pygame.Surface) -> None:
        """Composite all UI widgets onto the screen.

        Parameters
        ----------
        screen:
            Main display surface.
        """
        screen.fill((0, 0, 0))

        # Terminal
        self._terminal.update()
        screen.blit(self._terminal.surface, self._terminal_rect.topleft)

        # Status panel
        self._panel.update(turn=event_queue.turn)
        screen.blit(self._panel.surface, self._panel_rect.topleft)

        # Toolbar (drawn last — sits on top)
        self._toolbar.draw(screen, paused=self._paused)

        # Pause overlay
        if self._paused:
            self._draw_pause_overlay(screen)

    def _draw_pause_overlay(self, screen: pygame.Surface) -> None:
        """Draw a semi-transparent PAUSED overlay.

        Parameters
        ----------
        screen:
            Main display surface.
        """
        overlay = pygame.Surface((self._sw, self._sh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        screen.blit(overlay, (0, 0))

        font  = pygame.font.SysFont("monospace", 32)
        label = font.render("[ PAUSED ]", True, (0, 255, 70))
        cx    = (self._sw - label.get_width())  // 2
        cy    = (self._sh - label.get_height()) // 2
        screen.blit(label, (cx, cy))

    # ------------------------------------------------------------------
    # Site setup helpers
    # ------------------------------------------------------------------

    def _register_site_artifacts(self) -> None:
        """Walk the filesystem and register artifact stubs in ArtifactSystem.

        The site generator seeds ``artifact_id`` on FILE nodes.  Here we
        create matching ``Artifact`` objects and register them so the
        ArtifactSystem can manage their lifecycle.
        """
        for node in self._fs.all_nodes():
            if node.has_artifact:
                artifact = Artifact(
                    artifact_id = node.artifact_id,
                    name        = f"Data Fragment — {node.name}",
                    description = "A recovered piece of the lost digital civilization.",
                    node_id     = node.node_id,
                    rarity      = _pick_rarity(node.corruption),
                )
                self._arts.register(artifact)

    def _spawn_daemons(self) -> None:
        """Place a small set of daemons at random nodes in the filesystem.

        Uses a fixed count for now; future versions can derive count from
        site profile difficulty.
        """
        import random
        rng = random.Random(self._seed + 1)

        # Pick non-root directory nodes as spawn points
        candidates = [
            n for n in self._fs.all_nodes()
            if n.is_directory and not n.is_root
        ]
        if not candidates:
            return

        configs = [
            ("WATCHDOG-1", DaemonPersonality.AGGRESSIVE),
            ("GHOST-2",    DaemonPersonality.PARANOID),
            ("SENTINEL-3", DaemonPersonality.SLEEPY),
        ]

        for name, personality in configs:
            node = rng.choice(candidates)
            daemon = Daemon(
                name        = name,
                personality = personality,
                node_id     = node.node_id,
            )
            self._ds.add_daemon(daemon)
            log.debug("Spawned daemon %r at node %r", name, node.name)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_quit(self, event: object) -> None:
        """Handle QUIT_REQUESTED by flagging return to menu.

        Parameters
        ----------
        event:
            The event object (unused).
        """
        self._return_menu = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_rarity(corruption: float) -> ArtifactRarity:
    """Choose artifact rarity inversely proportional to node corruption.

    Less-corrupted nodes yield rarer artifacts — rewards fast excavation.

    Parameters
    ----------
    corruption:
        Node corruption in ``[0.0, 1.0]``.

    Returns
    -------
    ArtifactRarity
    """
    if corruption < 0.1:
        return ArtifactRarity.LEGENDARY
    if corruption < 0.3:
        return ArtifactRarity.RARE
    if corruption < 0.6:
        return ArtifactRarity.UNCOMMON
    return ArtifactRarity.COMMON


def _random_seed() -> int:
    """Generate a random integer seed.

    Returns
    -------
    int
    """
    import random
    return random.randint(0, 2 ** 32 - 1)