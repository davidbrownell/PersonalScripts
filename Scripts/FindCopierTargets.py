# noqa: D100
import os
import re

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Generator

import typer
import yaml

from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from typer.core import TyperGroup


# ----------------------------------------------------------------------
@dataclass
class CopierTarget:
    """Represents a found copier target with its metadata."""

    path: Path
    origin: str = field(default="<unknown>")
    version: str = field(default="<unknown>")


# ----------------------------------------------------------------------
class NaturalOrderGrouper(TyperGroup):  # noqa: D101
    # ----------------------------------------------------------------------
    def list_commands(self, *args, **kwargs) -> list[str]:  # noqa: ARG002, D102
        return list(self.commands.keys())  # pragma: no cover


# ----------------------------------------------------------------------
app = typer.Typer(
    cls=NaturalOrderGrouper,
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
        typer.Argument(help="Root directory to search for copier targets."),
    ],
    template_filename: Annotated[
        str,
        typer.Argument(help="Name of the copier answers file to search for."),
    ] = ".copier-answers.yml",
    verbose: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    """Given a directory, find all copier targets and display their template origin and version."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        # Resolve to absolute path for consistent handling
        directory = directory.resolve()

        if not directory.is_dir():
            dm.WriteError(f"'{directory}' is not a valid directory.\n")
            return

        # Find all copier targets
        targets: list[CopierTarget] = []

        with dm.Nested("Searching for copier targets...") as search_dm:
            for repo_root in _FindRepositoryRoots(directory):
                copier_file = repo_root / template_filename

                if not copier_file.is_file():
                    continue

                target = _ParseCopierFile(copier_file, directory)
                targets.append(target)

                if search_dm.is_verbose:
                    search_dm.WriteVerbose(f"Found: {target.path}\n")

        # Display results
        if not targets:
            dm.WriteLine("No copier targets found.\n")
            return

        _DisplayTable(targets, dm)


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
def _FindRepositoryRoots(directory: Path) -> Generator[Path, None, None]:
    """Find all git repository roots under the given directory."""

    for root, dirs, _files in os.walk(directory, followlinks=False):
        root_path = Path(root)

        if ".git" in dirs:
            # This is a repository root
            yield root_path
            # Don't descend into this repository
            dirs.clear()


# ----------------------------------------------------------------------
def _ParseCopierFile(copier_file: Path, base_directory: Path) -> CopierTarget:
    """Parse a copier answers file and extract origin and version."""

    with copier_file.open("r", encoding="utf-8") as f:
        content = f.read()

    data = yaml.safe_load(content)
    relative_path = copier_file.parent.relative_to(base_directory)

    origin = data.get("_src_path", "<unknown>")

    # If origin is ".", attempt to extract template URL from comments
    if origin == ".":
        origin = _ExtractTemplateUrlFromComments(content) or origin

    return CopierTarget(
        path=relative_path,
        origin=origin,
        version=data.get("_commit", "<unknown>"),
    )


# ----------------------------------------------------------------------
def _ExtractTemplateUrlFromComments(content: str) -> str | None:
    """Extract template URL from YAML file comments."""

    # Look for URLs in comment lines that mention "template"
    for line in content.splitlines():
        if line.startswith("#") and "template" in line.lower():
            # Search for a URL pattern in the line
            url_match = re.search(r"https?://[^\s)]+", line)
            if url_match:
                return url_match.group(0)

    return None


# ----------------------------------------------------------------------
def _DisplayTable(targets: list[CopierTarget], dm: DoneManager) -> None:
    """Display copier targets in a formatted table."""

    # Calculate column widths
    path_header = "Path"
    origin_header = "Template Origin"
    version_header = "Version"

    path_width = max(len(path_header), max(len(str(t.path)) for t in targets))
    origin_width = max(len(origin_header), max(len(t.origin) for t in targets))
    version_width = max(len(version_header), max(len(t.version) for t in targets))

    # Build table
    separator = f"+{'-' * (path_width + 2)}+{'-' * (origin_width + 2)}+{'-' * (version_width + 2)}+"
    header = f"| {path_header:<{path_width}} | {origin_header:<{origin_width}} | {version_header:<{version_width}} |"

    dm.WriteLine("")
    dm.WriteLine(separator)
    dm.WriteLine(header)
    dm.WriteLine(separator)

    for target in targets:
        row = f"| {str(target.path):<{path_width}} | {target.origin:<{origin_width}} | {target.version:<{version_width}} |"
        dm.WriteLine(row)

    dm.WriteLine(separator)
    dm.WriteLine(f"\nFound {len(targets)} copier target(s).\n")


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
