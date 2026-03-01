"""
world/node.py
=============
Core data structures for the DIG.EXCAVATION virtual filesystem.

A dig site is a tree of ``Node`` objects.  This module defines only the
data — no game logic, no rendering, no event posting.  Systems that operate
on nodes (``systems/filesystem.py``, ``world/site_generator.py``) import
from here.

Node types
----------
DIRECTORY   A container that holds child nodes.
FILE        A leaf node representing a data file.  May hold an artifact.
DEBRIS      A corrupted block.  Can be carved into a FILE or cleared.

Corruption
----------
Each node carries a ``corruption`` float in [0.0, 1.0].  At 1.0 the node
is considered fully decayed.  Systems are responsible for incrementing this
value and deciding consequences; the dataclass itself enforces no rules.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class NodeType(enum.Enum):
    """The three kinds of node that can exist in a dig-site filesystem."""
    DIRECTORY = "directory"
    FILE      = "file"
    DEBRIS    = "debris"


class NodeVisibility(enum.Enum):
    """How much the player knows about this node."""
    HIDDEN    = "hidden"     # not yet detected
    DETECTED  = "detected"   # SCAN revealed it exists but not its contents
    REVEALED  = "revealed"   # fully visible


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """A single node in the virtual filesystem tree.

    Parameters
    ----------
    name:
        Display name (e.g. ``"invoices"``, ``"readme.txt"``).
    node_type:
        ``DIRECTORY``, ``FILE``, or ``DEBRIS``.
    node_id:
        Unique identifier.  Auto-generated if not supplied.
    parent_id:
        ``node_id`` of the parent directory, or ``None`` for the root.
    children_ids:
        Ordered list of child ``node_id`` values (directories only).
    corruption:
        Decay level in ``[0.0, 1.0]``.  ``1.0`` means fully corrupted.
    visibility:
        How much of this node the player can currently see.
    artifact_id:
        If set, this FILE contains a recoverable artifact with this id.
    metadata:
        Arbitrary key-value store for site-generator hints, lore snippets,
        file size, timestamps, etc.  Not interpreted by this module.
    """

    name:         str
    node_type:    NodeType
    node_id:      str               = field(default_factory=lambda: str(uuid.uuid4()))
    parent_id:    Optional[str]     = None
    children_ids: list[str]         = field(default_factory=list)
    corruption:   float             = 0.0
    visibility:   NodeVisibility    = NodeVisibility.HIDDEN
    artifact_id:  Optional[str]     = None
    metadata:     dict              = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_root(self) -> bool:
        """True if this node has no parent (i.e. it is the site root)."""
        return self.parent_id is None

    @property
    def is_directory(self) -> bool:
        return self.node_type is NodeType.DIRECTORY

    @property
    def is_file(self) -> bool:
        return self.node_type is NodeType.FILE

    @property
    def is_debris(self) -> bool:
        return self.node_type is NodeType.DEBRIS

    @property
    def is_fully_corrupted(self) -> bool:
        """True when corruption has reached 1.0."""
        return self.corruption >= 1.0

    @property
    def has_artifact(self) -> bool:
        """True if this file node contains an unrecovered artifact."""
        return self.artifact_id is not None

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_child(self, child_id: str) -> None:
        """Append *child_id* to this directory's child list.

        Parameters
        ----------
        child_id:
            The ``node_id`` of the child node to register.

        Raises
        ------
        ValueError
            If this node is not a DIRECTORY.
        """
        if not self.is_directory:
            raise ValueError(f"Cannot add child to non-directory node {self.name!r}")
        if child_id not in self.children_ids:
            self.children_ids.append(child_id)

    def remove_child(self, child_id: str) -> None:
        """Remove *child_id* from this directory's child list.

        Safe to call even if *child_id* is not present.

        Parameters
        ----------
        child_id:
            The ``node_id`` of the child to remove.
        """
        try:
            self.children_ids.remove(child_id)
        except ValueError:
            pass

    def apply_corruption(self, delta: float) -> None:
        """Increase corruption by *delta*, clamped to ``[0.0, 1.0]``.

        Parameters
        ----------
        delta:
            Amount to add.  Negative values reduce corruption (repair).
        """
        self.corruption = max(0.0, min(1.0, self.corruption + delta))

    def __repr__(self) -> str:
        return (
            f"<Node {self.node_type.name} name={self.name!r} "
            f"corruption={self.corruption:.2f} vis={self.visibility.name}>"
        )