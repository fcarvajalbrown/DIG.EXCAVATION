"""
systems/artifact.py
===================
Tracks discovered, held, and sold artifacts for a dig session.

Responsibilities
----------------
- Own the artifact registry (id -> Artifact).
- Record when an artifact is found, collected, or sold.
- Calculate sell value based on condition (inverse of corruption at time
  of recovery).
- Post relevant events via event_queue.
- Never render anything; never import pygame.

Artifact lifecycle
------------------
    UNDISCOVERED  ->  FOUND  ->  COLLECTED  ->  SOLD
                                     |
                                  (held in memory slot)

An artifact moves from FOUND to COLLECTED when the player runs RECONSTRUCT
on it.  COLLECTED artifacts occupy a memory slot in ResourceManager.
Selling clears the memory slot and awards in-game currency.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from systems.event_queue import EventType, event_queue
from systems.resource_manager import Resource, ResourceManager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ArtifactState(Enum):
    """Lifecycle state of an artifact."""
    UNDISCOVERED = "undiscovered"   # exists in filesystem, not yet found
    FOUND        = "found"          # SCAN surfaced it; not yet collected
    COLLECTED    = "collected"      # RECONSTRUCT succeeded; in memory
    SOLD         = "sold"           # sold to the Virtual Artifacts Council


class ArtifactRarity(Enum):
    """Rarity tier — affects base sell value."""
    COMMON    = "common"
    UNCOMMON  = "uncommon"
    RARE      = "rare"
    LEGENDARY = "legendary"

    @property
    def value_multiplier(self) -> float:
        """Sell value multiplier for this rarity tier."""
        return {
            ArtifactRarity.COMMON:    1.0,
            ArtifactRarity.UNCOMMON:  2.5,
            ArtifactRarity.RARE:      6.0,
            ArtifactRarity.LEGENDARY: 15.0,
        }[self]


# ---------------------------------------------------------------------------
# Artifact dataclass
# ---------------------------------------------------------------------------

@dataclass
class Artifact:
    """A recoverable digital relic found in the filesystem.

    Parameters
    ----------
    artifact_id:
        Unique identifier (matches the ``artifact_id`` stored on the Node).
    name:
        Display name shown to the player.
    description:
        Lore text revealed after collection.
    node_id:
        The filesystem node this artifact was found on.
    rarity:
        Rarity tier affecting sell value.
    state:
        Current lifecycle state.
    condition:
        Float in ``[0.0, 1.0]`` representing how intact the artifact is.
        Set at collection time as ``1.0 - node.corruption``.
    sell_value:
        Computed at collection time; 0 until then.
    metadata:
        Arbitrary extra data (theme, era, content type, etc.).
    """

    artifact_id:  str
    name:         str
    description:  str                = ""
    node_id:      str                = ""
    rarity:       ArtifactRarity     = ArtifactRarity.COMMON
    state:        ArtifactState      = ArtifactState.UNDISCOVERED
    condition:    float              = 1.0
    sell_value:   float              = 0.0
    metadata:     dict               = field(default_factory=dict)

    # Memory cost in ResourceManager.MEMORY slots
    MEMORY_COST: float = field(default=10.0, init=False, repr=False)

    def __repr__(self) -> str:
        return (
            f"<Artifact {self.artifact_id!r} {self.rarity.name} "
            f"state={self.state.name} condition={self.condition:.2f}>"
        )


# ---------------------------------------------------------------------------
# Artifact system
# ---------------------------------------------------------------------------

class ArtifactSystem:
    """Manages all artifacts across a dig session.

    Parameters
    ----------
    resource_manager:
        Used to allocate / free memory slots when artifacts are
        collected or sold.

    Usage
    -----
        arts = ArtifactSystem(resource_manager=rm)

        # Called by site_generator or a loader to pre-register artifacts
        arts.register(artifact)

        # Called by filesystem when ARTIFACT_FOUND event fires
        arts.mark_found(artifact_id)

        # Called by RECONSTRUCT command
        arts.collect(artifact_id, node_corruption=0.3)

        # Called by SELL command
        arts.sell(artifact_id)
    """

    # Base sell value before rarity and condition multipliers
    _BASE_VALUE: float = 50.0

    def __init__(self, resource_manager: ResourceManager) -> None:
        self._rm:        ResourceManager         = resource_manager
        self._artifacts: dict[str, Artifact]     = {}
        self._currency:  float                   = 0.0

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, artifact: Artifact) -> None:
        """Add an artifact to the registry.

        Typically called by ``world/site_generator.py`` during site setup.

        Parameters
        ----------
        artifact:
            The ``Artifact`` to register.  Must have a unique ``artifact_id``.
        """
        if artifact.artifact_id in self._artifacts:
            log.warning("Artifact %r already registered — skipping.", artifact.artifact_id)
            return
        self._artifacts[artifact.artifact_id] = artifact
        log.debug("Registered artifact %r (%s)", artifact.artifact_id, artifact.rarity.name)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def mark_found(self, artifact_id: str) -> Optional[Artifact]:
        """Transition an artifact from UNDISCOVERED to FOUND.

        Called when the filesystem posts ``ARTIFACT_FOUND``.

        Parameters
        ----------
        artifact_id:
            The artifact to mark as found.

        Returns
        -------
        Artifact or None
            The artifact if found in the registry, else None.
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            log.warning("mark_found: artifact %r not in registry.", artifact_id)
            return None
        if artifact.state is not ArtifactState.UNDISCOVERED:
            return artifact   # already progressed, no-op

        artifact.state = ArtifactState.FOUND
        log.debug("Artifact %r marked FOUND", artifact_id)
        return artifact

    def collect(self, artifact_id: str, node_corruption: float = 0.0) -> bool:
        """Collect a FOUND artifact (RECONSTRUCT succeeded).

        Allocates a memory slot and computes sell value based on condition.
        Posts ``RECONSTRUCT_END`` with success status.

        Parameters
        ----------
        artifact_id:
            The artifact to collect.
        node_corruption:
            The corruption level of the node at time of collection.
            Used to compute artifact condition.

        Returns
        -------
        bool
            ``True`` if collection succeeded; ``False`` if the artifact is
            in the wrong state or there is insufficient memory.
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            log.warning("collect: artifact %r not found.", artifact_id)
            return False

        if artifact.state is not ArtifactState.FOUND:
            log.debug(
                "collect: artifact %r is %s, expected FOUND.",
                artifact_id, artifact.state.name,
            )
            return False

        # Try to allocate memory
        if not self._rm.consume(Resource.MEMORY, artifact.MEMORY_COST, source="ArtifactSystem"):
            event_queue.post_immediate(
                EventType.RECONSTRUCT_END,
                {"artifact_id": artifact_id, "success": False, "reason": "insufficient_memory"},
                source="ArtifactSystem",
            )
            log.debug("collect: insufficient memory for %r", artifact_id)
            return False

        # Compute condition and sell value
        artifact.condition  = max(0.01, 1.0 - node_corruption)
        artifact.sell_value = self._compute_value(artifact)
        artifact.state      = ArtifactState.COLLECTED

        event_queue.post_immediate(
            EventType.RECONSTRUCT_END,
            {
                "artifact_id": artifact_id,
                "success":     True,
                "condition":   artifact.condition,
                "sell_value":  artifact.sell_value,
                "name":        artifact.name,
            },
            source="ArtifactSystem",
        )
        log.info(
            "Artifact %r collected — condition=%.2f value=%.0f",
            artifact_id, artifact.condition, artifact.sell_value,
        )
        return True

    def sell(self, artifact_id: str) -> float:
        """Sell a COLLECTED artifact and receive currency.

        Frees the memory slot and adds the sell value to the player's
        currency balance.

        Parameters
        ----------
        artifact_id:
            The artifact to sell.

        Returns
        -------
        float
            Currency earned.  Returns 0.0 if the artifact cannot be sold.
        """
        artifact = self._artifacts.get(artifact_id)
        if artifact is None or artifact.state is not ArtifactState.COLLECTED:
            log.debug("sell: artifact %r not collectable.", artifact_id)
            return 0.0

        earned           = artifact.sell_value
        artifact.state   = ArtifactState.SOLD
        artifact.sell_value = 0.0
        self._currency  += earned

        # Free memory slot
        self._rm.restore(Resource.MEMORY, artifact.MEMORY_COST, source="ArtifactSystem")

        event_queue.post_immediate(
            EventType.LOG_ENTRY_ADDED,
            {
                "artifact_id": artifact_id,
                "name":        artifact.name,
                "earned":      earned,
                "description": artifact.description,
            },
            source="ArtifactSystem",
        )
        log.info("Artifact %r sold for %.0f currency", artifact_id, earned)
        return earned

    # ------------------------------------------------------------------
    # Value calculation
    # ------------------------------------------------------------------

    def _compute_value(self, artifact: Artifact) -> float:
        """Compute the sell value for *artifact* based on rarity and condition.

        Parameters
        ----------
        artifact:
            The artifact to value.  Must have ``condition`` set.

        Returns
        -------
        float
            Computed sell value, rounded to one decimal place.
        """
        return round(
            self._BASE_VALUE
            * artifact.rarity.value_multiplier
            * artifact.condition,
            1,
        )

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    @property
    def currency(self) -> float:
        """Total currency earned from artifact sales this session."""
        return self._currency

    def get(self, artifact_id: str) -> Optional[Artifact]:
        """Return the artifact for *artifact_id*, or None."""
        return self._artifacts.get(artifact_id)

    def collected(self) -> list[Artifact]:
        """Return all currently held (COLLECTED) artifacts."""
        return [a for a in self._artifacts.values() if a.state is ArtifactState.COLLECTED]

    def found(self) -> list[Artifact]:
        """Return all FOUND (not yet collected) artifacts."""
        return [a for a in self._artifacts.values() if a.state is ArtifactState.FOUND]

    def all_artifacts(self) -> list[Artifact]:
        """Return every artifact regardless of state."""
        return list(self._artifacts.values())

    def __repr__(self) -> str:
        collected = len(self.collected())
        return (
            f"<ArtifactSystem artifacts={len(self._artifacts)} "
            f"collected={collected} currency={self._currency:.0f}>"
        )