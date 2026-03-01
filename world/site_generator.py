"""
world/site_generator.py
=======================
Procedural generation of dig-site filesystems.

Responsibilities
----------------
- Build a tree of ``Node`` objects that represents a unique dig site.
- Return a ready-to-use ``Filesystem`` instance.
- Never post events; never import pygame.

Generation approach
-------------------
Each site is seeded so results are reproducible (useful for save/load).
The generator works in three passes:

1. **Skeleton** — build the directory tree using a recursive branching
   algorithm driven by a ``SiteProfile``.
2. **Population** — scatter FILE and DEBRIS nodes into leaf directories
   according to the profile's density parameters.
3. **Seeding** — place artifacts on a subset of FILE nodes and attach
   lore metadata drawn from the profile's theme.

``SiteProfile`` is a plain dataclass so profiles can be defined in YAML
(``data/sites/``) and loaded externally; the generator accepts one as input.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field

from systems.filesystem import Filesystem
from world.node import Node, NodeType, NodeVisibility

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Site profile
# ---------------------------------------------------------------------------

@dataclass
class SiteProfile:
    """Parameters that shape a procedurally generated dig site.

    All counts and probabilities are treated as guidelines; the generator
    clamps values to sane ranges internally.

    Parameters
    ----------
    name:
        Human-readable site name shown to the player (e.g. ``"Abandoned Corp
        Server — Tokyo"``).
    seed:
        RNG seed for reproducibility.  Two runs with the same seed and
        profile produce identical sites.
    max_depth:
        Maximum directory nesting depth (root = 0).
    branch_factor:
        Mean number of subdirectories per directory.  Actual count is
        sampled from a Poisson-like distribution around this value.
    files_per_dir:
        Mean number of FILE nodes placed in each leaf directory.
    debris_ratio:
        Fraction of leaf nodes that become DEBRIS instead of FILE.
        Clamped to ``[0.0, 1.0]``.
    artifact_density:
        Fraction of FILE nodes that carry an artifact.
        Clamped to ``[0.0, 1.0]``.
    base_corruption:
        Starting corruption applied to every node at generation time.
        Represents how decayed the site already is when the player arrives.
    theme:
        Flavour tag used to select lore metadata (e.g. ``"corporate"``,
        ``"personal"``, ``"research"``).
    dir_names:
        Pool of directory names to draw from.  Sampled without replacement
        per directory; reused across depths when exhausted.
    file_names:
        Pool of file names for FILE nodes.
    debris_names:
        Pool of names for DEBRIS nodes.
    """

    name:             str
    seed:             int                = 0
    max_depth:        int                = 3
    branch_factor:    float              = 2.5
    files_per_dir:    float              = 3.0
    debris_ratio:     float              = 0.3
    artifact_density: float              = 0.2
    base_corruption:  float              = 0.1
    theme:            str                = "corporate"
    dir_names:        list[str]          = field(default_factory=lambda: [
        "invoices", "archive", "logs", "backup", "system",
        "personal", "reports", "cache", "temp", "projects",
        "assets", "config", "network", "users", "research",
    ])
    file_names:       list[str]          = field(default_factory=lambda: [
        "readme.txt", "memo.doc", "export.csv", "notes.txt",
        "report.pdf", "manifest.log", "index.dat", "summary.txt",
        "contacts.db", "schedule.txt", "budget.xls", "draft.doc",
    ])
    debris_names:     list[str]          = field(default_factory=lambda: [
        "fragment_A", "corrupt_B", "debris_01", "shard_02",
        "remnant_C", "chunk_03", "erased_D", "lost_04",
    ])


# ---------------------------------------------------------------------------
# Default profiles
# ---------------------------------------------------------------------------

PROFILE_CORPORATE = SiteProfile(
    name          = "Abandoned Corporate Server",
    theme         = "corporate",
    max_depth     = 2,
    branch_factor = 2.0,
    files_per_dir = 3.0,
    debris_ratio  = 0.25,
    artifact_density = 0.15,
    base_corruption  = 0.08,
)

PROFILE_PERSONAL = SiteProfile(
    name          = "Personal Databank",
    theme         = "personal",
    max_depth     = 2,
    branch_factor = 1.5,
    files_per_dir = 5.0,
    debris_ratio  = 0.4,
    artifact_density = 0.3,
    base_corruption  = 0.2,
)

PROFILE_RESEARCH = SiteProfile(
    name          = "Research Terminal",
    theme         = "research",
    max_depth     = 3,
    branch_factor = 2.0,
    files_per_dir = 6.0,
    debris_ratio  = 0.2,
    artifact_density = 0.25,
    base_corruption  = 0.05,
)


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

class SiteGenerator:
    """Builds a ``Filesystem`` from a ``SiteProfile``.

    Usage
    -----
        gen = SiteGenerator(profile=PROFILE_CORPORATE, seed=42)
        fs  = gen.generate()

    The generator is stateless between calls to ``generate()``; you can
    call it multiple times with different seeds.
    """

    def __init__(self, profile: SiteProfile, seed: int | None = None) -> None:
        """
        Parameters
        ----------
        profile:
            The ``SiteProfile`` that shapes the generated site.
        seed:
            Override seed.  If ``None``, uses ``profile.seed``.
        """
        self._profile = profile
        self._seed    = seed if seed is not None else profile.seed

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def generate(self) -> Filesystem:
        """Run all three generation passes and return a ``Filesystem``.

        Returns
        -------
        Filesystem
            A fully populated, ready-to-play filesystem instance.
        """
        rng     = random.Random(self._seed)
        nodes:  dict[str, Node] = {}

        log.info("Generating site %r (seed=%d)", self._profile.name, self._seed)

        # Pass 1 — skeleton
        root = self._make_node("root", NodeType.DIRECTORY, parent_id=None,
                               profile=self._profile, rng=rng)
        root.visibility = NodeVisibility.REVEALED   # root is always visible
        nodes[root.node_id] = root

        self._build_tree(root, depth=0, nodes=nodes, rng=rng)

        # Pass 2 — population (files + debris in leaf dirs)
        self._populate(nodes, rng=rng)

        # Pass 3 — seed artifacts
        self._seed_artifacts(nodes, rng=rng)

        log.info("Site generated: %d nodes total", len(nodes))
        return Filesystem(root=root, nodes=nodes)

    # ------------------------------------------------------------------
    # Pass 1 — skeleton
    # ------------------------------------------------------------------

    def _build_tree(
        self,
        parent:  Node,
        depth:   int,
        nodes:   dict[str, Node],
        rng:     random.Random,
    ) -> None:
        """Recursively build child directories under *parent*.

        Parameters
        ----------
        parent:
            The directory node to populate with subdirectories.
        depth:
            Current depth (root = 0).
        nodes:
            Shared node registry — new nodes are inserted here.
        rng:
            Seeded RNG instance.
        """
        if depth >= self._profile.max_depth:
            return

        # Poisson-approximated branch count
        n_branches = max(0, round(rng.gauss(self._profile.branch_factor, 0.8)))
        name_pool  = list(self._profile.dir_names)
        rng.shuffle(name_pool)

        for i in range(n_branches):
            name = name_pool[i % len(name_pool)]
            # Deduplicate sibling names by appending a counter
            if any(
                self._lookup_child_name(name, parent, nodes)
            ):
                name = f"{name}_{i}"

            child = self._make_node(
                name, NodeType.DIRECTORY,
                parent_id=parent.node_id,
                profile=self._profile,
                rng=rng,
            )
            nodes[child.node_id] = child
            parent.add_child(child.node_id)

            self._build_tree(child, depth + 1, nodes, rng)

    # ------------------------------------------------------------------
    # Pass 2 — population
    # ------------------------------------------------------------------

    def _populate(self, nodes: dict[str, Node], rng: random.Random) -> None:
        """Add FILE and DEBRIS nodes to leaf directories.

        A leaf directory is one with no child directories (it may have
        files added in this pass).

        Parameters
        ----------
        nodes:
            Shared node registry.
        rng:
            Seeded RNG instance.
        """
        profile      = self._profile
        debris_ratio = max(0.0, min(1.0, profile.debris_ratio))

        leaf_dirs = [
            n for n in nodes.values()
            if n.is_directory and not any(
                nodes[cid].is_directory
                for cid in n.children_ids
                if cid in nodes
            )
        ]

        for leaf in leaf_dirs:
            n_files = max(0, round(rng.gauss(profile.files_per_dir, 1.0)))
            file_pool   = list(profile.file_names)
            debris_pool = list(profile.debris_names)
            rng.shuffle(file_pool)
            rng.shuffle(debris_pool)

            for i in range(n_files):
                is_debris = rng.random() < debris_ratio
                if is_debris:
                    name      = debris_pool[i % len(debris_pool)]
                    node_type = NodeType.DEBRIS
                else:
                    name      = file_pool[i % len(file_pool)]
                    node_type = NodeType.FILE

                child = self._make_node(
                    name, node_type,
                    parent_id=leaf.node_id,
                    profile=profile,
                    rng=rng,
                )
                nodes[child.node_id] = child
                leaf.add_child(child.node_id)

    # ------------------------------------------------------------------
    # Pass 3 — artifact seeding
    # ------------------------------------------------------------------

    def _seed_artifacts(self, nodes: dict[str, Node], rng: random.Random) -> None:
        """Assign artifact IDs to a fraction of FILE nodes.

        Parameters
        ----------
        nodes:
            Shared node registry.
        rng:
            Seeded RNG instance.
        """
        density   = max(0.0, min(1.0, self._profile.artifact_density))
        file_nodes = [n for n in nodes.values() if n.is_file]

        for i, node in enumerate(file_nodes):
            if rng.random() < density:
                node.artifact_id = f"arc_{self._profile.theme}_{i:04d}"
                node.metadata["artifact"] = True
                log.debug("Artifact %r seeded on %r", node.artifact_id, node.name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_node(
        self,
        name:      str,
        node_type: NodeType,
        parent_id: str | None,
        profile:   SiteProfile,
        rng:       random.Random,
    ) -> Node:
        """Construct a ``Node`` with base corruption applied.

        Parameters
        ----------
        name:
            Display name for the node.
        node_type:
            ``DIRECTORY``, ``FILE``, or ``DEBRIS``.
        parent_id:
            Parent node id, or ``None`` for root.
        profile:
            Site profile (used for base corruption and metadata).
        rng:
            Seeded RNG for slight corruption variation.

        Returns
        -------
        Node
            A new node with randomised base corruption.
        """
        # Slight variance around base_corruption so not everything decays uniformly
        corruption = max(0.0, min(
            0.99,
            profile.base_corruption + rng.uniform(-0.03, 0.03),
        ))
        node = Node(
            name       = name,
            node_type  = node_type,
            parent_id  = parent_id,
            corruption = corruption,
            metadata   = {"theme": profile.theme, "site": profile.name},
        )
        if node_type is NodeType.DIRECTORY:
            node.visibility = NodeVisibility.DETECTED
        return node

    @staticmethod
    def _lookup_child_name(
        name:   str,
        parent: Node,
        nodes:  dict[str, Node],
    ) -> list[bool]:
        """Return a one-element list ``[True]`` if *name* already exists as a
        child of *parent*, else ``[]`` (falsy).  Used in a truthiness check."""
        return [
            True for cid in parent.children_ids
            if nodes.get(cid) and nodes[cid].name == name
        ]