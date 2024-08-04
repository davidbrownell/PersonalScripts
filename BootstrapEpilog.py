# pylint: disable=missing-module-docstring

import subprocess
import sys

from pathlib import Path


# Parse the arguments
no_cache = False

display_flags: list[str] = []

# First arg is the script name, second arg is the name of the shell script to write to
for arg in sys.argv[2:]:
    if arg == "--no-cache":
        no_cache = True
    else:
        sys.stderr.write(f"WARNING: '{arg}' is not a recognized argument.\n")

subprocess.run(
    "pip install --disable-pip-version-check {} --requirement requirements.txt".format(
        "--no-cache-dir" if no_cache else "",
    ),
    check=True,
    shell=True,
    cwd=Path(__file__).parent,
)
