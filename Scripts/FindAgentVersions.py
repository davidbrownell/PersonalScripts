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

    name: str
    path: Path
    header: str = field(default="")
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
    """Given a directory, find all agent files and display their version."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        results: list[AgentVersionInfo] = []

        with dm.Nested("Searching for AGENTS.md files...") as search_dm:
            for repo_root in FindRepositoryRoots(directory):
                for potential_agents_file in ["AGENTS.md", "CLAUDE.md"]:
                    agents_file = repo_root / potential_agents_file

                    if not agents_file.is_file():
                        continue

                    header, version = _ExtractVersionFromAgentsFile(agents_file)
                    relative_path = agents_file.parent.relative_to(directory)

                    results.append(
                        AgentVersionInfo(
                            name=potential_agents_file,
                            path=relative_path,
                            header=header,
                            version=version,
                        )
                    )

                    if search_dm.is_verbose:
                        search_dm.WriteVerbose(
                            f"Found: {relative_path} (Header: {header or '<none>'}, Version: {version})\n"
                        )

        if not results:
            dm.WriteLine("No AGENTS.md files found.\n")
            return

        _DisplayTable(results, dm)


# ----------------------------------------------------------------------
def _ExtractVersionFromAgentsFile(agents_file: Path) -> tuple[str, str]:
    """Extract the optional header and version from HTML comments in AGENTS.md."""

    comment_block_regex = re.compile(r"<!--(.*?)-->", re.DOTALL)
    version_regex = re.compile(r"\s*(.*?)\s*\bversion:\s*(\S+)", re.IGNORECASE)

    with agents_file.open("r", encoding="utf-8") as f:
        content = f.read()

    for comment_match in comment_block_regex.finditer(content):
        comment_content = comment_match.group(1)
        version_match = version_regex.match(comment_content)
        if version_match:
            return version_match.group(1), version_match.group(2)

    return "", "<unknown>"


# ----------------------------------------------------------------------
def _DisplayTable(results: list[AgentVersionInfo], dm: DoneManager) -> None:
    """Display agent versions in a formatted table."""

    headers = ["Path", "Name", "Header", "Version"]
    rows = [[str(r.path), r.name, r.header, r.version] for r in results]

    widths = [max(len(value) for value in column) for column in zip(headers, *rows, strict=True)]

    separator = "+" + "+".join("-" * (width + 2) for width in widths) + "+"

    def FormatRow(values: list[str]) -> str:
        return (
            "| " + " | ".join(f"{value:<{width}}" for value, width in zip(values, widths, strict=True)) + " |"
        )

    dm.WriteLine("")
    dm.WriteLine(separator)
    dm.WriteLine(FormatRow(headers))
    dm.WriteLine(separator)

    for row in rows:
        dm.WriteLine(FormatRow(row))

    dm.WriteLine(separator)
    dm.WriteLine(f"\nFound {len(results)} agent file(s).\n")


# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
