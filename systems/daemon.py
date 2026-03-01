"""
systems/daemon.py
=================
Security daemon AI for DIG.EXCAVATION.

Responsibilities
----------------
- Own the state and behaviour of every daemon on a dig site.
- Move daemons through the filesystem each turn.
- Detect the player and escalate alert level.
- Corrupt nodes and drain resources on contact.
- Post events so the UI and other systems can react.
- Never render anything; never import pygame.

Daemon personalities
--------------------
AGGRESSIVE  Moves toward the player every turn once alerted.
PARANOID    Patrols randomly but has a wide detection radius.
SLEEPY      Stationary most turns; slow to alert but hard to pacify once active.

Alert states
------------
IDLE        Daemon is unaware of the player.
SUSPICIOUS  Daemon detected indirect signs (node accessed nearby).
ALERT       Daemon is actively hunting; moves toward player every turn.

The alert level escalates automatically and decays slowly when the player
stays out of detection range.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from systems.event_queue import EventType, event_queue
from systems.resource_manager import Resource, ResourceManager
from world.node import Node

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DaemonPersonality(Enum):
    """Behavioural archetype of a daemon."""
    AGGRESSIVE = "aggressive"
    PARANOID   = "paranoid"
    SLEEPY     = "sleepy"


class AlertState(Enum):
    """How aware the daemon currently is of the player."""
    IDLE       = "idle"
    SUSPICIOUS = "suspicious"
    ALERT      = "alert"


# ---------------------------------------------------------------------------
# Daemon dataclass
# ---------------------------------------------------------------------------

@dataclass
class Daemon:
    """A single security daemon living in the virtual filesystem.

    Parameters
    ----------
    name:
        Display name shown to the player (e.g. ``"WATCHDOG-7"``).
    personality:
        Determines movement and detection behaviour.
    node_id:
        The ``node_id`` of the filesystem node the daemon currently occupies.
    detection_radius:
        How many directory hops away the daemon can detect the player.
        PARANOID daemons have this boosted at runtime.
    alert_state:
        Current awareness level.
    alert_level:
        Float in ``[0.0, 1.0]`` tracking how close to full ALERT the daemon
        is.  Crosses thresholds to change ``alert_state``.
    move_cooldown:
        Turns between moves.  SLEEPY daemons have a high cooldown.
    turns_since_move:
        Internal counter for move cooldown.
    pacified:
        If ``True`` the daemon has been neutralised and skips all behaviour.
    daemon_id:
        Unique identifier.
    """

    name:             str
    personality:      DaemonPersonality
    node_id:          str
    detection_radius: int               = 2
    alert_state:      AlertState        = AlertState.IDLE
    alert_level:      float             = 0.0
    move_cooldown:    int               = 1
    turns_since_move: int               = 0
    pacified:         bool              = False
    daemon_id:        str               = field(default_factory=lambda: (
        f"daemon_{id(object())}"
    ))

    # Alert thresholds
    _SUSPICIOUS_THRESHOLD: float = field(default=0.4, init=False, repr=False)
    _ALERT_THRESHOLD:      float = field(default=0.75, init=False, repr=False)

    def __repr__(self) -> str:
        return (
            f"<Daemon {self.name!r} {self.personality.name} "
            f"state={self.alert_state.name} node={self.node_id[:8]}>"
        )


# ---------------------------------------------------------------------------
# Daemon system
# ---------------------------------------------------------------------------

class DaemonSystem:
    """Manages all daemons on a dig site.

    Parameters
    ----------
    resource_manager:
        Used to drain player resources on daemon contact.
    nodes:
        The flat node registry from the active ``Filesystem``.  The daemon
        system needs it to resolve adjacency for movement and detection.
    rng_seed:
        Seed for the internal RNG (keep deterministic with the site seed).

    Usage
    -----
        ds = DaemonSystem(resource_manager=rm, nodes=fs_nodes, rng_seed=42)
        ds.add_daemon(Daemon(name="WATCHDOG-7", personality=DaemonPersonality.AGGRESSIVE,
                             node_id=some_node_id))

        # Each turn, after filesystem.tick():
        ds.tick(player_node_id=fs.cwd.node_id)
    """

    # Alert gain per turn when player is within detection radius
    _ALERT_GAIN_NEAR:   float = 0.2
    # Alert gain when player acted in an adjacent node (indirect detection)
    _ALERT_GAIN_NOISE:  float = 0.1
    # Alert decay per turn when player is out of range
    _ALERT_DECAY:       float = 0.05
    # Power drained from player per turn while daemon shares their node
    _CONTACT_DRAIN:     float = 10.0
    # Corruption added to a node when a daemon occupies it
    _DAEMON_CORRUPTION: float = 0.05

    def __init__(
        self,
        resource_manager: ResourceManager,
        nodes:            dict[str, Node],
        rng_seed:         int = 0,
    ) -> None:
        self._rm:      ResourceManager      = resource_manager
        self._nodes:   dict[str, Node]      = nodes
        self._daemons: dict[str, Daemon]    = {}
        self._rng:     random.Random        = random.Random(rng_seed)

    # ------------------------------------------------------------------
    # Daemon registration
    # ------------------------------------------------------------------

    def add_daemon(self, daemon: Daemon) -> None:
        """Register a daemon with the system.

        Parameters
        ----------
        daemon:
            The ``Daemon`` instance to add.  Its ``node_id`` must exist in
            the node registry passed at construction.
        """
        if daemon.node_id not in self._nodes:
            raise ValueError(
                f"Daemon {daemon.name!r} node_id {daemon.node_id!r} not in node registry."
            )
        # Apply personality modifiers
        if daemon.personality is DaemonPersonality.PARANOID:
            daemon.detection_radius += 1
        elif daemon.personality is DaemonPersonality.SLEEPY:
            daemon.move_cooldown = 3

        self._daemons[daemon.daemon_id] = daemon
        log.debug("Registered daemon %r at node %r", daemon.name, daemon.node_id[:8])

    def remove_daemon(self, daemon_id: str) -> None:
        """Remove a daemon from the system (e.g. after pacification).

        Parameters
        ----------
        daemon_id:
            The ``daemon_id`` of the daemon to remove.
        """
        self._daemons.pop(daemon_id, None)

    # ------------------------------------------------------------------
    # Per-turn update
    # ------------------------------------------------------------------

    def tick(self, player_node_id: str, noise_node_id: Optional[str] = None) -> None:
        """Advance all daemon behaviours by one turn.

        Parameters
        ----------
        player_node_id:
            The ``node_id`` of the node the player currently occupies.
        noise_node_id:
            Optional ``node_id`` where the player just performed an action
            (SCAN, CARVE, etc.).  Creates indirect detection pressure on
            nearby daemons even if they can't directly see the player.
        """
        for daemon in list(self._daemons.values()):
            if daemon.pacified:
                continue

            self._update_alert(daemon, player_node_id, noise_node_id)
            self._maybe_move(daemon, player_node_id)
            self._apply_contact_effects(daemon, player_node_id)
            self._corrupt_current_node(daemon)

    # ------------------------------------------------------------------
    # Alert management
    # ------------------------------------------------------------------

    def _update_alert(
        self,
        daemon:         Daemon,
        player_node_id: str,
        noise_node_id:  Optional[str],
    ) -> None:
        """Update *daemon*'s alert level based on player proximity.

        Parameters
        ----------
        daemon:
            The daemon to update.
        player_node_id:
            Current player node.
        noise_node_id:
            Where the player last acted (may be None).
        """
        dist = self._distance(daemon.node_id, player_node_id)

        if dist <= daemon.detection_radius:
            gain = self._ALERT_GAIN_NEAR
            # PARANOID daemons gain alert faster
            if daemon.personality is DaemonPersonality.PARANOID:
                gain *= 1.5
        elif noise_node_id and self._distance(daemon.node_id, noise_node_id) <= daemon.detection_radius + 1:
            gain = self._ALERT_GAIN_NOISE
        else:
            gain = -self._ALERT_DECAY
            # SLEEPY daemons decay back to IDLE faster
            if daemon.personality is DaemonPersonality.SLEEPY:
                gain *= 2.0

        previous_state = daemon.alert_state
        daemon.alert_level = max(0.0, min(1.0, daemon.alert_level + gain))

        # State transitions
        if daemon.alert_level >= daemon._ALERT_THRESHOLD:
            daemon.alert_state = AlertState.ALERT
        elif daemon.alert_level >= daemon._SUSPICIOUS_THRESHOLD:
            daemon.alert_state = AlertState.SUSPICIOUS
        else:
            daemon.alert_state = AlertState.IDLE

        if daemon.alert_state != previous_state:
            self._post_alert_event(daemon, previous_state)

    def _post_alert_event(self, daemon: Daemon, previous: AlertState) -> None:
        """Post the appropriate event when a daemon changes alert state.

        Parameters
        ----------
        daemon:
            The daemon whose state changed.
        previous:
            The state before the change.
        """
        if daemon.alert_state is AlertState.SUSPICIOUS and previous is AlertState.IDLE:
            event_queue.post_immediate(
                EventType.DAEMON_SPOTTED,
                {"daemon_id": daemon.daemon_id, "name": daemon.name,
                 "node_id": daemon.node_id},
                source="DaemonSystem",
            )
        elif daemon.alert_state is AlertState.ALERT:
            event_queue.post_immediate(
                EventType.DAEMON_ALERT,
                {"daemon_id": daemon.daemon_id, "name": daemon.name,
                 "node_id": daemon.node_id},
                source="DaemonSystem",
            )
        log.debug(
            "Daemon %r: %s -> %s", daemon.name, previous.name, daemon.alert_state.name
        )

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def _maybe_move(self, daemon: Daemon, player_node_id: str) -> None:
        """Move *daemon* if its cooldown has elapsed.

        Movement target depends on personality and alert state:
        - ALERT AGGRESSIVE / PARANOID: move toward the player.
        - IDLE / SUSPICIOUS: patrol randomly among adjacent nodes.
        - SLEEPY IDLE: stay put most turns.

        Parameters
        ----------
        daemon:
            The daemon to potentially move.
        player_node_id:
            Player's current node (used for pursuit).
        """
        daemon.turns_since_move += 1
        if daemon.turns_since_move < daemon.move_cooldown:
            return
        daemon.turns_since_move = 0

        neighbours = self._neighbours(daemon.node_id)
        if not neighbours:
            return

        if daemon.alert_state is AlertState.ALERT and daemon.personality in (
            DaemonPersonality.AGGRESSIVE, DaemonPersonality.PARANOID
        ):
            # Move toward player — pick neighbour closest to player
            target_id = min(
                neighbours,
                key=lambda nid: self._distance(nid, player_node_id),
            )
        else:
            # Random patrol
            target_id = self._rng.choice(neighbours)

        daemon.node_id = target_id
        log.debug("Daemon %r moved to node %r", daemon.name, target_id[:8])

    # ------------------------------------------------------------------
    # Contact effects
    # ------------------------------------------------------------------

    def _apply_contact_effects(self, daemon: Daemon, player_node_id: str) -> None:
        """Drain player resources when daemon shares the player's node.

        Parameters
        ----------
        daemon:
            The daemon to check.
        player_node_id:
            Current player node.
        """
        if daemon.node_id != player_node_id:
            return
        drained = self._rm.consume(
            Resource.POWER, self._CONTACT_DRAIN, source=daemon.name
        )
        if drained:
            log.debug("Daemon %r draining player power", daemon.name)

    def _corrupt_current_node(self, daemon: Daemon) -> None:
        """Apply daemon-induced corruption to the node it occupies.

        Parameters
        ----------
        daemon:
            The daemon whose current node should be corrupted.
        """
        node = self._nodes.get(daemon.node_id)
        if node:
            node.apply_corruption(self._DAEMON_CORRUPTION)

    # ------------------------------------------------------------------
    # Pacification
    # ------------------------------------------------------------------

    def pacify(self, daemon_id: str) -> bool:
        """Attempt to pacify a daemon (neutralise it).

        Sets ``daemon.pacified = True`` and posts ``DAEMON_PACIFIED``.
        Currently always succeeds; future versions can add access-code
        validation logic here.

        Parameters
        ----------
        daemon_id:
            The daemon to pacify.

        Returns
        -------
        bool
            ``True`` if pacification succeeded.
        """
        daemon = self._daemons.get(daemon_id)
        if daemon is None:
            return False

        daemon.pacified    = True
        daemon.alert_state = AlertState.IDLE
        daemon.alert_level = 0.0

        event_queue.post_immediate(
            EventType.DAEMON_PACIFIED,
            {"daemon_id": daemon.daemon_id, "name": daemon.name},
            source="DaemonSystem",
        )
        log.info("Daemon %r pacified", daemon.name)
        return True

    # ------------------------------------------------------------------
    # Graph utilities
    # ------------------------------------------------------------------

    def _neighbours(self, node_id: str) -> list[str]:
        """Return adjacent node ids (parent + children) for *node_id*.

        Parameters
        ----------
        node_id:
            The node whose neighbours to find.

        Returns
        -------
        list[str]
            List of adjacent node ids the daemon can move to.
        """
        node = self._nodes.get(node_id)
        if node is None:
            return []
        adjacent = list(node.children_ids)
        if node.parent_id:
            adjacent.append(node.parent_id)
        return [nid for nid in adjacent if nid in self._nodes]

    def _distance(self, from_id: str, to_id: str) -> int:
        """BFS hop count between two nodes in the filesystem tree.

        Returns a large sentinel value (999) if no path exists.

        Parameters
        ----------
        from_id:
            Starting node id.
        to_id:
            Target node id.

        Returns
        -------
        int
            Number of hops between the two nodes.
        """
        if from_id == to_id:
            return 0

        visited = {from_id}
        queue   = [(from_id, 0)]

        while queue:
            current_id, dist = queue.pop(0)
            for nid in self._neighbours(current_id):
                if nid == to_id:
                    return dist + 1
                if nid not in visited:
                    visited.add(nid)
                    queue.append((nid, dist + 1))

        return 999   # unreachable

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def daemons_at(self, node_id: str) -> list[Daemon]:
        """Return all active (non-pacified) daemons at *node_id*.

        Parameters
        ----------
        node_id:
            The node to query.
        """
        return [
            d for d in self._daemons.values()
            if d.node_id == node_id and not d.pacified
        ]

    def all_daemons(self) -> list[Daemon]:
        """Return all registered daemons regardless of state."""
        return list(self._daemons.values())

    def __repr__(self) -> str:
        active = sum(1 for d in self._daemons.values() if not d.pacified)
        return f"<DaemonSystem daemons={len(self._daemons)} active={active}>"