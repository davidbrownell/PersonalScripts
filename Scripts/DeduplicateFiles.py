# noqa: D100
import hashlib
import os
import textwrap

from pathlib import Path
from typing import Annotated, Callable

import typer

from dbrownell_Common import ExecuteTasks
from dbrownell_Common.InflectEx import inflect
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from typer.core import TyperGroup


# ----------------------------------------------------------------------
class NaturalOrderGrouper(TyperGroup):  # noqa: D101
    # ----------------------------------------------------------------------
    def list_commands(self, *args, **kwargs) -> list[str]:  # noqa: ARG002, D102
        return list(self.commands.keys())


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
    input_directory: Annotated[
        Path,
        typer.Argument(
            exists=True, file_okay=False, resolve_path=True, help="Directory to search for duplicates."
        ),
    ],
    clean: Annotated[
        bool,
        typer.Option(
            "--clean", help="Remove duplicate filenames (the first filename encountered will be preserved)."
        ),
    ] = False,
    ssd: Annotated[
        bool,
        typer.Option("--ssd", help="Hashes will be calculated in parallel for solid state drives."),
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    """Deduplicate files."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        all_filenames: list[Path] = []

        with dm.Nested(
            f"Calculating files in '{input_directory}'...",
            lambda: "{} found".format(inflect.no("file", len(all_filenames))),
            suffix="\n",
        ):
            for root_str, _, filenames in os.walk(input_directory):
                root = Path(root_str)

                for filename in filenames:
                    fullpath = root / filename

                    if fullpath.stat().st_size == 0:
                        continue

                    all_filenames.append(fullpath)

        if not all_filenames:
            return

        duplicated_files: dict[tuple[str, str], list[Path]] = {}

        with dm.Nested(
            "Organizing files...",
            lambda: "{} found".format(inflect.no("duplicated file", len(duplicated_files))),
            suffix="\n",
        ) as hash_dm:
            # ----------------------------------------------------------------------
            def CalculateHash(
                context: Path,
                on_simple_status_func: Callable[[str], None],
            ) -> tuple[
                int,
                ExecuteTasks.TransformTasksExTypes.TransformFuncType,
            ]:
                filename = context
                del context

                # ----------------------------------------------------------------------
                def Execute(
                    status: ExecuteTasks.Status,
                ) -> str:
                    status_bytes = 0

                    hasher = hashlib.sha512()

                    with filename.open("rb") as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break

                            hasher.update(chunk)

                            status_bytes += len(chunk)
                            status.OnProgress(status_bytes, None)

                    return hasher.hexdigest()

                # ----------------------------------------------------------------------

                return filename.stat().st_size, Execute

            # ----------------------------------------------------------------------

            hash_values = ExecuteTasks.TransformTasksEx(
                hash_dm,
                "Calculating file hashes...",
                [
                    ExecuteTasks.TaskData(
                        str(filename),
                        filename,
                    )
                    for filename in all_filenames
                ],
                CalculateHash,
                max_num_threads=None if ssd else 1,
                refresh_per_second=2,
            )

            organized_files: dict[tuple[str, str], list[Path]] = {}

            for filename, hash_value in zip(all_filenames, hash_values):
                assert isinstance(filename, Path), filename
                assert isinstance(hash_value, str), hash_value

                organized_files.setdefault(
                    (filename.name, hash_value),
                    [],
                ).append(filename)

            with hash_dm.Nested("Identifying duplicates"):
                for key, filenames in organized_files.items():
                    if len(filenames) != 1:
                        duplicated_files[key] = filenames

        if not duplicated_files:
            return

        if not clean or dm.is_verbose:
            with dm.YieldStream() as stream:
                for index, (key, filenames) in enumerate(duplicated_files.items()):
                    stream.write(
                        textwrap.dedent(
                            """\
                            {index}) {filename}
                            {files}

                            """,
                        ).format(
                            index=index + 1,
                            filename=key[0],
                            files="\n".join(f"  - {filename}" for filename in filenames),
                        ),
                    )

        if clean:
            num_cleaned_files = 0

            with dm.Nested(
                "Cleaning duplicate files...",
                lambda: "{} cleaned".format(inflect.no("duplicated file", num_cleaned_files)),
                suffix="\n",
            ) as clean_dm:
                for filenames in duplicated_files.values():
                    with clean_dm.Nested(
                        f"Preserving '{filenames[0]}'...",
                        suffix="\n",
                    ) as this_dm:
                        for filename in filenames[1:]:
                            with this_dm.Nested(f"Removing '{filename}'..."):
                                filename.unlink()
                                num_cleaned_files += 1


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()
