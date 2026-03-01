"""
systems/resource_manager.py
===========================
Tracks the three player resources: processing power, memory, and energy.

Responsibilities
----------------
- Own current and maximum values for each resource.
- Expose ``consume()`` and ``restore()`` for systems that spend/earn resources.
- Post ``RESOURCE_CHANGED`` and ``RESOURCE_DEPLETED`` events via event_queue.
- Never render anything; never import pygame.

Design note
-----------
Resources are intentionally separate from the filesystem and daemon systems.
Any system that needs to spend resources calls ``resource_manager.consume()``
and checks the return value — it never reads resource values directly.
This keeps spending logic centralised and auditable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from systems.event_queue import EventType, event_queue

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resource type
# ---------------------------------------------------------------------------

class Resource(Enum):
    """The three spendable resources."""
    POWER  = "power"    # processing power — spent by SCAN / RECONSTRUCT
    MEMORY = "memory"   # memory slots    — spent by holding artefacts
    ENERGY = "energy"   # energy          — spent each turn passively


# ---------------------------------------------------------------------------
# Resource slot
# ---------------------------------------------------------------------------

@dataclass
class ResourceSlot:
    """Current and maximum value for a single resource.

    Parameters
    ----------
    current:
        Present amount available.
    maximum:
        Hard cap; ``current`` never exceeds this.
    """
    current: float
    maximum: float

    @property
    def is_depleted(self) -> bool:
        """True when current has reached zero."""
        return self.current <= 0.0

    @property
    def ratio(self) -> float:
        """Current as a fraction of maximum, in ``[0.0, 1.0]``."""
        if self.maximum <= 0:
            return 0.0
        return max(0.0, min(1.0, self.current / self.maximum))


# ---------------------------------------------------------------------------
# Resource manager
# ---------------------------------------------------------------------------

class ResourceManager:
    """Owns and mediates access to all player resources.

    Parameters
    ----------
    power:
        Starting (and maximum) processing power.
    memory:
        Starting (and maximum) memory.
    energy:
        Starting (and maximum) energy.
    energy_drain:
        Amount of energy consumed automatically each turn by ``tick()``.

    Usage
    -----
        rm = ResourceManager(power=100, memory=50, energy=80)

        # Spending (returns False if insufficient)
        ok = rm.consume(Resource.POWER, 10, source="ScanCommand")

        # Restoring
        rm.restore(Resource.ENERGY, 5, source="UpgradeSystem")

        # Each turn
        rm.tick()
    """

    def __init__(
        self,
        power:        float = 100.0,
        memory:       float = 50.0,
        energy:       float = 80.0,
        energy_drain: float = 1.0,
    ) -> None:
        self._slots: dict[Resource, ResourceSlot] = {
            Resource.POWER:  ResourceSlot(current=power,  maximum=power),
            Resource.MEMORY: ResourceSlot(current=memory, maximum=memory),
            Resource.ENERGY: ResourceSlot(current=energy, maximum=energy),
        }
        self._energy_drain = energy_drain

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def current(self, resource: Resource) -> float:
        """Return the current amount of *resource*."""
        return self._slots[resource].current

    def maximum(self, resource: Resource) -> float:
        """Return the maximum amount of *resource*."""
        return self._slots[resource].maximum

    def ratio(self, resource: Resource) -> float:
        """Return current/maximum as a float in ``[0.0, 1.0]``."""
        return self._slots[resource].ratio

    def is_depleted(self, resource: Resource) -> bool:
        """Return True if *resource* is at zero."""
        return self._slots[resource].is_depleted

    def can_afford(self, resource: Resource, amount: float) -> bool:
        """Return True if *amount* of *resource* is available.

        Parameters
        ----------
        resource:
            The resource to check.
        amount:
            The amount required.
        """
        return self._slots[resource].current >= amount

    # ------------------------------------------------------------------
    # Write access
    # ------------------------------------------------------------------

    def consume(
        self,
        resource: Resource,
        amount:   float,
        source:   str = "",
    ) -> bool:
        """Spend *amount* of *resource*.

        Parameters
        ----------
        resource:
            Which resource to spend.
        amount:
            How much to spend.  Must be non-negative.
        source:
            Debug label for the caller (e.g. ``"ScanCommand"``).

        Returns
        -------
        bool
            ``True`` if the spend succeeded; ``False`` if insufficient funds.
            On failure the resource value is unchanged.
        """
        if amount < 0:
            raise ValueError(f"consume() amount must be non-negative, got {amount}")

        slot = self._slots[resource]
        if slot.current < amount:
            log.debug(
                "%s: cannot consume %.1f %s (have %.1f)",
                source, amount, resource.name, slot.current,
            )
            return False

        slot.current -= amount
        self._post_changed(resource, -amount, source)

        if slot.is_depleted:
            event_queue.post_immediate(
                EventType.RESOURCE_DEPLETED,
                {"resource": resource.name, "source": source},
                source="ResourceManager",
            )
            log.warning("%s depleted (triggered by %s)", resource.name, source)

        return True

    def restore(
        self,
        resource: Resource,
        amount:   float,
        source:   str = "",
    ) -> None:
        """Restore *amount* of *resource*, capped at maximum.

        Parameters
        ----------
        resource:
            Which resource to restore.
        amount:
            How much to add.  Must be non-negative.
        source:
            Debug label for the caller.
        """
        if amount < 0:
            raise ValueError(f"restore() amount must be non-negative, got {amount}")

        slot         = self._slots[resource]
        actual_gain  = min(amount, slot.maximum - slot.current)
        slot.current += actual_gain

        if actual_gain > 0:
            self._post_changed(resource, actual_gain, source)

    def set_maximum(self, resource: Resource, new_max: float) -> None:
        """Update the maximum for *resource* (e.g. after an upgrade).

        Current value is clamped to the new maximum if it would exceed it.

        Parameters
        ----------
        resource:
            Which resource to upgrade.
        new_max:
            New maximum value.  Must be positive.
        """
        if new_max <= 0:
            raise ValueError(f"Maximum must be positive, got {new_max}")
        slot         = self._slots[resource]
        slot.maximum = new_max
        slot.current = min(slot.current, new_max)
        self._post_changed(resource, 0.0, source="Upgrade")

    # ------------------------------------------------------------------
    # Turn tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Apply passive energy drain for one turn.

        Called by ``Game.update()`` each turn.  If energy is depleted after
        the drain, ``RESOURCE_DEPLETED`` is posted by ``consume()``.
        """
        self.consume(Resource.ENERGY, self._energy_drain, source="PassiveDrain")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _post_changed(self, resource: Resource, delta: float, source: str) -> None:
        """Post a ``RESOURCE_CHANGED`` event.

        Parameters
        ----------
        resource:
            The resource that changed.
        delta:
            Signed change amount (negative = spent, positive = gained).
        source:
            Debug label.
        """
        slot = self._slots[resource]
        event_queue.post_immediate(
            EventType.RESOURCE_CHANGED,
            {
                "resource": resource.name,
                "delta":    delta,
                "current":  slot.current,
                "maximum":  slot.maximum,
                "ratio":    slot.ratio,
                "source":   source,
            },
            source="ResourceManager",
        )

    def __repr__(self) -> str:
        parts = ", ".join(
            f"{r.name}={s.current:.0f}/{s.maximum:.0f}"
            for r, s in self._slots.items()
        )
        return f"<ResourceManager {parts}>"