import os
import threading

from collections.abc import Callable, Generator, Sequence
from enum import Enum
from pathlib import Path

from dbrownell_Common import ExecuteTasks, PathEx
from dbrownell_Common.Streams.DoneManager import DoneManager


# ----------------------------------------------------------------------
def FindRepositoryRoots(directory: Path) -> Generator[Path]:
    """Find all git repository roots under the given directory."""

    for root, dirs, _files in os.walk(directory, followlinks=False):
        if ".git" in dirs:
            yield Path(root)

            # Don't descend into this repository
            dirs.clear()


# ----------------------------------------------------------------------
def ExecuteInParallel[ResultT](
    dm: DoneManager,
    heading: str,
    repositories: Sequence[Path],
    activities: Sequence[Enum],
    execute_func: Callable[
        [DoneManager, Path, Callable[[Enum], None]],
        tuple[ResultT | None, str | None],
    ],
    *,
    display_name_func: Callable[[Path], str] = str,
    max_num_threads: int | None = None,
) -> dict[Path, ResultT]:
    """Invoke a function for each repository in parallel and return the results by repository.

    `execute_func` is invoked with a repository and a function that it invokes with the activity
    that it is about to perform; it returns the result (which is omitted from the return value when
    it is None) and the text displayed once it completes.

    `activities` are all of the activities that `execute_func` may perform, declared in the order
    that they are performed.
    """

    results: dict[Path, ResultT] = {}
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
            # ----------------------------------------------------------------------
            def Execute(
                status: ExecuteTasks.Status,
            ) -> tuple[int, str | None]:
                # ----------------------------------------------------------------------
                def OnActivity(activity: Enum) -> None:
                    # The step is derived from the activity rather than counted, as the activities
                    # that are skipped vary by repository and by option.
                    status.OnProgress(activities.index(activity), activity.value)

                # ----------------------------------------------------------------------

                # Each repository writes to its own log file, as the output of concurrent
                # invocations would otherwise be interleaved on the terminal.
                with (
                    log_filename.open("w", encoding="utf-8") as f,
                    DoneManager.Create(
                        f,
                        "{} '{}'...".format(heading, repository),
                        flags=dm.flags,
                    ) as repository_dm,
                ):
                    result, status_text = execute_func(repository_dm, repository, OnActivity)

                    if result is not None:
                        with results_lock:
                            results[repository] = result

                    return repository_dm.result, status_text

            # ----------------------------------------------------------------------

            return len(activities), Execute

        # ----------------------------------------------------------------------

        return log_filename, Prepare

    # ----------------------------------------------------------------------

    ExecuteTasks.ExecuteTasks(
        dm,
        heading,
        [
            ExecuteTasks.TaskData(
                "{} '{}'".format(heading, display_name_func(repository)),
                repository,
            )
            for repository in repositories
        ],
        Init,
        max_num_threads=max_num_threads,
    )

    return results
