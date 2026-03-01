"""
systems/command_handler.py
==========================
Parses and dispatches player CLI commands to the appropriate systems.

Responsibilities
----------------
- Accept a raw input string from the UI.
- Tokenise and validate it against the known command set.
- Call the correct system method (filesystem, artifact, resource_manager).
- Return a ``CommandResult`` the UI can display without knowing game logic.
- Post ``COMMAND_ENTERED`` so other systems can react (e.g. daemon noise).
- Never render anything; never import pygame.

Supported commands
------------------
    SCAN   <target>          Reveal a node in the current directory.
    CARVE  <target>          Convert a DEBRIS node to a FILE.
    RECON  <target>          Reconstruct (collect) a FOUND artifact.
    SELL   <artifact_id>     Sell a COLLECTED artifact.
    LS                       List current directory contents.
    CD     <target>          Change directory.
    PWD                      Print current path.
    STATUS                   Show resource levels.
    HELP                     List available commands.
    QUIT                     Request clean shutdown.

Resource costs
--------------
    SCAN   costs POWER.
    CARVE  costs POWER + ENERGY.
    RECON  costs POWER + MEMORY (memory allocated inside ArtifactSystem).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from systems.artifact import ArtifactSystem
from systems.event_queue import EventType, event_queue
from systems.filesystem import Filesystem, FilesystemError
from systems.resource_manager import Resource, ResourceManager
from world.node import NodeType, NodeVisibility

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command result
# ---------------------------------------------------------------------------

@dataclass
class CommandResult:
    """Return value from ``CommandHandler.execute()``.

    The UI reads this to decide what to print — it never calls game systems
    directly.

    Parameters
    ----------
    success:
        Whether the command completed without error.
    lines:
        List of text lines to display in the terminal, in order.
    command:
        The normalised command verb that was executed.
    error:
        Human-readable error message if ``success`` is False.
    """

    success:  bool
    lines:    list[str]       = field(default_factory=list)
    command:  str             = ""
    error:    str             = ""

    def add(self, line: str) -> None:
        """Append *line* to the output lines.

        Parameters
        ----------
        line:
            Text to append.
        """
        self.lines.append(line)

    @classmethod
    def ok(cls, command: str, *lines: str) -> "CommandResult":
        """Convenience constructor for a successful result.

        Parameters
        ----------
        command:
            The command verb.
        lines:
            Output lines to display.
        """
        return cls(success=True, lines=list(lines), command=command)

    @classmethod
    def fail(cls, command: str, error: str) -> "CommandResult":
        """Convenience constructor for a failed result.

        Parameters
        ----------
        command:
            The command verb.
        error:
            Human-readable error message.
        """
        return cls(success=False, error=error, command=command,
                   lines=[f"ERROR: {error}"])


# ---------------------------------------------------------------------------
# Resource costs table
# ---------------------------------------------------------------------------

# Maps command verb -> list of (Resource, amount) tuples
_COSTS: dict[str, list[tuple[Resource, float]]] = {
    "SCAN":  [(Resource.POWER, 5.0)],
    "CARVE": [(Resource.POWER, 8.0), (Resource.ENERGY, 4.0)],
    "RECON": [(Resource.POWER, 12.0)],
}


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------

class CommandHandler:
    """Routes player input to game systems and returns display-ready results.

    Parameters
    ----------
    filesystem:
        The active dig-site filesystem.
    resource_manager:
        Player resource tracker.
    artifact_system:
        Artifact registry and lifecycle manager.

    Usage
    -----
        handler = CommandHandler(fs, rm, arts)
        result  = handler.execute("SCAN readme.txt")
        for line in result.lines:
            terminal.print(line)
    """

    def __init__(
        self,
        filesystem:       Filesystem,
        resource_manager: ResourceManager,
        artifact_system:  ArtifactSystem,
    ) -> None:
        self._fs   = filesystem
        self._rm   = resource_manager
        self._arts = artifact_system

        # Last node where an action was performed (for daemon noise)
        self._last_action_node_id: Optional[str] = None

        # Dispatch table: verb -> handler method
        self._dispatch = {
            "SCAN":   self._cmd_scan,
            "CARVE":  self._cmd_carve,
            "RECON":  self._cmd_recon,
            "SELL":   self._cmd_sell,
            "LS":     self._cmd_ls,
            "CD":     self._cmd_cd,
            "PWD":    self._cmd_pwd,
            "STATUS": self._cmd_status,
            "HELP":   self._cmd_help,
            "QUIT":   self._cmd_quit,
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, raw_input: str) -> CommandResult:
        """Parse *raw_input* and dispatch to the matching command handler.

        Parameters
        ----------
        raw_input:
            The raw string the player typed (e.g. ``"scan readme.txt"``).

        Returns
        -------
        CommandResult
            Display-ready result for the UI to render.
        """
        tokens = raw_input.strip().split()
        if not tokens:
            return CommandResult.fail("", "No command entered.")

        verb = tokens[0].upper()
        args = tokens[1:]

        event_queue.post_immediate(
            EventType.COMMAND_ENTERED,
            {"verb": verb, "args": args, "raw": raw_input},
            source="CommandHandler",
        )

        handler = self._dispatch.get(verb)
        if handler is None:
            return CommandResult.fail(verb, f"Unknown command: {verb!r}. Type HELP for a list.")

        # Check and spend resources before executing
        cost_result = self._check_and_spend(verb)
        if cost_result is not None:
            return cost_result

        try:
            result = handler(args)
        except FilesystemError as exc:
            result = CommandResult.fail(verb, str(exc))
        except Exception as exc:
            log.exception("Unexpected error executing command %r", verb)
            result = CommandResult.fail(verb, f"Internal error: {exc}")

        # Record action location for daemon noise detection
        self._last_action_node_id = self._fs.cwd.node_id
        return result

    @property
    def last_action_node_id(self) -> Optional[str]:
        """Node id where the player last performed an action.

        Read by ``DaemonSystem.tick()`` each turn as the noise source.
        """
        return self._last_action_node_id

    # ------------------------------------------------------------------
    # Resource gating
    # ------------------------------------------------------------------

    def _check_and_spend(self, verb: str) -> Optional[CommandResult]:
        """Spend resources required by *verb*.

        Parameters
        ----------
        verb:
            Normalised command verb.

        Returns
        -------
        CommandResult or None
            A failure result if resources are insufficient, else None
            (meaning the caller should proceed).
        """
        costs = _COSTS.get(verb, [])
        for resource, amount in costs:
            if not self._rm.can_afford(resource, amount):
                return CommandResult.fail(
                    verb,
                    f"Insufficient {resource.name} "
                    f"(need {amount:.0f}, have {self._rm.current(resource):.0f}).",
                )
        # All affordable — spend them
        for resource, amount in costs:
            self._rm.consume(resource, amount, source=verb)
        return None

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _cmd_scan(self, args: list[str]) -> CommandResult:
        """Handle SCAN <target> or SCAN * to scan everything in the current directory.

        Parameters
        ----------
        args:
            Command arguments; first element is the target name or *.
        """
        if not args:
            return CommandResult.fail("SCAN", "Usage: SCAN <target> | SCAN *")

        target_name = args[0]

        # Wildcard — scan every node in the current directory
        if target_name == "*":
            children = list(self._fs.list_directory(include_hidden=True))
            if not children:
                return CommandResult.fail("SCAN", "Nothing to scan here.")
            lines = ["SCANNING ALL..."]
            for child in children:
                try:
                    node = self._fs.scan(child.name)
                    tag  = "[ART]" if node.has_artifact else ""
                    lines.append(f"  {node.node_type.name[:3]}  {node.name:<20} {node.visibility.name}  {node.corruption:.0%}  {tag}")
                except Exception:
                    pass
            return CommandResult.ok("SCAN", *lines)

        # Single target
        node  = self._fs.scan(target_name)
        lines = [f"SCANNING {target_name}..."]
        lines.append(f"  Type:       {node.node_type.name}")
        lines.append(f"  Visibility: {node.visibility.name}")
        lines.append(f"  Corruption: {node.corruption:.0%}")

        if node.has_artifact:
            lines.append(f"  [!] ARTIFACT DETECTED — run RECON to reconstruct.")

        return CommandResult.ok("SCAN", *lines)

    def _cmd_carve(self, args: list[str]) -> CommandResult:
        """Handle CARVE <target>.

        Parameters
        ----------
        args:
            Command arguments; first element is the target DEBRIS name.
        """
        if not args:
            return CommandResult.fail("CARVE", "Usage: CARVE <target>")

        target_name = args[0]
        node        = self._fs.carve(target_name)

        if node.is_file:
            return CommandResult.ok(
                "CARVE",
                f"CARVING {target_name}...",
                f"  Success — debris converted to FILE.",
                f"  Corruption: {node.corruption:.0%}",
            )
        else:
            return CommandResult.fail(
                "CARVE",
                f"Carve failed — {target_name} corruption too high ({node.corruption:.0%}).",
            )

    def _cmd_recon(self, args: list[str]) -> CommandResult:
        """Handle RECON <target> — reconstruct (collect) a found artifact.

        Parameters
        ----------
        args:
            Command arguments; first element is the node name containing
            the artifact.
        """
        if not args:
            return CommandResult.fail("RECON", "Usage: RECON <target>")

        target_name = args[0]

        # Resolve node
        node = next(
            (n for n in self._fs.list_directory()
             if n.name == target_name and n.visibility is NodeVisibility.REVEALED),
            None,
        )
        if node is None:
            return CommandResult.fail("RECON", f"No visible node named {target_name!r}.")
        if not node.has_artifact:
            return CommandResult.fail("RECON", f"{target_name!r} contains no artifact.")

        artifact_id = node.artifact_id
        self._arts.mark_found(artifact_id)
        success = self._arts.collect(artifact_id, node_corruption=node.corruption)

        if success:
            artifact = self._arts.get(artifact_id)
            return CommandResult.ok(
                "RECON",
                f"RECONSTRUCTING {target_name}...",
                f"  Artifact collected: {artifact.name}",
                f"  Condition:  {artifact.condition:.0%}",
                f"  Est. value: {artifact.sell_value:.0f} credits",
            )
        else:
            return CommandResult.fail(
                "RECON",
                "Reconstruction failed — insufficient memory. SELL an artifact first.",
            )

    def _cmd_sell(self, args: list[str]) -> CommandResult:
        """Handle SELL <artifact_id>.

        Parameters
        ----------
        args:
            Command arguments; first element is the artifact id.
        """
        if not args:
            # Show collected artifacts as a hint
            collected = self._arts.collected()
            if not collected:
                return CommandResult.fail("SELL", "No artifacts to sell. Collect some first.")
            lines = ["Collected artifacts:"]
            for a in collected:
                lines.append(f"  {a.artifact_id}  {a.name}  ({a.sell_value:.0f} credits)")
            lines.append("Usage: SELL <artifact_id>")
            return CommandResult.ok("SELL", *lines)

        artifact_id = args[0]
        earned      = self._arts.sell(artifact_id)

        if earned == 0.0:
            return CommandResult.fail(
                "SELL",
                f"Cannot sell {artifact_id!r} — not found or not collected.",
            )
        return CommandResult.ok(
            "SELL",
            f"Sold {artifact_id} for {earned:.0f} credits.",
            f"Total credits: {self._arts.currency:.0f}",
        )

    def _cmd_ls(self, args: list[str]) -> CommandResult:
        """Handle LS — list current directory.

        Parameters
        ----------
        args:
            Unused; present for dispatch signature consistency.
        """
        nodes = list(self._fs.list_directory())
        if not nodes:
            return CommandResult.ok("LS", "(empty directory)")

        lines = [f"{self._fs.path_to_cwd()}"]
        for node in nodes:
            icon = {
                NodeType.DIRECTORY: "DIR ",
                NodeType.FILE:      "FILE",
                NodeType.DEBRIS:    "DBRS",
            }[node.node_type]
            corruption_bar = self._corruption_bar(node.corruption)
            artifact_flag  = " [ART]" if node.has_artifact else ""
            lines.append(
                f"  [{icon}] {node.name:<24} {corruption_bar}  {node.visibility.name}{artifact_flag}"
            )
        return CommandResult.ok("LS", *lines)

    def _cmd_cd(self, args: list[str]) -> CommandResult:
        """Handle CD <target>.

        Parameters
        ----------
        args:
            Command arguments; first element is the directory name or ``..``.
        """
        if not args:
            return CommandResult.fail("CD", "Usage: CD <directory> | CD ..")

        target = args[0]
        node   = self._fs.change_directory(target)
        return CommandResult.ok("CD", f"-> {self._fs.path_to_cwd()}")

    def _cmd_pwd(self, args: list[str]) -> CommandResult:
        """Handle PWD — print working directory.

        Parameters
        ----------
        args:
            Unused.
        """
        return CommandResult.ok("PWD", self._fs.path_to_cwd())

    def _cmd_status(self, args: list[str]) -> CommandResult:
        """Handle STATUS — show resource levels.

        Parameters
        ----------
        args:
            Unused.
        """
        lines = ["SYSTEM STATUS"]
        for res in Resource:
            bar     = self._resource_bar(self._rm.ratio(res))
            current = self._rm.current(res)
            maximum = self._rm.maximum(res)
            lines.append(f"  {res.name:<8} {bar}  {current:>6.1f} / {maximum:.1f}")
        lines.append(f"  CREDITS  {self._arts.currency:.0f}")
        return CommandResult.ok("STATUS", *lines)

    def _cmd_help(self, args: list[str]) -> CommandResult:
        """Handle HELP — list all commands.

        Parameters
        ----------
        args:
            Unused.
        """
        lines = [
            "AVAILABLE COMMANDS",
            "  SCAN   <target>       Reveal a node (costs POWER)",
            "  SCAN   *              Reveal ALL nodes here (costs POWER)",
            "  CARVE  <target>       Convert DEBRIS to FILE (costs POWER + ENERGY)",
            "  RECON  <target>       Reconstruct artifact (costs POWER + MEMORY)",
            "  SELL   <artifact_id>  Sell a collected artifact",
            "  LS                    List current directory",
            "  CD     <dir>          Change directory (CD .. to go up)",
            "  PWD                   Show current path",
            "  STATUS                Show resource levels",
            "  HELP                  Show this message",
            "  QUIT                  Exit the program",
        ]
        return CommandResult.ok("HELP", *lines)

    def _cmd_quit(self, args: list[str]) -> CommandResult:
        """Handle QUIT — request clean shutdown.

        Parameters
        ----------
        args:
            Unused.
        """
        event_queue.post_immediate(
            EventType.QUIT_REQUESTED,
            {},
            source="CommandHandler",
        )
        return CommandResult.ok("QUIT", "Disconnecting from site...")

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _corruption_bar(corruption: float, width: int = 10) -> str:
        """Render a fixed-width ASCII corruption bar.

        Parameters
        ----------
        corruption:
            Float in ``[0.0, 1.0]``.
        width:
            Total bar width in characters.

        Returns
        -------
        str
            A string like ``[####......] 40%``.
        """
        filled = round(corruption * width)
        bar    = "#" * filled + "." * (width - filled)
        return f"[{bar}] {corruption:.0%}"

    @staticmethod
    def _resource_bar(ratio: float, width: int = 12) -> str:
        """Render a fixed-width ASCII resource bar.

        Parameters
        ----------
        ratio:
            Float in ``[0.0, 1.0]``.
        width:
            Total bar width in characters.

        Returns
        -------
        str
            A string like ``[============] 100%``.
        """
        filled = round(ratio * width)
        bar    = "=" * filled + " " * (width - filled)
        return f"[{bar}] {ratio:.0%}"