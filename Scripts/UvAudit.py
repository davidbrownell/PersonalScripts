# noqa: INP001
"""Upgrade Python dependencies with security vulnerabilities and shepherd the change to mainline.

Runs `uv audit` against a repository, upgrades the packages with reported vulnerabilities, creates
a pull request, waits for its builds to pass, merges it, and waits for the mainline builds to
complete.
"""

import textwrap
import tomllib

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from dbrownell_Common import PathEx, TextwrapEx
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags

from Impl import PullRequestWorkflow
from Impl.RepositoryUtils import ExecuteInParallel, FindRepositoryRoots


# ----------------------------------------------------------------------
# The emoji and category match the conventions enforced by dbrownell_CommitEmojis.
COMMIT_MESSAGE = "🔒️ [+security] Upgraded dependencies based on `uv audit`"


# ----------------------------------------------------------------------
_BRANCH_NAME_PREFIX = "uv-audit"


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
            branch_name=branch_name or PullRequestWorkflow.CreateBranchName(_BRANCH_NAME_PREFIX),
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
        branch_name = branch_name or PullRequestWorkflow.CreateBranchName(_BRANCH_NAME_PREFIX)

        # ----------------------------------------------------------------------
        def Audit(
            repository_dm: DoneManager,
            repository: Path,
            on_activity_func: Callable[[Enum], None],
        ) -> tuple["_AuditResult | None", str | None]:
            result = _AuditRepository(
                repository_dm,
                repository,
                branch_name=branch_name,
                timeout_minutes=timeout_minutes,
                on_activity_func=on_activity_func,
                dry_run=dry_run,
                no_merge=no_merge,
            )

            if result is None:
                return None, "no vulnerabilities"

            return result, "{} upgraded".format(len(result.upgrades))

        # ----------------------------------------------------------------------

        results = ExecuteInParallel(
            dm,
            "Auditing",
            repositories,
            _ACTIVITIES,
            Audit,
            display_name_func=lambda repository: str(repository.relative_to(root)),
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
    """Activities performed by `_AuditRepository` before the changes are shepherded to mainline."""

    ValidatingRepository = "Validating the repository"
    AuditingDependencies = "Auditing the dependencies"
    UpgradingPackages = "Upgrading the packages"


# ----------------------------------------------------------------------
_ACTIVITIES: list[Enum] = [*_AuditActivity, *PullRequestWorkflow.Activity]


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
class _AuditResult:
    """The outcome of auditing a single repository."""

    vulnerabilities: list[_Vulnerability]
    upgrades: list[_PackageUpgrade]

    workflow_result: PullRequestWorkflow.WorkflowResult | None = field(default=None)

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
    on_activity_func: Callable[[_AuditActivity | PullRequestWorkflow.Activity], None],
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

    with PullRequestWorkflow.RestoreBranch(dm, repository, repository_info.original_branch):
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

        result.workflow_result = PullRequestWorkflow.ShepherdChanges(
            dm,
            repository,
            repository_info,
            branch_name=branch_name,
            commit_message=COMMIT_MESSAGE,
            pull_request_body=_CreatePullRequestBody(result),
            filenames=["uv.lock"],
            timeout_minutes=timeout_minutes,
            on_activity_func=on_activity_func,
            no_merge=no_merge,
        )

        return result


# ----------------------------------------------------------------------
def _Preflight(dm: DoneManager, repository: Path) -> PullRequestWorkflow.RepositoryInfo:
    """Validate the environment and repository state and return information about the repository."""

    with dm.Nested("Validating the repository...") as preflight_dm:
        PullRequestWorkflow.EnsureTools(["git", "gh", "uv"])

        for filename in ["pyproject.toml", "uv.lock"]:
            PathEx.EnsureFile(repository / filename)

        # Unrelated local changes would otherwise be swept into the commit created below.
        if PullRequestWorkflow.HasChanges(preflight_dm, repository):
            msg = "'{}' has uncommitted changes.".format(repository)
            raise Exception(msg)

        repository_info = PullRequestWorkflow.GetRepositoryInfo(preflight_dm, repository)

        PullRequestWorkflow.Run(preflight_dm, "git fetch origin --force --tags --prune", repository)

        PullRequestWorkflow.Run(
            preflight_dm,
            "git checkout {}".format(repository_info.default_branch),
            repository,
        )

        PullRequestWorkflow.Run(preflight_dm, "git pull --ff-only", repository)

        return repository_info


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
    result = PullRequestWorkflow.RunRaw(dm, command_line, repository)

    try:
        content = PullRequestWorkflow.ExtractJson(result.output)
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
        PullRequestWorkflow.Run(
            upgrade_dm,
            "uv lock {}".format(
                " ".join("--upgrade-package {}".format(name) for name in original_versions),
            ),
            repository,
        )

        if not PullRequestWorkflow.HasChanges(upgrade_dm, repository, "uv.lock"):
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
            PullRequestWorkflow.Run(upgrade_dm, "git checkout -- uv.lock", repository)

            msg = _CreateVulnerabilityMessage(
                "The following vulnerabilities remain after the upgrade, which means that a "
                "'pyproject.toml' constraint is preventing the fix ('uv.lock' has been restored):",
                remaining,
            )
            raise Exception(msg)

        locked_versions = _GetLockedVersions(repository / "uv.lock", original_versions.keys())

        if restore:
            PullRequestWorkflow.Run(upgrade_dm, "git checkout -- uv.lock", repository)

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

    if result.workflow_result is not None:
        PullRequestWorkflow.DisplayResult(dm, result.workflow_result)

    for warning in result.warnings:
        dm.WriteWarning(warning + "\n")


# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()  # pragma: no cover
