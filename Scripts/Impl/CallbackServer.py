# ----------------------------------------------------------------------
# |
# |  CallbackServer.py
# |
# |  David Brownell <db@DavidBrownell.com>
# |      2024-08-05 07:29:26
# |
# ----------------------------------------------------------------------
# |
# |  Copyright David Brownell 2024
# |  Distributed under the MIT License.
# |
# ----------------------------------------------------------------------
"""Contains the CallbackServer object"""

import os
import ssl
import sys
import textwrap
import threading

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib import parse


# ----------------------------------------------------------------------
class CallbackServer:
    """\
    Creates a HTTP server that is active until a specific url is served. At that point, an event is
    fired that indicates that the server received the desired request. This can be used as the
    server for an OAuth callback, where the server is active until that callback is invoked.
    """

    # ----------------------------------------------------------------------
    # |
    # |  Public Types
    # |
    # ----------------------------------------------------------------------
    DEFAULT_SUCCESS_MESSAGE_TEMPLATE = textwrap.dedent(
        """\
        <div id="header">Congratulations</div>
        <div id="content">'{app_name}' has been authorized; it is safe to close this window.</div>
        """,
    )

    DEFAULT_FAILURE_MESSAGE_TEMPLATE = textwrap.dedent(
        """\
        <div id="header">Oops!</div>
        <div id="content">The authorization request for '{app_name}' has not completed successfully and functionality will not work as expected; you may close this window.</div>
        """,
    )

    DEFAULT_HTML_TEMPLATE = textwrap.dedent(
        """\
        <html>
          <head>
            <style type="text/css">
              body {{ font-family: 'Helvetica Neue', sans-serif; }}
              #header  {{ color: #111;    font-size: 100px; font-weight: bold; letter-spacing: -1px; line-height: 1; text-align: center; }}
              #content {{ color: #685206; font-size: 14px; line-height: 24px; margin: 24px 0px 24px; text-align: center; text-justify: inter-word; }}
            </style>
            <title>{title}</title>
          </head>
          <body>
            {content}
          </body>
        </html>
        """,
    )

    # ----------------------------------------------------------------------
    # |
    # |  Public Methods
    # |
    # ----------------------------------------------------------------------
    def __init__(
        self,
        application_name: str,
        callback_result_name_or_names: str | list[str] | None,
        callback_port: int,
        ssl_pem_filename: Path | None,
        success_message_template: str = DEFAULT_SUCCESS_MESSAGE_TEMPLATE,
        failure_message_template: str = DEFAULT_FAILURE_MESSAGE_TEMPLATE,
        html_template: str = DEFAULT_HTML_TEMPLATE,
    ) -> None:
        if isinstance(callback_result_name_or_names, list):
            callback_result_names = callback_result_name_or_names
        elif isinstance(callback_result_name_or_names, str):
            callback_result_names = [callback_result_name_or_names]
        elif callback_result_name_or_names is None:
            callback_result_names = []
        else:
            assert False, callback_result_name_or_names  # pragma: no cover

        del callback_result_name_or_names

        self._results: dict[str, str | None] = {}
        parent_results = self._results

        self._quit_event = threading.Event()
        parent_quit_event = self._quit_event

        # ----------------------------------------------------------------------
        class RequestHandler(BaseHTTPRequestHandler):
            # ----------------------------------------------------------------------
            def do_GET(self) -> None:
                request = parse.urlparse(self.path)

                query = parse.parse_qs(request.query)

                is_successful = True

                for result_name in callback_result_names:
                    if result_name in query:
                        parent_results[result_name] = query[result_name][0]
                    else:
                        parent_results[result_name] = None
                        is_successful = False

                message_template = (
                    success_message_template
                    if is_successful
                    else failure_message_template
                )

                self.send_response(200)
                self.end_headers()

                self.wfile.write(
                    html_template.format(
                        title=application_name,
                        content=message_template.format(app_name=application_name),
                    ).encode()
                )

                parent_quit_event.set()

            # ----------------------------------------------------------------------
            def log_message(self, *args, **kwargs) -> None:
                pass

        # ----------------------------------------------------------------------

        self._httpd = HTTPServer(("127.0.0.1", callback_port), RequestHandler)

        if ssl_pem_filename:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(ssl_pem_filename)

            self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)

        self._httpd.timeout = 1  # second

        # ----------------------------------------------------------------------
        def ThreadProc():
            while not self._quit_event.is_set():
                self._httpd.handle_request()

        # ----------------------------------------------------------------------

        self._thread = threading.Thread(target=ThreadProc)
        self._thread.start()

    # ----------------------------------------------------------------------
    def Wait(
        self,
        timeout_seconds: int = 120,
    ) -> str | dict[str, str | None] | None:
        """\
        Wait for results.

        Return value will be:
            - str if callback_result_name_or_names is a str
            - None if callback_result_name_or_names is None
            - dict[str, str | None] if callback_result_name_or_names is a list[str]
        """

        if not self._quit_event.wait(timeout_seconds):
            self._quit_event.set()
            raise Exception("timeout")

        if len(self._results) == 1:
            return next(iter(self._results.values()))

        return self._results
