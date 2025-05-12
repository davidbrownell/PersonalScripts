# ----------------------------------------------------------------------
# |
# |  BackupOneDrive.py
# |
# |  David Brownell <db@DavidBrownell.com>
# |      2024-08-05 07:59:44
# |
# ----------------------------------------------------------------------
# |
# |  Copyright David Brownell 2024
# |  Distributed under the MIT License.
# |
# ----------------------------------------------------------------------
"""Copies content from Microsoft OneDrive to a local directory."""

import base64
import datetime
import hashlib
import os
import shutil
import textwrap
import threading
import webbrowser

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Optional
from urllib.parse import urlparse, parse_qs

import requests
import typer

from dbrownell_Common import ExecuteTasks
from dbrownell_Common.InflectEx import inflect
from dbrownell_Common import PathEx
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from requests_oauthlib import OAuth2Session
from typer.core import TyperGroup

from Impl.CallbackServer import CallbackServer


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
@app.command("Backup", help=__doc__, no_args_is_help=True)
def Backup(
    name: Annotated[
        str,
        typer.Argument(help="Output subdirectory name for multi-user support."),
    ],
    expected_username: Annotated[
        str,
        typer.Argument(
            help="The expected username; produce an error if the actual value is not a match to this value."
        ),
    ],
    output_dir: Annotated[
        Path,
        typer.Argument(help="Output directory.", file_okay=False, resolve_path=True),
    ],
    expected_email: Annotated[
        Optional[str],
        typer.Option(
            "--expected-email",
            help="The expected email address; produce an error if the actual value is not a match to this value.",
        ),
    ] = None,
    local_pictures_subdir: Annotated[
        Optional[str],
        typer.Option("--local-pictures-subdir", help="Output subdir for pictures."),
    ] = "My Pictures",
    local_videos_subdir: Annotated[
        Optional[str],
        typer.Option("--local-videos-subdir", help="Output subdir for videos."),
    ] = "My Videos",
    output_dir_template: Annotated[
        str,
        typer.Option(
            "--output-dir-template",
            help="Template to use when creating output directories for content.",
        ),
    ] = f"{{year}}{os.path.sep}{{month:02d}}{os.path.sep}{{year}}.{{month:02d}}.{{day:02d}} - {{name}}",
    force_oauth: Annotated[
        bool,
        typer.Option("--force-oauth", help="Always execute the oauth workflow."),
    ] = False,
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
        # Get the required environment variables
        redirect_url: str | None = None
        client_id: str | None = None
        client_secret_str: str | None = None
        ssl_pem_filename_str: str | None = None

        errors: list[str] = []

        redirect_url = os.getenv("DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_REDIRECT_URI")
        if redirect_url is None:
            errors.append("DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_REDIRECT_URI")

        client_id = os.getenv("DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_CLIENT_ID")
        if client_id is None:
            errors.append("DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_CLIENT_ID")

        client_secret_str = os.getenv(
            "DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_CLIENT_SECRET"
        )
        if client_secret_str is None:
            errors.append("DEVELOPMENT_ENVIRONMENT_UTILITIES_MICROSOFT_LIVE_CONNECT_CLIENT_SECRET")

        ssl_pem_filename_str = os.getenv("DEVELOPMENT_ENVIRONMENT_UTILITIES_SSL_PEM_FILENAME")
        if ssl_pem_filename_str is None:
            errors.append("DEVELOPMENT_ENVIRONMENT_UTILITIES_SSL_PEM_FILENAME")

        if errors:
            dm.WriteError(
                textwrap.dedent(
                    """\
                    The following environment variable(s) were not defined:

                    {}
                    """,
                ).format("\n".join(f"    - {var}" for var in errors)),
            )
            return

        assert redirect_url is not None
        assert client_id is not None
        assert client_secret_str is not None
        assert ssl_pem_filename_str is not None

        # Convert the client secret
        if client_secret_str.endswith("="):
            client_secret = base64.b64decode(client_secret_str)
        else:
            client_secret = client_secret_str.encode("utf-8")

        del client_secret_str

        # Convert the SSL PEM filename
        ssl_pem_filename = Path(ssl_pem_filename_str)
        del ssl_pem_filename_str

        if not ssl_pem_filename.is_file():
            dm.WriteError(f"'{ssl_pem_filename}' is not a recognized file name.\n")
            return

        # Get the access token
        with dm.Nested("Accessing OneDrive..."):
            parts = redirect_url.split(":")

            if len(parts) == 1:
                callback_port = 80
            else:
                callback_port = int(parts[-1])

            token = _Token.Create(
                name,
                redirect_url,
                client_id,
                client_secret,
                callback_port,
                ssl_pem_filename,
                force_oauth=force_oauth,
            )

            token.GetAccessToken()

        # ----------------------------------------------------------------------
        def GetHeaders() -> dict[str, str]:
            return {
                "Authorization": f"Bearer {token.GetAccessToken()}",
            }

        # ----------------------------------------------------------------------

        # Verify the username
        with dm.Nested("Verifying profile...") as verify_dm:
            response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers=GetHeaders(),
            )

            response.raise_for_status()
            response = response.json()

            if response["displayName"] != expected_username:
                verify_dm.WriteError(
                    f"The display name '{response['displayName']}' does not match the expected username '{expected_username}'.",
                )
                return

            if expected_email is not None and response["mail"] != expected_email:
                verify_dm.WriteError(
                    f"The email address '{response['mail']}' does not match the expected email address '{expected_email}'.",
                )
                return

        # Get the content
        file_infos = _GetFileInfos(dm, GetHeaders)

        if dm.result != 0:
            return

        files_to_process = _GetFilesToProcess(
            dm,
            file_infos,
            name,
            output_dir,
            local_pictures_subdir,
            local_videos_subdir,
            output_dir_template,
        )

        if dm.result != 0:
            return

        if not files_to_process:
            return

        _ProcessFiles(dm, files_to_process, GetHeaders)
        if dm.result != 0:
            return


# ----------------------------------------------------------------------
@app.command("RemoveDuplicates", no_args_is_help=True)
def RemoveDuplicates(
    output_dir: Annotated[
        Path,
        typer.Argument(
            help="Backup output directory.",
            exists=True,
            file_okay=False,
            resolve_path=True,
        ),
    ],
    ssd: Annotated[
        bool,
        typer.Option("--ssd", help="Add this flag to increase performance on SSDs."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            help="Do not remove any files, only display what would be removed.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    """OneDrive seems to have a bad habit of recreating files under different folders. This function will remove those duplicate files by calculating the hash values of each file and removing duplicates encountered."""

    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        all_filenames: list[Path] = []

        with dm.Nested(
            "Counting files...",
            lambda: "{} found".format(inflect.no("file", len(all_filenames))),
        ):
            for root_str, _, filenames in os.walk(output_dir):
                root = Path(root_str)

                all_filenames += [root / filename for filename in filenames]

        # Calculate the hash values
        # ----------------------------------------------------------------------
        def PrepareTask(
            context: Any,
            on_simple_status_func: Callable[[str], None],
        ) -> tuple[int, ExecuteTasks.TransformTasksExTypes.TransformFuncType]:
            index = context
            del context

            filename = all_filenames[index]

            # ----------------------------------------------------------------------
            def ExecuteTask(
                status: ExecuteTasks.Status,
            ) -> str:
                hasher = hashlib.sha512()
                hashed = 0

                with filename.open("rb") as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break

                        hasher.update(chunk)

                        hashed += len(chunk)
                        status.OnProgress(hashed, None)

                return hasher.hexdigest()

            # ----------------------------------------------------------------------

            return filename.stat().st_size or 1, ExecuteTask

        # ----------------------------------------------------------------------

        all_hashes = ExecuteTasks.TransformTasksEx(
            dm,
            "Calculating hash values...",
            [ExecuteTasks.TaskData(str(filename), index) for index, filename in enumerate(all_filenames)],
            PrepareTask,
            max_num_threads=None if ssd else 1,
        )

        # Organize the hashes
        duplicates: list[list[Path]] = []

        with dm.Nested(
            "Organizing hash values...",
            lambda: "{} found".format(inflect.no("duplicate", len(duplicates))),
        ):
            hash_map: dict[str, list[Path]] = {}

            for filename, hash_value in zip(all_filenames, all_hashes):
                assert isinstance(hash_value, str), hash_value
                hash_map.setdefault(hash_value, []).append(filename)

            for dup_filenames in hash_map.values():
                if len(dup_filenames) > 1:
                    duplicates.append(dup_filenames)

        if not duplicates:
            return

        with dm.Nested("Removing duplicates...") as duplicate_dm:
            for index, dup_filenames in enumerate(duplicates):
                with duplicate_dm.Nested(
                    f"Removing duplicates of '{dup_filenames[0]}' ({index + 1} of {len(duplicates)})...",
                    suffix="\n",
                ) as this_dm:
                    for index, filename in enumerate(dup_filenames[1:]):
                        with this_dm.Nested(
                            f"Removing '{filename}' ({index + 1} of {len(dup_filenames) - 1})..."
                        ):
                            if not dry_run:
                                filename.unlink()

        num_removed = 0

        with dm.Nested(
            "Removing empty directories...",
            lambda: "{} removed".format(inflect.no("directory", num_removed)),
        ):
            directories: list[Path] = []

            for root_str, _, _ in os.walk(output_dir):
                directories.append(Path(root_str))

            for directory in reversed(directories):
                if not any(directory.iterdir()):
                    num_removed += 1

                    if not dry_run:
                        directory.rmdir()


# ----------------------------------------------------------------------
# |
# |  Private Types
# |
# ----------------------------------------------------------------------
class _Token:
    AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
    TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"

    # ----------------------------------------------------------------------
    @classmethod
    def Create(
        cls,
        expected_username: str,
        redirect_url: str,
        client_id: str,
        client_secret: bytes,
        callback_port: int,
        ssl_pem_filename: Path,
        *,
        force_oauth: bool,
    ) -> "_Token":
        os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"

        oauth = OAuth2Session(
            client_id,
            redirect_uri=redirect_url,
            scope=[
                "User.Read",
                "offline_access",
                "Files.ReadWrite.All",
            ],
        )

        data_filename = PathEx.GetUserDirectory() / f"{expected_username} - BackupOneDrive"

        if data_filename.is_file() and not force_oauth:
            refresh_token = data_filename.read_text(encoding="utf-8").strip()
        else:
            # Get the auth token
            local_server = CallbackServer(
                "BackupOneDrive",
                "code",
                callback_port=callback_port,
                ssl_pem_filename=ssl_pem_filename,
            )

            auth_url, state = oauth.authorization_url(
                cls.AUTHORIZE_URL,
                access_token="offline",
                prompt="select_account",
            )

            webbrowser.open_new_tab(auth_url)
            auth_token = local_server.Wait()

            # Get the refresh token
            response = oauth.fetch_token(
                cls.TOKEN_URL,
                code=auth_token,
                authorization_response=auth_url,
                client_secret=client_secret,
            )

            refresh_token = response["refresh_token"]
            assert refresh_token is not None, response

            with data_filename.open("w", encoding="utf-8") as f:
                f.write(refresh_token)

        return cls(oauth, refresh_token, client_id, client_secret, redirect_url)

    # ----------------------------------------------------------------------
    def __init__(
        self,
        oauth: OAuth2Session,
        refresh_token: str,
        client_id: str,
        client_secret: bytes,
        redirect_url: str,
    ) -> None:
        self.refresh_token = refresh_token

        self._oauth = oauth
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_url = redirect_url

        self._access_token: str | None = None
        self._access_expires: datetime.datetime | None = None

    # ----------------------------------------------------------------------
    def GetAccessToken(self) -> str:
        if self._access_expires is None or datetime.datetime.now(datetime.UTC) >= self._access_expires:
            response = self._oauth.refresh_token(
                self.__class__.TOKEN_URL,
                refresh_token=self.refresh_token,
                client_id=self._client_id,
                client_secret=self._client_secret,
                redirect_uri=self._redirect_url,
            )

            if "refresh_token" in response:
                self.refresh_token = response["refresh_token"]

            access_token = response["access_token"]
            expires_in = response["expires_in"]

            # expires_in is measured in seconds - store a value that is slightly less than
            # the original value to decrease the likelihood that we will accidentally use an
            # expired token.
            expires_in = max(5, int(float(expires_in) * 0.90))
            expires_in = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=expires_in)

            self._access_token = access_token
            self._access_expires = expires_in

        assert self._access_token is not None
        return self._access_token


# ----------------------------------------------------------------------
# |
# |  Private Functions
# |
# ----------------------------------------------------------------------
def _GetFileInfos(
    dm: DoneManager,
    get_headers_func: Callable[[], dict[str, str]],
) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []

    with dm.Nested(
        "Getting file metadata...",
        lambda: inflect.no("file", len(files)),
    ) as files_dm:
        to_search: list[tuple[str, str]] = [
            (
                "Camera Roll",
                "https://graph.microsoft.com/v1.0/me/drive/special/cameraroll/children",
            ),
        ]

        while to_search:
            display_name, url = to_search.pop()

            num_files = 0

            with files_dm.Nested(
                f"Searching in '{display_name}'...",
                lambda: inflect.no("file", num_files),
            ) as search_dm:
                response = requests.get(url, headers=get_headers_func())

                response.raise_for_status()
                response = response.json()

                for item in response["value"]:
                    if "folder" in item:
                        to_search.append(
                            (
                                f"{display_name}/{item['name']}",
                                f"https://graph.microsoft.com/v1.0/me/drive/items/{item['id']}/children",
                            ),
                        )
                    elif "file" in item:
                        search_dm.WriteVerbose(f"{num_files + 1}) {item['name']}\n")

                        files.append(item)
                        num_files += 1
                    else:
                        assert False, item

                if "@odata.nextLink" in response:
                    to_search.append(
                        (
                            f"{display_name} (continued)",
                            response["@odata.nextLink"],
                        ),
                    )

    return files


# ----------------------------------------------------------------------
def _GetFilesToProcess(
    dm: DoneManager,
    file_infos: list[dict[str, str]],
    name: str,
    output_dir: Path,
    pictures_subdir: str | None,
    videos_subdir: str | None,
    output_dir_template: str,
) -> list[tuple[dict[str, str], Path]]:
    files_to_process: list[tuple[dict[str, str], Path]] = []

    with dm.Nested(
        "Organizing content...",
        lambda: "{} to process".format(inflect.no("file", len(files_to_process))),
    ) as organize_dm:
        # ----------------------------------------------------------------------
        @dataclass(frozen=True)
        class FileProcessorInfo:
            file_types: set[str]
            output_dir_template: Path

        # ----------------------------------------------------------------------

        file_processors: dict[str, FileProcessorInfo] = {}

        if pictures_subdir is not None:
            file_processors["image"] = FileProcessorInfo(
                set([".jpg", ".heic"]),
                output_dir / pictures_subdir / output_dir_template,
            )

        if videos_subdir is not None:
            file_processors["video"] = FileProcessorInfo(
                set([".avi", ".mp4"]),
                output_dir / videos_subdir / output_dir_template,
            )

        ignore_file_types: set[str] = set([".thm"])

        for file_info in file_infos:
            ext = os.path.splitext(file_info["name"])[1]
            if ext in ignore_file_types:
                continue

            file_processor: FileProcessorInfo | None = None

            for (
                file_processor_attribute_name,
                potential_file_processor,
            ) in file_processors.items():
                if file_processor_attribute_name in file_info:
                    file_processor = potential_file_processor
                    break

                if ext in potential_file_processor.file_types:
                    file_processor = potential_file_processor
                    break

            if file_processor is None:
                organize_dm.WriteError(f"The file '{file_info['name']}' is not a recognized file type.\n")
                continue

            creation_date = datetime.datetime.fromisoformat(file_info["createdDateTime"])

            dest = (
                file_processor.output_dir_template.parent
                / str(file_processor.output_dir_template).format(
                    name=name,
                    year=creation_date.year,
                    month=creation_date.month,
                    day=creation_date.day,
                )
                / file_info["name"]
            )

            if dest.is_file():
                organize_dm.WriteVerbose(f"The file '{dest}' already exists.\n")
                continue

            files_to_process.append((file_info, dest))

    return files_to_process


# ----------------------------------------------------------------------
def _ProcessFiles(
    dm: DoneManager,
    files_to_process: list[tuple[dict[str, str], Path]],
    get_headers_func: Callable[[], dict[str, str]],
) -> None:
    mkdir_lock = threading.Lock()

    # ----------------------------------------------------------------------
    def Prepare(
        context: Any,
        on_simple_status_func: Callable[[str], None],
    ) -> tuple[int, ExecuteTasks.TransformTasksExTypes.TransformFuncType]:
        on_simple_status_func("Initializing...")

        index = context
        del context

        file_info, dest = files_to_process[index]
        url = file_info["@microsoft.graph.downloadUrl"]

        # The API seems to follow different patterns with regards to the download url depending on
        # the user. If 'tempauth' appears in the download url query string, use that as the Bearer
        # token. If it doesn't exist, use the standard access token as the Bearer token.
        #
        # Additional info: https://techcommunity.microsoft.com/t5/onedrive-developer/onedrive-download-issue-401-unauthorized/m-p/4161618
        #
        parsed_url = urlparse(url)
        components = parse_qs(parsed_url.query)

        tempauth = components.get("tempauth", None)
        if tempauth is not None:
            assert len(tempauth) == 1, tempauth
            headers = {"Bearer": tempauth[0]}
        else:
            headers = get_headers_func()

        response = requests.get(
            url,
            headers=headers,
            stream=True,
        )

        response.raise_for_status()

        total_size = int(response.headers["content-length"])

        # ----------------------------------------------------------------------
        def Transform(
            status: ExecuteTasks.Status,
        ) -> None:
            copied = 0

            temp_filename = PathEx.CreateTempFileName()

            with temp_filename.open("wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    copied += len(chunk)

                    status.OnProgress(copied, "Downloading")

            status.OnProgress(copied, f"Copying to '{dest}'...")

            # Double-checked locking pattern
            if not dest.parent.is_dir():
                with mkdir_lock:
                    dest.parent.mkdir(parents=True, exist_ok=True)

            dest_temp = dest.parent / f"{dest.name}.tmp"

            dest_temp.unlink(missing_ok=True)
            shutil.move(temp_filename, dest_temp)

            dest.unlink(missing_ok=True)
            shutil.move(dest_temp, dest)

        # ----------------------------------------------------------------------

        return total_size, Transform

    # ----------------------------------------------------------------------

    ExecuteTasks.TransformTasksEx(
        dm,
        "Processing files...",
        [ExecuteTasks.TaskData(ftp[0]["name"], index) for index, ftp in enumerate(files_to_process)],
        Prepare,
    )


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()
