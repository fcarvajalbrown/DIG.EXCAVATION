"""
systems/event_queue.py
======================
Turn-aware event queue for DIG.EXCAVATION.

Architecture
------------
This module is intentionally decoupled from pygame, game states, and any
rendering code. It acts as the single message bus that connects game systems
(filesystem, daemons, resources, UI) without them needing direct references
to each other.

Two dispatch modes
------------------
- IMMEDIATE : delivered during the current ``flush()`` call (same turn).
- DEFERRED  : held in a staging buffer and promoted at the *start* of the
              next turn (call ``advance_turn()`` before ``flush()``).

Typical per-turn flow in Game.update()
---------------------------------------
    event_queue.advance_turn()   # promote deferred events
    event_queue.flush()          # deliver all pending events

Subscriber protocol
-------------------
Any callable ``handler(event: Event) -> None`` can subscribe to an event
type.  Handlers are called in registration order; if a handler raises, the
exception propagates and remaining handlers for that event are skipped.
Use ``subscribe`` / ``unsubscribe`` to manage lifetime (e.g., unsubscribe
when a game state exits).
"""

from __future__ import annotations

import enum
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class EventType(enum.Enum):
    """Enumeration of all event types recognised by the game.

    Adding a new event
    ------------------
    1. Add a member here.
    2. Post it via ``EventQueue.post()``.
    3. Subscribe a handler in the relevant system or game state.

    No other files need to change — that is the point of this bus.
    """

    # --- Gameplay ---
    COMMAND_ENTERED   = "command_entered"    # player typed a CLI command
    SCAN_STARTED      = "scan_started"       # SCAN command accepted
    SCAN_COMPLETE     = "scan_complete"      # scan finished (success/fail)
    CARVE_STARTED     = "carve_started"
    CARVE_COMPLETE    = "carve_complete"
    RECONSTRUCT_START = "reconstruct_start"
    RECONSTRUCT_END   = "reconstruct_end"

    # --- Filesystem ---
    NODE_REVEALED     = "node_revealed"      # directory/file becomes visible
    NODE_CORRUPTED    = "node_corrupted"     # data block decays further
    ARTIFACT_FOUND    = "artifact_found"     # recoverable artifact located

    # --- Daemons ---
    DAEMON_SPOTTED    = "daemon_spotted"     # player entered daemon FOV
    DAEMON_ALERT      = "daemon_alert"       # daemon enters active pursuit
    DAEMON_PACIFIED   = "daemon_pacified"    # daemon neutralised

    # --- Resources ---
    RESOURCE_CHANGED  = "resource_changed"   # processing power / memory / energy delta
    RESOURCE_DEPLETED = "resource_depleted"  # a resource hit zero

    # --- Narrative ---
    LOG_ENTRY_ADDED   = "log_entry_added"    # dig log updated with recovered text

    # --- System ---
    TURN_ADVANCED     = "turn_advanced"      # a new game turn has begun
    GAME_STATE_PUSH   = "game_state_push"    # request to push a new state
    GAME_STATE_POP    = "game_state_pop"     # request to pop the current state
    QUIT_REQUESTED    = "quit_requested"     # clean shutdown requested


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

class Timing(enum.Enum):
    """When an event should be delivered."""
    IMMEDIATE = "immediate"
    DEFERRED  = "deferred"   # delivered at the start of the next turn


@dataclass
class Event:
    """Immutable-ish event record passed to subscribers.

    Parameters
    ----------
    type:
        The ``EventType`` that identifies this event.
    payload:
        Arbitrary key-value data relevant to the event.  Prefer flat dicts
        with string keys so handlers can pattern-match without imports.
    timing:
        ``IMMEDIATE`` events are dispatched in the current ``flush()``; 
        ``DEFERRED`` events are held until ``advance_turn()`` promotes them.
    source:
        Optional human-readable tag for debugging (e.g. ``"DaemonSystem"``).
    """

    type:    EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timing:  Timing         = Timing.IMMEDIATE
    source:  str            = ""

    def __repr__(self) -> str:
        src = f" from={self.source!r}" if self.source else ""
        return f"<Event {self.type.name}{src} timing={self.timing.name}>"


# ---------------------------------------------------------------------------
# Handler type alias
# ---------------------------------------------------------------------------

Handler = Callable[[Event], None]


# ---------------------------------------------------------------------------
# EventQueue
# ---------------------------------------------------------------------------

class EventQueue:
    """Central message bus for DIG.EXCAVATION.

    Usage
    -----
    Instantiate once and share via dependency injection or a module-level
    singleton (``systems/event_queue.py`` exports ``event_queue``).

        from systems.event_queue import event_queue, EventType, Event

        # Subscribe
        event_queue.subscribe(EventType.ARTIFACT_FOUND, my_handler)

        # Post (from any system)
        event_queue.post(Event(EventType.ARTIFACT_FOUND, {"id": "arc_001"}))

        # Each game turn (called by Game.update)
        event_queue.advance_turn()
        event_queue.flush()
    """

    def __init__(self) -> None:
        # Pending events ready for dispatch this flush cycle
        self._pending:  list[Event] = []
        # Events waiting for the next advance_turn() call
        self._deferred: list[Event] = []
        # type -> ordered list of handlers
        self._subscribers: defaultdict[EventType, list[Handler]] = defaultdict(list)
        self._turn: int = 0
        self._flushing: bool = False   # re-entrancy guard

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        """Register *handler* to receive events of *event_type*.

        Parameters
        ----------
        event_type:
            The ``EventType`` the handler is interested in.
        handler:
            A callable ``(Event) -> None``.  Registered once; duplicate
            registrations are silently ignored.
        """
        handlers = self._subscribers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        """Remove *handler* from the subscriber list for *event_type*.

        Safe to call even if the handler was never registered.
        """
        handlers = self._subscribers[event_type]
        try:
            handlers.remove(handler)
        except ValueError:
            pass

    def unsubscribe_all(self, handler: Handler) -> None:
        """Remove *handler* from every event type it was registered to.

        Call this in a game state's ``on_exit()`` to avoid dangling
        references.
        """
        for handlers in self._subscribers.values():
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Posting events
    # ------------------------------------------------------------------

    def post(self, event: Event) -> None:
        """Enqueue *event* for dispatch.

        IMMEDIATE events go into the pending buffer (delivered on the next
        ``flush()`` or, if we are already flushing, at the end of the
        current cycle).  DEFERRED events go into the staging buffer and are
        promoted by ``advance_turn()``.

        Parameters
        ----------
        event:
            The ``Event`` to enqueue.
        """
        if event.timing is Timing.DEFERRED:
            self._deferred.append(event)
            log.debug("Enqueued deferred %r", event)
        else:
            self._pending.append(event)
            log.debug("Enqueued immediate %r", event)

    def post_immediate(
        self,
        event_type: EventType,
        payload:    dict[str, Any] | None = None,
        source:     str = "",
    ) -> None:
        """Convenience wrapper to post a simple IMMEDIATE event.

        Parameters
        ----------
        event_type:
            The type of event to post.
        payload:
            Optional data dict.
        source:
            Optional debug label.
        """
        self.post(Event(
            type    = event_type,
            payload = payload or {},
            timing  = Timing.IMMEDIATE,
            source  = source,
        ))

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def advance_turn(self) -> None:
        """Promote all staged DEFERRED events into the pending buffer.

        Call this at the *beginning* of each game turn, before ``flush()``,
        so that events posted at the end of turn N are delivered at the
        start of turn N+1.

        Also posts a ``TURN_ADVANCED`` event so subscribers can react to
        the turn boundary itself.
        """
        self._turn += 1
        log.debug("Turn %d: promoting %d deferred event(s)", self._turn, len(self._deferred))
        self._pending.extend(self._deferred)
        self._deferred.clear()
        self.post_immediate(
            EventType.TURN_ADVANCED,
            {"turn": self._turn},
            source="EventQueue",
        )

    def flush(self) -> None:
        """Dispatch all pending IMMEDIATE events to their subscribers.

        Handles re-entrant posts: if a handler posts new IMMEDIATE events
        during dispatch, they are appended to the working list and delivered
        in the same flush cycle (breadth-first within the turn).

        Events with no subscribers are silently dropped but logged at DEBUG
        level to aid development.
        """
        if self._flushing:
            # flush() called from within a handler — will be picked up by
            # the outer loop automatically.
            return

        self._flushing = True
        try:
            # Iterate by index so appends during dispatch are included.
            i = 0
            while i < len(self._pending):
                event    = self._pending[i]
                handlers = self._subscribers.get(event.type, [])
                if not handlers:
                    log.debug("No subscribers for %r", event)
                else:
                    for handler in handlers:
                        try:
                            handler(event)
                        except Exception:
                            log.exception(
                                "Handler %r raised while processing %r",
                                handler,
                                event,
                            )
                i += 1
        finally:
            self._pending.clear()
            self._flushing = False

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def turn(self) -> int:
        """Current turn counter (incremented by ``advance_turn()``)."""
        return self._turn

    @property
    def pending_count(self) -> int:
        """Number of IMMEDIATE events waiting to be flushed."""
        return len(self._pending)

    @property
    def deferred_count(self) -> int:
        """Number of DEFERRED events waiting for the next turn."""
        return len(self._deferred)

    def __repr__(self) -> str:
        return (
            f"<EventQueue turn={self._turn} "
            f"pending={self.pending_count} deferred={self.deferred_count}>"
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Shared instance.  Import this directly rather than constructing your own.
#:
#:     from systems.event_queue import event_queue
event_queue: EventQueue = EventQueue()