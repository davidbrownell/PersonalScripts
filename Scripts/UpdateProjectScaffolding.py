# ----------------------------------------------------------------------
# |
# |  UpdateProjectScaffolding.py
# |
# |  David Brownell <db@DavidBrownell.com>
# |      2024-09-06 13:00:53
# |
# ----------------------------------------------------------------------
# |
# |  Copyright David Brownell 2024
# |  Distributed under the MIT License.
# |
# ----------------------------------------------------------------------
"""Updates project scaffolding for all repositories in the specified location."""

import os
import textwrap

from enum import auto, Enum
from pathlib import Path
from typing import Annotated, Any, Callable, cast

import typer

from dbrownell_Common import ExecuteTasks  # type: ignore[import-untyped]
from dbrownell_Common.InflectEx import inflect  # type: ignore[import-untyped]
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags  # type: ignore[import-untyped]
from dbrownell_Common import SubprocessEx  # type: ignore[import-untyped]
from typer.core import TyperGroup


# ----------------------------------------------------------------------
class NaturalOrderGrouper(TyperGroup):
    # pylint: disable=missing-class-docstring
    # ----------------------------------------------------------------------
    def list_commands(self, *args, **kwargs):  # pylint: disable=unused-argument
        return self.commands.keys()


# ----------------------------------------------------------------------
app = typer.Typer(
    cls=NaturalOrderGrouper,
    help=__doc__,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)


# ----------------------------------------------------------------------
@app.command("EntryPoint", help=__doc__, no_args_is_help=True)
def EntryPoint(
    input_directory: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            exists=True,
            resolve_path=True,
            help="Location used to search for repositories to update.",
        ),
    ],
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        repositories: list[Path] = []

        with dm.Nested(
            f"Searching for repositories in '{input_directory}'...",
            lambda: "{} found".format(inflect.no("repository", len(repositories))),
        ):
            for root_str, directories, _ in os.walk(input_directory):
                root = Path(root_str)

                if (root / ".copier-answers.yml").is_file():
                    repositories.append(root)
                    directories[:] = []

        if not repositories:
            return

        # ----------------------------------------------------------------------
        class Activity(Enum):
            CheckChanges1 = 0
            Update = auto()
            CheckChanges2 = auto()
            Resolve = auto()
            CheckChanges3 = auto()

        # ----------------------------------------------------------------------
        def PrepareTask(
            context: Any,
            on_simple_status_func: Callable[  # pylint: disable=unused-argument
                [str], None
            ],
        ) -> tuple[int, ExecuteTasks.TransformTasksExTypes.TransformFuncType]:
            repository = cast(Path, context)
            del context

            # ----------------------------------------------------------------------
            def Transform(
                status: ExecuteTasks.Status,
            ) -> bool:
                # ----------------------------------------------------------------------
                def CheckChanges(
                    activity: Activity,
                ) -> bool:
                    status.OnProgress(activity.value, "Checking for changes...")

                    command_line = "git status --porcelain"
                    status.OnInfo(f"Running '{command_line}'", verbose=True)

                    result = SubprocessEx.Run(
                        command_line,
                        cwd=repository,
                    )

                    return bool(result.output.strip())

                # ----------------------------------------------------------------------

                # Check for local changes
                if CheckChanges(Activity.CheckChanges1):
                    status.OnInfo(
                        f"Changes detected in '{repository}'; the repository will not be processed."
                    )
                    return False

                # Update the repository
                status.OnProgress(Activity.Update.value, "Updating...")

                command_line = "docker run -it --rm -v .:/output gtssecenter/copier-projectscaffolding update /output --trust --skip-answered"
                status.OnInfo(f"Running '{command_line}'", verbose=True)

                result = SubprocessEx.Run(
                    command_line,
                    cwd=repository,
                )

                if result.returncode != 0:
                    raise Exception(
                        textwrap.dedent(
                            f"""\
                            The command '{command_line}' failed in '{repository}' with the output:

                            {result.output}
                            """,
                        ),
                    )

                # Check for changes after the update
                if not CheckChanges(Activity.CheckChanges2):
                    return False

                # Resolve the changes
                # BugBug

                # Check for changes after resolving the files
                return CheckChanges(Activity.CheckChanges3)

            # ----------------------------------------------------------------------

            return len(Activity), Transform

        # ----------------------------------------------------------------------

        results = ExecuteTasks.TransformTasksEx(
            dm,
            "Processing {}...".format(inflect.plural("repository", len(repositories))),
            [
                ExecuteTasks.TaskData(
                    str(
                        repository.relative_to(input_directory)
                        if repository != input_directory
                        else repository
                    ),
                    repository,
                )
                for repository in repositories
            ],
            PrepareTask,
        )

        # Write the repos that have changes
        modified: list[Path] = []

        for result, repository in zip(results, repositories):
            if result:
                modified.append(repository)

        dm.WriteLine("")

        dm.WriteLine(
            "{} {} modified.\n".format(
                inflect.no("repository", len(modified)),
                inflect.plural_verb("was", len(modified)),
            ),
        )

        if modified:
            dm.WriteLine(
                "\n{}\n\n".format(
                    "\n".join(f"    - {repository}" for repository in modified)
                )
            )


# ----------------------------------------------------------------------
@app.command("Validate", help="BugBug", no_args_is_help=True)
def Validate(
    output_dir: Annotated[
        Path, typer.Argument(file_okay=False, resolve_path=True, exists=True)
    ],
) -> None:
    found = 0

    for filename in [
        "CONTRIBUTING.md",
        "DEVELOPMENT.md",
        "README.md",
        "SECURITY.md",
        "post_generation_actions.html",
    ]:
        fullpath = output_dir / filename

        if not fullpath.is_file():
            continue

        print(f"Validating '{filename}'...")
        found += 1

        filename_bytes = fullpath.read_bytes()
        assert "\r\n".encode() not in filename_bytes, fullpath

    assert found != 0


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()
