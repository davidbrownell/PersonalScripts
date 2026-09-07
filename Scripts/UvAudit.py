# noqa: INP001
"""Upgrade Python dependencies with security vulnerabilities and shepherd the change to mainline.

Runs `uv audit` against a repository, upgrades the packages with reported vulnerabilities, creates
a pull request, waits for its builds to pass, merges it, and waits for the mainline builds to
complete.
"""

import json
import os
import shutil
import textwrap
import threading
import time
import tomllib

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer

from dbrownell_Common import ExecuteTasks, PathEx, SubprocessEx, TextwrapEx
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags

from Impl.RepositoryUtils import FindRepositoryRoots


# ----------------------------------------------------------------------
# The emoji and category match the conventions enforced by dbrownell_CommitEmojis.
COMMIT_MESSAGE = "🔒️ [+security] Upgraded dependencies based on `uv audit`"


# ----------------------------------------------------------------------
_POLL_INTERVAL_SECONDS = 30

# `main` is typically configured to require that a branch be up to date before it can be merged,
# so a merge can legitimately fail once when mainline advances while the checks are running.
_MAX_MERGE_ATTEMPTS = 2

# `SubprocessEx.Run` merges stderr into stdout, so the update notification that `gh` writes to
# stderr would otherwise corrupt the JSON written by `gh ... --json`.
_ENVIRONMENT_OVERRIDES: dict[str, str] = {"GH_NO_UPDATE_NOTIFIER": "1"}


# ----------------------------------------------------------------------
app = typer.Typer(
    help=__doc__,
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    pretty_exceptions_enable=False,
)


# ----------------------------------------------------------------------
@app.command("Execute", no_args_is_help=True)
def Execute(
    repository: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Repository to audit.",
        ),
    ],
    branch_name: Annotated[
        str | None,
        typer.Option("--branch-name", help="Name of the branch created for the pull request."),
    ] = None,
    timeout_minutes: Annotated[
        int,
        typer.Option("--timeout-minutes", min=1, help="Maximum time to wait for each build step."),
    ] = 60,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--dry-run", help="Display the upgrades without committing them."),
    ] = False,
    no_merge: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--no-merge", help="Create the pull request and wait for its builds, but do not merge."),
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
    """Upgrade dependencies with security vulnerabilities and shepherd the change to mainline."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        result = _AuditRepository(
            dm,
            repository,
            branch_name=branch_name or _CreateBranchName(),
            timeout_minutes=timeout_minutes,
            # The activity is already visible in the output written to `dm`.
            on_activity_func=lambda _: None,
            dry_run=dry_run,
            no_merge=no_merge,
        )

        if result is not None:
            _DisplaySummary(dm, result)


# ----------------------------------------------------------------------
@app.command("ExecuteTree", no_args_is_help=True)
def ExecuteTree(
    root: Annotated[
        Path,
        typer.Argument(
            exists=True,
            file_okay=False,
            resolve_path=True,
            help="Root directory searched for repositories to audit.",
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
        typer.Option("--max-num-threads", min=1, help="Maximum number of repositories audited at once."),
    ] = None,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--dry-run", help="Display the upgrades without committing them."),
    ] = False,
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
    """Audit every uv-based git repository under a root directory in parallel."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        repositories: list[Path] = []

        with dm.Nested(
            "Searching for repositories...",
            lambda: "{} found".format(len(repositories)),
            suffix="\n",
        ):
            repositories = [
                repository
                for repository in FindRepositoryRoots(root)
                if all((repository / filename).is_file() for filename in ["pyproject.toml", "uv.lock"])
            ]

        if not repositories:
            return

        # Every repository shares a branch name so that the pull requests are easy to correlate.
        branch_name = branch_name or _CreateBranchName()

        results: dict[Path, _AuditResult] = {}
        results_lock = threading.Lock()

        # ----------------------------------------------------------------------
        def Init(context: Path) -> tuple[Path, ExecuteTasks.ExecuteTasksTypes.PrepareFuncType]:
            repository = context
            del context

            log_filename = PathEx.CreateTempFileName(".log")

            # ----------------------------------------------------------------------
            def Prepare(
                on_simple_status_func: Callable[[str], None],  # noqa: ARG001
            ) -> tuple[int, ExecuteTasks.ExecuteTasksTypes.ExecuteFuncType]:
                activities = list(_AuditActivity)

                # ----------------------------------------------------------------------
                def Audit(
                    status: ExecuteTasks.Status,
                ) -> tuple[int, str | None]:
                    # ----------------------------------------------------------------------
                    def OnActivity(activity: _AuditActivity) -> None:
                        # The step is derived from the activity rather than counted, as the
                        # activities that are skipped vary by repository and by option.
                        status.OnProgress(activities.index(activity), activity.value)

                    # ----------------------------------------------------------------------

                    # Each repository writes to its own log file, as the output of concurrent
                    # audits would otherwise be interleaved on the terminal.
                    with (
                        log_filename.open("w", encoding="utf-8") as f,
                        DoneManager.Create(
                            f,
                            "Auditing '{}'...".format(repository),
                            flags=dm.flags,
                        ) as repository_dm,
                    ):
                        result = _AuditRepository(
                            repository_dm,
                            repository,
                            branch_name=branch_name,
                            timeout_minutes=timeout_minutes,
                            on_activity_func=OnActivity,
                            dry_run=dry_run,
                            no_merge=no_merge,
                        )

                        if result is None:
                            return repository_dm.result, "no vulnerabilities"

                        with results_lock:
                            results[repository] = result

                        return repository_dm.result, "{} upgraded".format(len(result.upgrades))

                # ----------------------------------------------------------------------

                return len(activities), Audit

            # ----------------------------------------------------------------------

            return log_filename, Prepare

        # ----------------------------------------------------------------------

        ExecuteTasks.ExecuteTasks(
            dm,
            "Auditing",
            [
                ExecuteTasks.TaskData(
                    "Auditing '{}'".format(repository.relative_to(root)),
                    repository,
                )
                for repository in repositories
            ],
            Init,
            max_num_threads=max_num_threads,
        )

        for repository in repositories:
            result = results.get(repository)

            if result is not None:
                dm.WriteLine("\n{}".format(repository))
                _DisplaySummary(dm, result)


# ----------------------------------------------------------------------
# |
# |  Private Types
# |
# ----------------------------------------------------------------------
class _AuditActivity(Enum):
    """Activities performed by `_AuditRepository`, declared in the order that they are performed."""

    ValidatingRepository = "Validating the repository"
    AuditingDependencies = "Auditing the dependencies"
    UpgradingPackages = "Upgrading the packages"
    CommittingAndPushing = "Committing and pushing the upgrades"
    CreatingPullRequest = "Creating the pull request"
    WaitingForPullRequestChecks = "Waiting for the pull request checks"
    MergingPullRequest = "Merging the pull request"
    WaitingForMainlineBuilds = "Waiting for the mainline builds"
    UpdatingLocalRepository = "Updating the local repository"


# ----------------------------------------------------------------------
@dataclass
class _Vulnerability:
    """A single vulnerability reported by `uv audit`."""

    package_name: str
    package_version: str
    advisory_id: str
    fix_versions: list[str]
    description: str
    link: str


# ----------------------------------------------------------------------
@dataclass
class _PackageUpgrade:
    """A package upgraded to address one or more vulnerabilities."""

    name: str
    from_version: str
    to_version: str
    advisory_ids: list[str]


# ----------------------------------------------------------------------
@dataclass
class _RepositoryInfo:
    """Repository state needed to drive the pull request workflow."""

    # The branch (or commit, when the repository is in a detached HEAD state) that was checked out
    # before the audit began.
    original_branch: str

    default_branch: str
    has_workflows: bool


# ----------------------------------------------------------------------
@dataclass
class _AuditResult:
    """The outcome of auditing a single repository."""

    vulnerabilities: list[_Vulnerability]
    upgrades: list[_PackageUpgrade]

    pull_request_url: str | None = field(default=None)
    merge_commit: str | None = field(default=None)

    warnings: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# |
# |  Private Functions
# |
# ----------------------------------------------------------------------
def _AuditRepository(
    dm: DoneManager,
    repository: Path,
    *,
    branch_name: str,
    timeout_minutes: int,
    on_activity_func: Callable[[_AuditActivity], None],
    dry_run: bool = False,
    no_merge: bool = False,
) -> _AuditResult | None:
    """Upgrade vulnerable packages within a repository and shepherd the change to mainline.

    `on_activity_func` is invoked with the current activity, which is useful when the output written
    to `dm` is not visible.

    Returns None when no vulnerabilities were found.
    """

    on_activity_func(_AuditActivity.ValidatingRepository)
    repository_info = _Preflight(dm, repository)

    with _RestoreBranch(dm, repository, repository_info.original_branch):
        on_activity_func(_AuditActivity.AuditingDependencies)

        with dm.Nested("Auditing the dependencies...") as audit_dm:
            vulnerabilities = _RunAudit(audit_dm, repository, frozen=True)

            if not vulnerabilities:
                audit_dm.WriteInfo("No vulnerabilities were found.\n")
                return None

            audit_dm.WriteLine("{} vulnerabilities were found.\n".format(len(vulnerabilities)))

        on_activity_func(_AuditActivity.UpgradingPackages)

        result = _AuditResult(
            vulnerabilities=vulnerabilities,
            upgrades=_Upgrade(dm, repository, vulnerabilities, restore=dry_run),
        )

        if dry_run:
            result.warnings.append("'--dry-run' was specified; 'uv.lock' was restored.")
            return result

        on_activity_func(_AuditActivity.CommittingAndPushing)
        _CommitAndPush(dm, repository, branch_name)

        on_activity_func(_AuditActivity.CreatingPullRequest)
        result.pull_request_url = _CreatePullRequest(dm, repository, repository_info.default_branch, result)
        pull_request_id = result.pull_request_url.rsplit("/", 1)[-1]

        if repository_info.has_workflows:
            on_activity_func(_AuditActivity.WaitingForPullRequestChecks)
            _WaitForChecks(dm, repository, pull_request_id, timeout_minutes=timeout_minutes)

        if no_merge:
            result.warnings.append("'--no-merge' was specified; the pull request was not merged.")
            return result

        on_activity_func(_AuditActivity.MergingPullRequest)

        result.merge_commit = _Merge(
            dm,
            repository,
            pull_request_id,
            timeout_minutes=timeout_minutes,
            wait_for_checks=repository_info.has_workflows,
        )

        if repository_info.has_workflows:
            on_activity_func(_AuditActivity.WaitingForMainlineBuilds)
            _WaitForWorkflowRuns(dm, repository, result.merge_commit, timeout_minutes=timeout_minutes)

        on_activity_func(_AuditActivity.UpdatingLocalRepository)
        _SyncLocalRepository(dm, repository, repository_info.default_branch, warnings=result.warnings)

        return result


# ----------------------------------------------------------------------
def _CreateBranchName() -> str:
    """Create a branch name that is unique to this invocation."""

    return "uv-audit-{}".format(datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))


# ----------------------------------------------------------------------
def _Preflight(dm: DoneManager, repository: Path) -> _RepositoryInfo:
    """Validate the environment and repository state and return information about the repository."""

    with dm.Nested("Validating the repository...") as preflight_dm:
        for tool_name in ["git", "gh", "uv"]:
            if shutil.which(tool_name) is None:
                msg = "'{}' was not found on the path.".format(tool_name)
                raise Exception(msg)

        for filename in ["pyproject.toml", "uv.lock"]:
            PathEx.EnsureFile(repository / filename)

        # Unrelated local changes would otherwise be swept into the commit created below.
        if _Run(preflight_dm, "git status --porcelain", repository).strip():
            msg = "'{}' has uncommitted changes.".format(repository)
            raise Exception(msg)

        # `git symbolic-ref` fails when the repository is in a detached HEAD state, which is
        # restored by commit rather than by name.
        symbolic_ref_result = _RunRaw(preflight_dm, "git symbolic-ref --quiet --short HEAD", repository)

        original_branch = (
            symbolic_ref_result.output.strip()
            if symbolic_ref_result.returncode == 0
            else _Run(preflight_dm, "git rev-parse HEAD", repository).strip()
        )

        default_branch = _RunJson(preflight_dm, "gh repo view --json defaultBranchRef", repository)[
            "defaultBranchRef"
        ]["name"]

        _Run(preflight_dm, "git fetch origin --force --tags --prune", repository)
        _Run(preflight_dm, "git checkout {}".format(default_branch), repository)
        _Run(preflight_dm, "git pull --ff-only", repository)

        workflows_path = repository / ".github" / "workflows"

        return _RepositoryInfo(
            original_branch=original_branch,
            default_branch=default_branch,
            has_workflows=workflows_path.is_dir() and any(workflows_path.glob("*.y*ml")),
        )


# ----------------------------------------------------------------------
def _RunAudit(dm: DoneManager, repository: Path, *, frozen: bool) -> list[_Vulnerability]:
    """Run `uv audit` and return the vulnerabilities that it reports."""

    # '--preview-features' suppresses the experimental warnings that would otherwise be
    # interleaved with the JSON output.
    command_line = "uv audit --output-format json --preview-features audit-command,json-output"

    if frozen:
        command_line += " --frozen"

    # `uv audit` exits with an error code when it finds vulnerabilities, so the output is the only
    # reliable indication of success.
    result = _RunRaw(dm, command_line, repository)

    try:
        content = _ExtractJson(result.output)
    except ValueError:
        result.RaiseOnError()
        raise

    return [
        _Vulnerability(
            package_name=vulnerability["dependency"]["name"],
            package_version=vulnerability["dependency"]["version"],
            advisory_id=vulnerability["display_id"],
            fix_versions=vulnerability.get("fix_versions") or [],
            description=vulnerability.get("summary")
            or vulnerability.get("description")
            or "<no description>",
            link=vulnerability.get("link") or "",
        )
        for vulnerability in content["vulnerabilities"]
    ]


# ----------------------------------------------------------------------
def _Upgrade(
    dm: DoneManager,
    repository: Path,
    vulnerabilities: list[_Vulnerability],
    *,
    restore: bool,
) -> list[_PackageUpgrade]:
    """Upgrade the vulnerable packages in `uv.lock`, leaving `pyproject.toml` untouched."""

    original_versions: dict[str, str] = {}

    for vulnerability in vulnerabilities:
        original_versions.setdefault(vulnerability.package_name, vulnerability.package_version)

    with dm.Nested(
        "Upgrading {}...".format(", ".join("'{}'".format(name) for name in original_versions)),
    ) as upgrade_dm:
        _Run(
            upgrade_dm,
            "uv lock {}".format(
                " ".join("--upgrade-package {}".format(name) for name in original_versions),
            ),
            repository,
        )

        if not _Run(upgrade_dm, "git status --porcelain -- uv.lock", repository).strip():
            msg = _CreateVulnerabilityMessage(
                "'uv.lock' was not changed, which means that no fix has been published or that a "
                "'pyproject.toml' constraint is preventing the fix for:",
                vulnerabilities,
            )
            raise Exception(msg)

        remaining = [
            vulnerability
            for vulnerability in _RunAudit(upgrade_dm, repository, frozen=False)
            if vulnerability.package_name in original_versions
        ]

        if remaining:
            _Run(upgrade_dm, "git checkout -- uv.lock", repository)

            msg = _CreateVulnerabilityMessage(
                "The following vulnerabilities remain after the upgrade, which means that a "
                "'pyproject.toml' constraint is preventing the fix ('uv.lock' has been restored):",
                remaining,
            )
            raise Exception(msg)

        locked_versions = _GetLockedVersions(repository / "uv.lock", original_versions.keys())

        if restore:
            _Run(upgrade_dm, "git checkout -- uv.lock", repository)

        return [
            _PackageUpgrade(
                name=name,
                from_version=version,
                to_version=locked_versions.get(name.lower(), "<removed>"),
                advisory_ids=[
                    vulnerability.advisory_id
                    for vulnerability in vulnerabilities
                    if vulnerability.package_name == name
                ],
            )
            for name, version in original_versions.items()
        ]


# ----------------------------------------------------------------------
def _CommitAndPush(dm: DoneManager, repository: Path, branch_name: str) -> None:
    """Commit the updated `uv.lock` on a new branch and push it."""

    with dm.Nested("Committing and pushing '{}'...".format(branch_name)) as commit_dm:
        _Run(commit_dm, "git checkout -b {}".format(branch_name), repository)
        _Run(commit_dm, "git add uv.lock", repository)

        # The commit message contains backticks, which a POSIX shell would interpret as command
        # substitution; a file keeps the message byte-exact on every platform.
        with _TemporaryTextFile(COMMIT_MESSAGE + "\n") as message_filename:
            _Run(commit_dm, 'git commit --file "{}"'.format(message_filename), repository)

        _Run(commit_dm, "git push --set-upstream origin {}".format(branch_name), repository)


# ----------------------------------------------------------------------
def _CreatePullRequest(
    dm: DoneManager,
    repository: Path,
    default_branch: str,
    result: _AuditResult,
) -> str:
    """Create the pull request and return its url."""

    with dm.Nested("Creating the pull request...") as pr_dm:
        # '--fill' takes the title from the commit, which avoids passing the backticks in the
        # commit message through a shell.
        with _TemporaryTextFile(_CreatePullRequestBody(result)) as body_filename:
            output = _Run(
                pr_dm,
                'gh pr create --base {} --fill --body-file "{}"'.format(default_branch, body_filename),
                repository,
            )

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
            result = _RunRaw(
                checks_dm,
                "gh pr checks {} --json name,bucket,link".format(pull_request_id),
                repository,
            )

            try:
                checks = _ExtractJson(result.output)
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
            head_sha = _RunJson(
                merge_dm,
                "gh pr view {} --json headRefOid".format(pull_request_id),
                repository,
            )["headRefOid"]

            # '--match-head-commit' ensures that the commit validated by the checks is the commit
            # that is merged.
            merge_result = _RunRaw(
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

            _Run(merge_dm, "gh pr update-branch {}".format(pull_request_id), repository)

            if wait_for_checks:
                _WaitForChecks(merge_dm, repository, pull_request_id, timeout_minutes=timeout_minutes)

        # ----------------------------------------------------------------------
        def Poll() -> dict[str, Any] | None:
            return _RunJson(
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
            runs = _RunJson(
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
        _Run(sync_dm, "git checkout {}".format(default_branch), repository)
        _Run(sync_dm, "git fetch origin --tags --prune", repository)

        # The release has already succeeded, so an inability to fast-forward (because mainline has
        # advanced again) is not a failure of this script.
        result = _RunRaw(sync_dm, "git pull --ff-only", repository)

        if result.returncode != 0:
            warnings.append(
                "The local repository could not be updated:\n{}".format(result.output.rstrip()),
            )


# ----------------------------------------------------------------------
def _GetLockedVersions(lock_filename: Path, package_names: Iterable[str]) -> dict[str, str]:
    """Extract the locked version of each of the provided packages."""

    with lock_filename.open("rb") as f:
        content = tomllib.load(f)

    lowercase_names = {name.lower() for name in package_names}

    return {
        package["name"].lower(): package["version"]
        for package in content.get("package", [])
        if package["name"].lower() in lowercase_names and "version" in package
    }


# ----------------------------------------------------------------------
def _CreateVulnerabilityMessage(heading: str, vulnerabilities: list[_Vulnerability]) -> str:
    """Create an error message that describes each of the provided vulnerabilities."""

    return "\n".join(
        [heading, ""]
        + [
            "  - {} {} ({}); fixed in {}".format(
                vulnerability.package_name,
                vulnerability.package_version,
                vulnerability.advisory_id,
                ", ".join(vulnerability.fix_versions) or "<no fix available>",
            )
            for vulnerability in vulnerabilities
        ]
        + [""],
    )


# ----------------------------------------------------------------------
def _CreateUpgradeTable(upgrades: list[_PackageUpgrade]) -> str:
    """Create a table that describes the packages that were upgraded."""

    return TextwrapEx.CreateTable(
        ["Package", "From", "To", "Advisories"],
        [
            [
                upgrade.name,
                upgrade.from_version,
                upgrade.to_version,
                ", ".join(upgrade.advisory_ids),
            ]
            for upgrade in upgrades
        ],
    )


# ----------------------------------------------------------------------
def _CreatePullRequestBody(result: _AuditResult) -> str:
    """Create markdown that describes the upgrades and the advisories that prompted them."""

    return textwrap.dedent(
        """\
        `uv audit` reported security vulnerabilities in the following packages; `uv.lock` has been
        updated to upgrade them.

        ```
        {}
        ```

        ## Advisories

        {}
        """,
    ).format(
        _CreateUpgradeTable(result.upgrades).rstrip(),
        "\n".join(
            "- **{}** ({} {}, fixed in {}): {} [details]({})".format(
                vulnerability.advisory_id,
                vulnerability.package_name,
                vulnerability.package_version,
                ", ".join(vulnerability.fix_versions) or "<no fix available>",
                vulnerability.description,
                vulnerability.link,
            )
            for vulnerability in result.vulnerabilities
        ),
    )


# ----------------------------------------------------------------------
def _DisplaySummary(dm: DoneManager, result: _AuditResult) -> None:
    """Display the outcome of the audit."""

    dm.WriteLine("\n" + _CreateUpgradeTable(result.upgrades) + "\n\n")

    for description, value in [
        ("Pull Request", result.pull_request_url),
        ("Merge Commit", result.merge_commit),
    ]:
        if value is not None:
            dm.WriteLine("{}: {}\n".format(description, value))

    for warning in result.warnings:
        dm.WriteWarning(warning + "\n")


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
def _RestoreBranch(dm: DoneManager, repository: Path, branch: str) -> Iterator[None]:
    """Restore the branch that was checked out before the audit began.

    `_Preflight` checks out the default branch, so the repository would otherwise be left on a
    different branch than the one that the caller was using.
    """

    try:
        yield
    finally:
        result = _RunRaw(dm, "git checkout {}".format(branch), repository)

        if result.returncode != 0:
            # Restoring the branch is a convenience, so a failure here must not mask the outcome of
            # the audit itself.
            dm.WriteWarning(
                "'{}' could not be restored:\n{}\n".format(branch, result.output.rstrip()),
            )


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


# ----------------------------------------------------------------------
def _RunRaw(dm: DoneManager, command_line: str, repository: Path) -> SubprocessEx.RunResult:
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
def _Run(dm: DoneManager, command_line: str, repository: Path) -> str:
    """Run a command within the repository, raising on error."""

    result = _RunRaw(dm, command_line, repository)
    result.RaiseOnError()

    return result.output


# ----------------------------------------------------------------------
def _RunJson(dm: DoneManager, command_line: str, repository: Path) -> Any:  # noqa: ANN401
    """Run a command within the repository and parse its output as JSON."""

    return _ExtractJson(_Run(dm, command_line, repository))


# ----------------------------------------------------------------------
def _ExtractJson(output: str) -> Any:  # noqa: ANN401
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
if __name__ == "__main__":
    app()  # pragma: no cover
