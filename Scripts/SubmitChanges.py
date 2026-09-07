# noqa: INP001
"""Commit the changes to files within one or more repositories and shepherd them to mainline.

Creates a branch within each repository, commits and pushes the provided files, creates a pull
request, waits for its builds to pass, merges it, and waits for the mainline builds to complete.
"""

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags

from Impl import PullRequestWorkflow
from Impl.RepositoryUtils import ExecuteInParallel


# ----------------------------------------------------------------------
_BRANCH_NAME_PREFIX = "submit-changes"


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
    commit_message: Annotated[
        str,
        typer.Argument(help="Message associated with the commit and its pull request."),
    ],
    repositories: Annotated[
        list[Path],
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Repositories with changed files to submit.",
        ),
    ],
    filenames: Annotated[
        list[str],
        typer.Option(
            "--filename",
            help="Name of a file (relative to each repository) to commit; can be provided multiple times.",
        ),
    ],
    branch_name: Annotated[
        str | None,
        typer.Option("--branch-name", help="Name of the branch created for the pull requests."),
    ] = None,
    timeout_minutes: Annotated[
        int,
        typer.Option("--timeout-minutes", min=1, help="Maximum time to wait for each build step."),
    ] = 60,
    max_num_threads: Annotated[
        int | None,
        typer.Option("--max-num-threads", min=1, help="Maximum number of repositories processed at once."),
    ] = None,
    no_merge: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--no-merge", help="Create the pull requests and wait for their builds, but do not merge."
        ),
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
    """Commit the changes to files within one or more repositories and shepherd them to mainline."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        _Preflight(dm, repositories, filenames)

        # Every repository shares a branch name so that the pull requests are easy to correlate.
        branch_name = branch_name or PullRequestWorkflow.CreateBranchName(_BRANCH_NAME_PREFIX)

        # ----------------------------------------------------------------------
        def Submit(
            repository_dm: DoneManager,
            repository: Path,
            on_activity_func: Callable[[Enum], None],
        ) -> tuple[PullRequestWorkflow.WorkflowResult | None, str | None]:
            on_activity_func(_SubmitActivity.QueryingRepository)

            with repository_dm.Nested("Querying the repository...") as query_dm:
                repository_info = PullRequestWorkflow.GetRepositoryInfo(query_dm, repository)

            with PullRequestWorkflow.RestoreBranch(
                repository_dm,
                repository,
                repository_info.original_branch,
            ):
                result = PullRequestWorkflow.ShepherdChanges(
                    repository_dm,
                    repository,
                    repository_info,
                    branch_name=branch_name,
                    commit_message=commit_message,
                    filenames=filenames,
                    timeout_minutes=timeout_minutes,
                    on_activity_func=on_activity_func,
                    no_merge=no_merge,
                )

            return result, "merged" if result.merge_commit is not None else "pull request created"

        # ----------------------------------------------------------------------

        results = ExecuteInParallel(
            dm,
            "Submitting",
            repositories,
            _ACTIVITIES,
            Submit,
            max_num_threads=max_num_threads,
        )

        for repository in repositories:
            result = results.get(repository)

            if result is not None:
                dm.WriteLine("\n{}".format(repository))
                PullRequestWorkflow.DisplayResult(dm, result)


# ----------------------------------------------------------------------
# |
# |  Private Types
# |
# ----------------------------------------------------------------------
class _SubmitActivity(Enum):
    """Activities performed before the changes are shepherded to mainline."""

    QueryingRepository = "Querying the repository"


# ----------------------------------------------------------------------
_ACTIVITIES: list[Enum] = [*_SubmitActivity, *PullRequestWorkflow.Activity]


# ----------------------------------------------------------------------
# |
# |  Private Functions
# |
# ----------------------------------------------------------------------
def _Preflight(dm: DoneManager, repositories: list[Path], filenames: list[str]) -> None:
    """Validate the environment and the state of each repository.

    The repositories are validated before any of them are submitted so that a repository without
    changes is reported on the terminal rather than within the log of a failed task.
    """

    with dm.Nested("Validating the repositories...", suffix="\n") as preflight_dm:
        PullRequestWorkflow.EnsureTools(["git", "gh"])

        without_changes = [
            repository
            for repository in repositories
            if not PullRequestWorkflow.HasChanges(preflight_dm, repository, *filenames)
        ]

        if without_changes:
            msg = "\n".join(
                ["The following repositories do not have any changes to the provided files:", ""]
                + ["  - {}".format(repository) for repository in without_changes]
                + [""],
            )
            raise Exception(msg)


# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
