import os

from collections.abc import Generator
from pathlib import Path


# ----------------------------------------------------------------------
def FindRepositoryRoots(directory: Path) -> Generator[Path]:
    """Find all git repository roots under the given directory."""

    for root, dirs, _files in os.walk(directory, followlinks=False):
        if ".git" in dirs:
            yield Path(root)

            # Don't descend into this repository
            dirs.clear()
