# noqa: INP001
# ----------------------------------------------------------------------
# |
# |  CreateSelfSignedCert.py
# |
# |  David Brownell <db@DavidBrownell.com>
# |      2024-08-04 18:05:52
# |
# ----------------------------------------------------------------------
# |
# |  Copyright David Brownell 2024
# |  Distributed under the MIT License.
# |
# ----------------------------------------------------------------------
"""Creates a PEM file for self-signed SSL certificates."""

import uuid

from pathlib import Path
from typing import Annotated

import typer

from dbrownell_Common.ContextlibEx import ExitStack
from dbrownell_Common.Streams.DoneManager import DoneManager, Flags as DoneManagerFlags
from dbrownell_Common import SubprocessEx
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
@app.command("EntryPoint", help=__doc__, no_args_is_help=True)
def EntryPoint(  # noqa: D103
    output_filename: Annotated[
        Path,
        typer.Argument(help="Output filename (e.g. my_cert.pem)", dir_okay=False, resolve_path=True),
    ],
    hostname: Annotated[
        str,
        typer.Argument(help="Hostname (e.g. davidbrownell.com)"),
    ],
    company: Annotated[
        str,
        typer.Argument(help="Company name or individual name."),
    ],
    city: Annotated[
        str,
        typer.Argument(help="City name"),
    ],
    state: Annotated[
        str,
        typer.Argument(help="State name"),
    ],
    expiry_days: Annotated[
        int,
        typer.Option("--expiry-days", help="The number of days the certificate is valid.", min=1),
    ] = 3650,
    key_size: Annotated[
        int,
        typer.Option("--key-size", help="The size of the key to generate.", min=2048),
    ] = 4096,
    verbose: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--verbose", help="Write verbose information to the terminal."),
    ] = False,
    debug: Annotated[  # noqa: FBT002
        bool,
        typer.Option("--debug", help="Write debug information to the terminal."),
    ] = False,
) -> None:
    with DoneManager.CreateCommandLine(
        flags=DoneManagerFlags.Create(verbose=verbose, debug=debug),
    ) as dm:
        unique_id = str(uuid.uuid4()).replace("-", "")
        key_filename = Path(f"{unique_id}_key.pem")
        cert_filename = Path(f"{unique_id}_cert.pem")

        with dm.Nested("Generating certificate...") as generate_dm:
            command_line = f'openssl req -x509 -newkey rsa:{key_size} -keyout {key_filename} -out {cert_filename} -sha256 -days {expiry_days} -nodes -subj "/C=XX/ST={state}/L={city}/O={company}/CN={hostname}"'

            generate_dm.WriteVerbose(f"Command Line: {command_line}\n\n")

            with generate_dm.YieldStream() as stream:
                generate_dm.result = SubprocessEx.Stream(command_line, stream)
                if generate_dm.result != 0:
                    return

        with (
            ExitStack(
                key_filename.unlink,
                cert_filename.unlink,
            ),
            dm.Nested(f"Writing '{output_filename}'..."),
        ):
            key_content = key_filename.read_text(encoding="utf-8")
            cert_content = cert_filename.read_text(encoding="utf-8")

            output_filename.parent.mkdir(parents=True, exist_ok=True)

            with output_filename.open("w", encoding="utf-8") as f:
                f.write(cert_content)
                f.write(key_content)


# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app()
