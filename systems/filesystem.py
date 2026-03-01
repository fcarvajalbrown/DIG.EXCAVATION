"""
systems/filesystem.py
=====================
Runtime state and operations for a loaded dig-site filesystem.

Responsibilities
----------------
- Own the flat node registry (id -> Node).
- Track the player's current directory.
- Expose navigation (cd, ls) and excavation (scan, carve) operations.
- Tick corruption each turn and post events when nodes change state.
- Never render anything; never import pygame.

This system is the single source of truth for the virtual filesystem.
All other systems that need filesystem data should call methods here or
subscribe to the events this module posts via ``event_queue``.

Events posted
-------------
NODE_REVEALED       When a hidden node becomes DETECTED or REVEALED.
NODE_CORRUPTED      When a node's corruption crosses a whole-number threshold.
ARTIFACT_FOUND      When a SCAN surfaces an artifact on a FILE node.
SCAN_COMPLETE       When a SCAN operation finishes (success or failure).
CARVE_COMPLETE      When a CARVE operation finishes (success or failure).
"""

from __future__ import annotations

import logging
from typing import Iterator, Optional

from systems.event_queue import EventType, event_queue
from world.node import Node, NodeType, NodeVisibility

log = logging.getLogger(__name__)

# Corruption added to every visible node per turn.
_CORRUPTION_TICK = 0.02

# Corruption threshold above which CARVE has a chance to fail.
_CARVE_FAIL_THRESHOLD = 0.8


class FilesystemError(Exception):
    """Raised for invalid filesystem operations (bad path, wrong node type)."""


class Filesystem:
    """Manages the virtual filesystem for a single dig site.

    Parameters
    ----------
    root:
        The root ``Node`` (must be a DIRECTORY).
    nodes:
        Flat dict mapping ``node_id -> Node`` for every node in the site,
        including the root.  Typically produced by ``world/site_generator.py``.

    Usage
    -----
        fs = Filesystem(root=root_node, nodes=all_nodes)

        # Navigation
        fs.change_directory("invoices")
        children = list(fs.list_directory())

        # Excavation (called by command handler each turn)
        fs.scan("readme.txt")
        fs.carve("debris_01")

        # Called by Game.update() each turn after advance_turn()
        fs.tick()
    """

    def __init__(self, root: Node, nodes: dict[str, Node]) -> None:
        if not root.is_directory:
            raise FilesystemError("Root node must be a DIRECTORY.")
        self._nodes: dict[str, Node] = nodes
        self._root_id: str = root.node_id
        self._cwd_id:  str = root.node_id

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _node(self, node_id: str) -> Node:
        """Return the node for *node_id* or raise ``FilesystemError``."""
        try:
            return self._nodes[node_id]
        except KeyError:
            raise FilesystemError(f"Node id {node_id!r} not found in filesystem.")

    def _child_by_name(self, name: str, parent: Node) -> Optional[Node]:
        """Return the first child of *parent* whose name matches *name*, or None."""
        for cid in parent.children_ids:
            child = self._nodes.get(cid)
            if child and child.name == name:
                return child
        return None

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    @property
    def cwd(self) -> Node:
        """The node representing the current working directory."""
        return self._node(self._cwd_id)

    @property
    def root(self) -> Node:
        """The filesystem root node."""
        return self._node(self._root_id)

    def change_directory(self, name: str) -> Node:
        """Move into a child directory or up with ``".."``.

        Parameters
        ----------
        name:
            Child directory name, or ``".."`` to go up one level.

        Returns
        -------
        Node
            The new current directory node.

        Raises
        ------
        FilesystemError
            If the target does not exist, is not a DIRECTORY, or is not
            visible enough to enter (must be at least DETECTED).
        """
        cwd = self.cwd

        if name == "..":
            if cwd.is_root:
                raise FilesystemError("Already at root — cannot go up.")
            target = self._node(cwd.parent_id)
        else:
            target = self._child_by_name(name, cwd)
            if target is None:
                raise FilesystemError(f"No such directory: {name!r}")
            if target.visibility is NodeVisibility.HIDDEN:
                raise FilesystemError(f"{name!r} is not visible. Run SCAN first.")
            if not target.is_directory:
                raise FilesystemError(f"{name!r} is not a directory.")

        self._cwd_id = target.node_id
        log.debug("cd -> %r", target.name)
        return target

    def list_directory(self, include_hidden: bool = False) -> Iterator[Node]:
        """Yield child nodes of the current directory.

        Parameters
        ----------
        include_hidden:
            If ``True``, yield HIDDEN nodes too (useful for debug/cheats).
            Default is ``False`` (player-facing view).
        """
        for cid in self.cwd.children_ids:
            node = self._nodes.get(cid)
            if node is None:
                continue
            if include_hidden or node.visibility is not NodeVisibility.HIDDEN:
                yield node

    def path_to_cwd(self) -> str:
        """Return a ``/``-separated path string from root to the cwd.

        Example: ``"/corp_server/invoices/q3"``
        """
        parts: list[str] = []
        node = self.cwd
        while not node.is_root:
            parts.append(node.name)
            node = self._node(node.parent_id)
        parts.append("/")
        return "/".join(reversed(parts)).replace("//", "/")

    # ------------------------------------------------------------------
    # Excavation commands
    # ------------------------------------------------------------------

    def scan(self, name: str) -> Node:
        """Run a SCAN on a child node of the current directory.

        SCAN reveals a HIDDEN node (sets it to DETECTED) or fully reveals a
        DETECTED node.  If the target is a FILE with an artifact, posts
        ``ARTIFACT_FOUND``.  Always posts ``SCAN_COMPLETE``.

        Parameters
        ----------
        name:
            Name of the child node to scan.

        Returns
        -------
        Node
            The (now more visible) target node.

        Raises
        ------
        FilesystemError
            If no child with *name* exists in the current directory.
        """
        target = self._child_by_name(name, self.cwd)
        if target is None:
            raise FilesystemError(f"SCAN: no target named {name!r} in current directory.")

        previous = target.visibility

        if target.visibility is NodeVisibility.HIDDEN:
            target.visibility = NodeVisibility.DETECTED
        elif target.visibility is NodeVisibility.DETECTED:
            target.visibility = NodeVisibility.REVEALED

        if target.visibility is not previous:
            event_queue.post_immediate(
                EventType.NODE_REVEALED,
                {"node_id": target.node_id, "name": target.name,
                 "visibility": target.visibility.name},
                source="Filesystem",
            )
            log.debug("SCAN revealed %r -> %s", target.name, target.visibility.name)

        # Surface artifact if fully revealed FILE
        if (target.visibility is NodeVisibility.REVEALED
                and target.is_file
                and target.has_artifact):
            event_queue.post_immediate(
                EventType.ARTIFACT_FOUND,
                {"node_id": target.node_id, "artifact_id": target.artifact_id,
                 "name": target.name},
                source="Filesystem",
            )

        event_queue.post_immediate(
            EventType.SCAN_COMPLETE,
            {"node_id": target.node_id, "name": target.name, "success": True},
            source="Filesystem",
        )
        return target

    def carve(self, name: str) -> Node:
        """Attempt to CARVE a DEBRIS node into a recoverable FILE.

        Carving converts a DEBRIS node to a FILE node and sets its visibility
        to REVEALED.  Fails (and posts ``CARVE_COMPLETE`` with
        ``success=False``) if the node's corruption is above
        ``_CARVE_FAIL_THRESHOLD``.

        Parameters
        ----------
        name:
            Name of the DEBRIS child node to carve.

        Returns
        -------
        Node
            The (possibly converted) node.

        Raises
        ------
        FilesystemError
            If *name* is not found, or is not DEBRIS, or is HIDDEN.
        """
        target = self._child_by_name(name, self.cwd)
        if target is None:
            raise FilesystemError(f"CARVE: no target named {name!r} in current directory.")
        if target.visibility is NodeVisibility.HIDDEN:
            raise FilesystemError(f"CARVE: {name!r} is not visible. Run SCAN first.")
        if not target.is_debris:
            raise FilesystemError(f"CARVE: {name!r} is not DEBRIS.")

        if target.corruption >= _CARVE_FAIL_THRESHOLD:
            log.debug("CARVE failed — corruption too high: %.2f", target.corruption)
            event_queue.post_immediate(
                EventType.CARVE_COMPLETE,
                {"node_id": target.node_id, "name": target.name, "success": False,
                 "reason": "corruption_too_high"},
                source="Filesystem",
            )
            return target

        # Convert to FILE
        target.node_type  = NodeType.FILE
        target.visibility = NodeVisibility.REVEALED
        log.debug("CARVE succeeded: %r is now a FILE", target.name)

        event_queue.post_immediate(
            EventType.CARVE_COMPLETE,
            {"node_id": target.node_id, "name": target.name, "success": True},
            source="Filesystem",
        )
        return target

    # ------------------------------------------------------------------
    # Turn tick
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Advance corruption on all visible nodes by one turn.

        Called by ``Game.update()`` after ``event_queue.advance_turn()``.
        Hidden nodes do not decay — they are inert until discovered.
        Posts ``NODE_CORRUPTED`` when a node crosses a 25 % corruption
        boundary (0.25, 0.50, 0.75, 1.0) so the UI can react.
        """
        thresholds = (0.25, 0.50, 0.75, 1.0)

        for node in self._nodes.values():
            if node.visibility is NodeVisibility.HIDDEN:
                continue

            before = node.corruption
            node.apply_corruption(_CORRUPTION_TICK)
            after  = node.corruption

            # Check if we crossed any threshold this tick
            for t in thresholds:
                if before < t <= after:
                    event_queue.post_immediate(
                        EventType.NODE_CORRUPTED,
                        {"node_id": node.node_id, "name": node.name,
                         "corruption": after, "threshold": t},
                        source="Filesystem",
                    )
                    log.debug(
                        "Node %r crossed corruption threshold %.2f", node.name, t
                    )
                    break   # one event per tick per node is enough

    # ------------------------------------------------------------------
    # Bulk access
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[Node]:
        """Return the node for *node_id*, or ``None`` if not found."""
        return self._nodes.get(node_id)

    def all_nodes(self) -> Iterator[Node]:
        """Yield every node in the filesystem (order not guaranteed)."""
        yield from self._nodes.values()

    def __repr__(self) -> str:
        return (
            f"<Filesystem nodes={len(self._nodes)} "
            f"cwd={self.cwd.name!r}>"
        )