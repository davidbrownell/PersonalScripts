"""Shepherd local changes to mainline via a pull request.

Commits the changes within a repository on a new branch, pushes them, creates a pull request, waits
for its builds to pass, merges it, and waits for the mainline builds to complete.
"""

import json
import os
import shutil
import time

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from dbrownell_Common import PathEx, SubprocessEx
from dbrownell_Common.Streams.DoneManager import DoneManager


# ----------------------------------------------------------------------
_POLL_INTERVAL_SECONDS = 30

# Mainline is typically configured to require that a branch be up to date before it can be merged,
# so a merge can legitimately fail once when mainline advances while the checks are running.
_MAX_MERGE_ATTEMPTS = 2

# `SubprocessEx.Run` merges stderr into stdout, so the update notification that `gh` writes to
# stderr would otherwise corrupt the JSON written by `gh ... --json`.
_ENVIRONMENT_OVERRIDES: dict[str, str] = {"GH_NO_UPDATE_NOTIFIER": "1"}


# ----------------------------------------------------------------------
# |
# |  Public Types
# |
# ----------------------------------------------------------------------
class Activity(Enum):
    """Activities performed by `ShepherdChanges`, declared in the order that they are performed."""

    CommittingAndPushing = "Committing and pushing the changes"
    CreatingPullRequest = "Creating the pull request"
    WaitingForPullRequestChecks = "Waiting for the pull request checks"
    MergingPullRequest = "Merging the pull request"
    WaitingForMainlineBuilds = "Waiting for the mainline builds"
    UpdatingLocalRepository = "Updating the local repository"


# ----------------------------------------------------------------------
@dataclass
class RepositoryInfo:
    """Repository state needed to drive the pull request workflow."""

    # The branch (or commit, when the repository is in a detached HEAD state) that is currently
    # checked out; it is restored by commit rather than by name in the latter case.
    original_branch: str

    default_branch: str
    has_workflows: bool


# ----------------------------------------------------------------------
@dataclass
class WorkflowResult:
    """The outcome of shepherding changes to mainline."""

    pull_request_url: str

    merge_commit: str | None = field(default=None)
    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# |
# |  Public Functions
# |
# ----------------------------------------------------------------------
def ShepherdChanges(
    dm: DoneManager,
    repository: Path,
    repository_info: RepositoryInfo,
    *,
    branch_name: str,
    commit_message: str,
    filenames: Sequence[str],
    pull_request_body: str | None = None,
    timeout_minutes: int,
    on_activity_func: Callable[[Activity], None],
    no_merge: bool = False,
) -> WorkflowResult:
    """Commit the changes within a repository and shepherd them to mainline.

    `filenames` are the repository-relative files to commit.

    `on_activity_func` is invoked with the current activity, which is useful when the output written
    to `dm` is not visible.
    """

    on_activity_func(Activity.CommittingAndPushing)
    _CommitAndPush(dm, repository, branch_name, commit_message, filenames)

    on_activity_func(Activity.CreatingPullRequest)

    result = WorkflowResult(
        pull_request_url=_CreatePullRequest(
            dm,
            repository,
            repository_info.default_branch,
            pull_request_body,
        ),
    )

    pull_request_id = result.pull_request_url.rsplit("/", 1)[-1]

    if repository_info.has_workflows:
        on_activity_func(Activity.WaitingForPullRequestChecks)
        _WaitForChecks(dm, repository, pull_request_id, timeout_minutes=timeout_minutes)

    if no_merge:
        result.warnings.append("'--no-merge' was specified; the pull request was not merged.")
        return result

    on_activity_func(Activity.MergingPullRequest)

    result.merge_commit = _Merge(
        dm,
        repository,
        pull_request_id,
        timeout_minutes=timeout_minutes,
        wait_for_checks=repository_info.has_workflows,
    )

    if repository_info.has_workflows:
        on_activity_func(Activity.WaitingForMainlineBuilds)
        _WaitForWorkflowRuns(dm, repository, result.merge_commit, timeout_minutes=timeout_minutes)

    on_activity_func(Activity.UpdatingLocalRepository)
    _SyncLocalRepository(dm, repository, repository_info.default_branch, warnings=result.warnings)

    return result


# ----------------------------------------------------------------------
def GetRepositoryInfo(dm: DoneManager, repository: Path) -> RepositoryInfo:
    """Query the repository state needed to drive the pull request workflow."""

    # `git symbolic-ref` fails when the repository is in a detached HEAD state, which is restored by
    # commit rather than by name.
    symbolic_ref_result = RunRaw(dm, "git symbolic-ref --quiet --short HEAD", repository)

    original_branch = (
        symbolic_ref_result.output.strip()
        if symbolic_ref_result.returncode == 0
        else Run(dm, "git rev-parse HEAD", repository).strip()
    )

    workflows_path = repository / ".github" / "workflows"

    return RepositoryInfo(
        original_branch=original_branch,
        default_branch=RunJson(dm, "gh repo view --json defaultBranchRef", repository)["defaultBranchRef"][
            "name"
        ],
        has_workflows=workflows_path.is_dir() and any(workflows_path.glob("*.y*ml")),
    )


# ----------------------------------------------------------------------
def EnsureTools(tool_names: Iterable[str]) -> None:
    """Ensure that each of the tools is available on the path."""

    for tool_name in tool_names:
        if shutil.which(tool_name) is None:
            msg = "'{}' was not found on the path.".format(tool_name)
            raise Exception(msg)


# ----------------------------------------------------------------------
def HasChanges(dm: DoneManager, repository: Path, *filenames: str) -> bool:
    """Determine if the repository has uncommitted changes, optionally limited to specific files."""

    command_line = "git status --porcelain"

    if filenames:
        command_line += " -- {}".format(" ".join('"{}"'.format(filename) for filename in filenames))

    return bool(Run(dm, command_line, repository).strip())


# ----------------------------------------------------------------------
def CreateBranchName(prefix: str) -> str:
    """Create a branch name that is unique to this invocation."""

    return "{}-{}".format(prefix, datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))


# ----------------------------------------------------------------------
def DisplayResult(dm: DoneManager, result: WorkflowResult) -> None:
    """Display the outcome of the workflow."""

    dm.WriteLine("Pull Request: {}\n".format(result.pull_request_url))

    if result.merge_commit is not None:
        dm.WriteLine("Merge Commit: {}\n".format(result.merge_commit))

    for warning in result.warnings:
        dm.WriteWarning(warning + "\n")


# ----------------------------------------------------------------------
@contextmanager
def RestoreBranch(dm: DoneManager, repository: Path, branch: str) -> Iterator[None]:
    """Restore the branch that was checked out before the workflow began."""

    try:
        yield
    finally:
        result = RunRaw(dm, "git checkout {}".format(branch), repository)

        if result.returncode != 0:
            # Restoring the branch is a convenience, so a failure here must not mask the outcome of
            # the workflow itself.
            dm.WriteWarning(
                "'{}' could not be restored:\n{}\n".format(branch, result.output.rstrip()),
            )


# ----------------------------------------------------------------------
def RunRaw(dm: DoneManager, command_line: str, repository: Path) -> SubprocessEx.RunResult:
    """Run a command within the repository and return its result."""

    dm.WriteVerbose("Running '{}'...\n".format(command_line))

    # `SubprocessEx.Run` replaces the environment rather than augmenting it.
    result = SubprocessEx.Run(
        command_line,
        cwd=repository,
        env={**os.environ, **_ENVIRONMENT_OVERRIDES},
    )

    dm.WriteDebug("{}\n{}".format(result.returncode, result.output))

    return result


# ----------------------------------------------------------------------
def Run(dm: DoneManager, command_line: str, repository: Path) -> str:
    """Run a command within the repository, raising on error."""

    result = RunRaw(dm, command_line, repository)
    result.RaiseOnError()

    return result.output


# ----------------------------------------------------------------------
def RunJson(dm: DoneManager, command_line: str, repository: Path) -> Any:  # noqa: ANN401
    """Run a command within the repository and parse its output as JSON."""

    return ExtractJson(Run(dm, command_line, repository))


# ----------------------------------------------------------------------
def ExtractJson(output: str) -> Any:  # noqa: ANN401
    """Parse the JSON within command output, ignoring any diagnostics that precede it.

    `SubprocessEx.Run` merges stderr into stdout, so a diagnostic written by an otherwise
    successful command would prevent the output from being parsed.

    Raises `ValueError` when the output does not contain JSON.
    """

    indexes = [index for index in [output.find("{"), output.find("[")] if index != -1]

    if not indexes:
        msg = "JSON content was not found in:\n{}".format(output)
        raise ValueError(msg)

    return json.loads(output[min(indexes) :])


# ----------------------------------------------------------------------
# |
# |  Private Functions
# |
# ----------------------------------------------------------------------
def _CommitAndPush(
    dm: DoneManager,
    repository: Path,
    branch_name: str,
    commit_message: str,
    filenames: Sequence[str],
) -> None:
    """Commit the changes on a new branch and push it."""

    with dm.Nested("Committing and pushing '{}'...".format(branch_name)) as commit_dm:
        Run(commit_dm, "git checkout -b {}".format(branch_name), repository)

        Run(
            commit_dm,
            "git add {}".format(" ".join('"{}"'.format(filename) for filename in filenames)),
            repository,
        )

        # The commit message may contain backticks, which a POSIX shell would interpret as command
        # substitution; a file keeps the message byte-exact on every platform.
        with _TemporaryTextFile(commit_message.rstrip() + "\n") as message_filename:
            Run(commit_dm, 'git commit --file "{}"'.format(message_filename), repository)

        Run(commit_dm, "git push --set-upstream origin {}".format(branch_name), repository)


# ----------------------------------------------------------------------
def _CreatePullRequest(
    dm: DoneManager,
    repository: Path,
    default_branch: str,
    body: str | None,
) -> str:
    """Create the pull request and return its url."""

    with dm.Nested("Creating the pull request...") as pr_dm:
        # '--fill' takes the title and body from the commit, which avoids passing characters that a
        # shell would interpret through the command line.
        command_line = "gh pr create --base {} --fill".format(default_branch)

        with ExitStack() as exit_stack:
            if body is not None:
                body_filename = exit_stack.enter_context(_TemporaryTextFile(body))
                command_line += ' --body-file "{}"'.format(body_filename)

            output = Run(pr_dm, command_line, repository)

        for line in reversed(output.splitlines()):
            if line.startswith("https://"):
                pr_dm.WriteLine("{}\n".format(line))
                return line

        msg = "A pull request url could not be extracted from:\n{}".format(output)
        raise Exception(msg)


# ----------------------------------------------------------------------
def _WaitForChecks(
    dm: DoneManager,
    repository: Path,
    pull_request_id: str,
    *,
    timeout_minutes: int,
) -> None:
    """Wait for the pull request's checks to complete, raising when any of them do not pass."""

    with dm.Nested("Waiting for the pull request checks...") as checks_dm:
        # ----------------------------------------------------------------------
        def Poll() -> list[dict[str, Any]] | None:
            # `gh pr checks` exits with an error code when checks are pending or failing and writes
            # a diagnostic rather than JSON when no checks have been created yet.
            result = RunRaw(
                checks_dm,
                "gh pr checks {} --json name,bucket,link".format(pull_request_id),
                repository,
            )

            try:
                checks = ExtractJson(result.output)
            except ValueError:
                return None

            if any(check["bucket"] == "pending" for check in checks):
                return None

            return checks

        # ----------------------------------------------------------------------

        checks = _PollUntil(checks_dm, "The checks", Poll, timeout_minutes=timeout_minutes)

        failures = [check for check in checks if check["bucket"] not in ["pass", "skipping"]]

        if failures:
            msg = "\n".join(
                ["The following pull request checks did not pass:", ""]
                + [
                    "  - {} [{}] {}".format(check["name"], check["bucket"], check["link"])
                    for check in failures
                ]
                + [""],
            )
            raise Exception(msg)


# ----------------------------------------------------------------------
def _Merge(
    dm: DoneManager,
    repository: Path,
    pull_request_id: str,
    *,
    timeout_minutes: int,
    wait_for_checks: bool,
) -> str:
    """Merge the pull request and return the merge commit."""

    with dm.Nested("Merging the pull request...") as merge_dm:
        for attempt in range(_MAX_MERGE_ATTEMPTS):
            head_sha = RunJson(
                merge_dm,
                "gh pr view {} --json headRefOid".format(pull_request_id),
                repository,
            )["headRefOid"]

            # '--match-head-commit' ensures that the commit validated by the checks is the commit
            # that is merged.
            merge_result = RunRaw(
                merge_dm,
                "gh pr merge {} --merge --delete-branch --match-head-commit {}".format(
                    pull_request_id,
                    head_sha,
                ),
                repository,
            )

            if merge_result.returncode == 0:
                break

            if attempt == _MAX_MERGE_ATTEMPTS - 1:
                merge_result.RaiseOnError()

            merge_dm.WriteWarning(
                "The merge failed; the branch will be updated and the merge attempted again.\n{}".format(
                    merge_result.output,
                ),
            )

            Run(merge_dm, "gh pr update-branch {}".format(pull_request_id), repository)

            if wait_for_checks:
                _WaitForChecks(merge_dm, repository, pull_request_id, timeout_minutes=timeout_minutes)

        # ----------------------------------------------------------------------
        def Poll() -> dict[str, Any] | None:
            return RunJson(
                merge_dm,
                "gh pr view {} --json mergeCommit".format(pull_request_id),
                repository,
            )["mergeCommit"]

        # ----------------------------------------------------------------------

        merge_commit = _PollUntil(
            merge_dm,
            "The merge commit",
            Poll,
            timeout_minutes=timeout_minutes,
            interval_seconds=5,
        )["oid"]

        merge_dm.WriteLine("{}\n".format(merge_commit))

        return merge_commit


# ----------------------------------------------------------------------
def _WaitForWorkflowRuns(
    dm: DoneManager,
    repository: Path,
    commit: str,
    *,
    timeout_minutes: int,
) -> None:
    """Wait for the builds triggered by a commit to complete, raising when any of them fail."""

    with dm.Nested("Waiting for the mainline builds...") as runs_dm:
        # ----------------------------------------------------------------------
        def Poll() -> list[dict[str, Any]] | None:
            # The runs do not exist for the first few seconds after the merge.
            runs = RunJson(
                runs_dm,
                "gh run list --commit {} --json name,status,conclusion,url".format(commit),
                repository,
            )

            if not runs or any(run["status"] != "completed" for run in runs):
                return None

            return runs

        # ----------------------------------------------------------------------

        runs = _PollUntil(runs_dm, "The mainline builds", Poll, timeout_minutes=timeout_minutes)

        failures = [run for run in runs if run["conclusion"] not in ["success", "skipped"]]

        if failures:
            msg = "\n".join(
                ["The following mainline builds did not pass:", ""]
                + ["  - {} [{}] {}".format(run["name"], run["conclusion"], run["url"]) for run in failures]
                + [""],
            )
            raise Exception(msg)


# ----------------------------------------------------------------------
def _SyncLocalRepository(
    dm: DoneManager,
    repository: Path,
    default_branch: str,
    *,
    warnings: list[str],
) -> None:
    """Update the local repository so that it includes the merge commit and the release tag."""

    with dm.Nested("Updating the local repository...") as sync_dm:
        Run(sync_dm, "git checkout {}".format(default_branch), repository)
        Run(sync_dm, "git fetch origin --force --tags --prune", repository)

        # The merge has already succeeded, so an inability to fast-forward (because mainline has
        # advanced again) is not a failure of this script.
        result = RunRaw(sync_dm, "git pull --ff-only", repository)

        if result.returncode != 0:
            warnings.append(
                "The local repository could not be updated:\n{}".format(result.output.rstrip()),
            )


# ----------------------------------------------------------------------
def _PollUntil[PollResultT](
    dm: DoneManager,
    description: str,
    poll_func: Callable[[], PollResultT | None],
    *,
    timeout_minutes: int,
    interval_seconds: int = _POLL_INTERVAL_SECONDS,
) -> PollResultT:
    """Invoke `poll_func` until it returns a value or the timeout expires."""

    expiration_time = time.monotonic() + timeout_minutes * 60

    while True:
        poll_result = poll_func()

        if poll_result is not None:
            dm.ClearStatus()
            return poll_result

        remaining_seconds = expiration_time - time.monotonic()

        if remaining_seconds <= 0:
            msg = "{} did not complete within {} minute(s).".format(description, timeout_minutes)
            raise Exception(msg)

        dm.WriteStatus(
            "{} did not complete; {:.1f} minute(s) remaining.\n".format(
                description,
                remaining_seconds / 60,
            ),
        )

        time.sleep(min(interval_seconds, remaining_seconds))


# ----------------------------------------------------------------------
@contextmanager
def _TemporaryTextFile(content: str) -> Iterator[Path]:
    """Write content to a temporary file, ensuring that the file is removed."""

    filename = PathEx.CreateTempFileName()

    filename.write_text(content, encoding="utf-8")

    try:
        yield filename
    finally:
        filename.unlink()
