# noqa: INP001
"""Find Python dependency versions across repositories.

Searches for a specific package in all uv.lock files found within
git repositories under a given directory.
"""

import re
import tomllib

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from Impl.RepositoryUtils import FindRepositoryRoots


# ----------------------------------------------------------------------
@dataclass
class DependencyVersionInfo:
    """Represents a found dependency version with its metadata."""

    path: Path
    version: str


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
            help="Root directory to search for uv.lock files.",
        ),
    ],
    package_name: Annotated[
        str,
        typer.Argument(
            help="Name of the Python package to search for (case-insensitive).",
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
    """Find versions of a Python package across all uv.lock files in repositories."""
    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        results: list[DependencyVersionInfo] = []
        normalized_search_name = _NormalizePackageName(package_name)

        with dm.Nested(f"Searching for '{package_name}' in uv.lock files...") as search_dm:
            for repo_root in FindRepositoryRoots(directory):
                if search_dm.is_verbose:
                    search_dm.WriteVerbose(f"Scanning repository: {repo_root.name}\n")

                uv_lock_path = repo_root / "uv.lock"
                if not uv_lock_path.is_file():
                    continue

                version = _ExtractVersionFromUvLock(uv_lock_path, normalized_search_name, search_dm)
                if version is None:
                    continue

                relative_path = uv_lock_path.parent.relative_to(directory)

                results.append(DependencyVersionInfo(path=relative_path, version=version))

                if search_dm.is_verbose:
                    search_dm.WriteVerbose(f"    Found: {package_name} {version}\n")

        if not results:
            dm.WriteLine(f"\nPackage '{package_name}' not found in any uv.lock files.\n")
            return

        _DisplayTable(results, package_name, dm)


# ----------------------------------------------------------------------
def _NormalizePackageName(name: str) -> str:
    """Normalize a package name for comparison per PEP 503."""
    return re.sub(r"[-_.]+", "-", name.lower())


# ----------------------------------------------------------------------
def _ExtractVersionFromUvLock(
    uv_lock_path: Path,
    normalized_package_name: str,
    dm: DoneManager,
) -> str | None:
    """Extract the version of a specific package from uv.lock."""
    try:
        with uv_lock_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        if dm.is_debug:
            dm.WriteDebug(f"    Failed to parse {uv_lock_path}: {e}\n")
        return None
    except OSError as e:
        if dm.is_debug:
            dm.WriteDebug(f"    Failed to read {uv_lock_path}: {e}\n")
        return None

    packages = data.get("package", [])

    for package in packages:
        name = package.get("name", "")
        if _NormalizePackageName(name) == normalized_package_name:
            return package.get("version", "<unknown>")

    return None


# ----------------------------------------------------------------------
def _DisplayTable(
    results: list[DependencyVersionInfo],
    package_name: str,
    dm: DoneManager,
) -> None:
    """Display dependency versions in a formatted table."""
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
    dm.WriteLine(f"\nFound '{package_name}' in {len(results)} uv.lock file(s).\n")


# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
