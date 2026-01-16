# noqa: INP001
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from Impl.RepositoryUtils import FindRepositoryRoots


# ----------------------------------------------------------------------
@dataclass
class AgentVersionInfo:
    """Represents a found AGENTS.md version with its metadata."""

    path: Path
    version: str = field(default="<unknown>")


# ----------------------------------------------------------------------
app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)


# ----------------------------------------------------------------------
@app.command("EntryPoint", no_args_is_help=True)
def EntryPoint(
    directory: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Root directory to search for agent versions.",
        ),
    ],
    verbose: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    """Given a directory, find all AGENTS.md files and display their version."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        results: list[AgentVersionInfo] = []

        with dm.Nested("Searching for AGENTS.md files...") as search_dm:
            for repo_root in FindRepositoryRoots(directory):
                agents_file = repo_root / "AGENTS.md"

                if not agents_file.is_file():
                    continue

                version = _ExtractVersionFromAgentsFile(agents_file)
                relative_path = agents_file.parent.relative_to(directory)

                results.append(AgentVersionInfo(path=relative_path, version=version))

                if search_dm.is_verbose:
                    search_dm.WriteVerbose(f"Found: {relative_path} (Version: {version})\n")

        if not results:
            dm.WriteLine("No AGENTS.md files found.\n")
            return

        _DisplayTable(results, dm)


# ----------------------------------------------------------------------
def _ExtractVersionFromAgentsFile(agents_file: Path) -> str:
    """Extract the version from HTML comments in AGENTS.md."""

    comment_block_regex = re.compile(r"<!--(.*?)-->", re.DOTALL)
    version_regex = re.compile(r"version:\s*([^\s]+)", re.IGNORECASE)

    with agents_file.open("r", encoding="utf-8") as f:
        content = f.read()

    for comment_match in comment_block_regex.finditer(content):
        comment_content = comment_match.group(1)
        version_match = version_regex.search(comment_content)
        if version_match:
            return version_match.group(1).strip()

    return "<unknown>"


# ----------------------------------------------------------------------
def _DisplayTable(results: list[AgentVersionInfo], dm: DoneManager) -> None:
    """Display agent versions in a formatted table."""

    path_header = "Path"
    version_header = "Version"

    path_width = max(len(path_header), max(len(str(r.path)) for r in results))  # noqa: PLW3301
    version_width = max(len(version_header), max(len(r.version) for r in results))  # noqa: PLW3301

    separator = f"+{'-' * (path_width + 2)}+{'-' * (version_width + 2)}+"
    header = f"| {path_header:<{path_width}} | {version_header:<{version_width}} |"

    dm.WriteLine("")
    dm.WriteLine(separator)
    dm.WriteLine(header)
    dm.WriteLine(separator)

    for result in results:
        row = f"| {result.path!s:<{path_width}} | {result.version:<{version_width}} |"
        dm.WriteLine(row)

    dm.WriteLine(separator)
    dm.WriteLine(f"\nFound {len(results)} AGENTS.md file(s).\n")


# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
