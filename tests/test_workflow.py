"""Offline checks for the Python embedded in the GitHub Actions workflow."""

import contextlib
import gzip
import io
import json
import os
from email.message import Message
from pathlib import Path
import textwrap
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


class WorkflowTests(unittest.TestCase):
    COOKIE = "session=TEST_ONLY_NOT_A_REAL_CREDENTIAL"
    PRIVATE_MESSAGE = "server-private-marker"

    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/anyrouter-checkin.yml"
        workflow = path.read_text(encoding="utf-8")
        script = workflow.split("          python3 - <<'PY'\n", 1)[1].split("          PY\n", 1)[0]
        cls.code = compile(textwrap.dedent(script), str(path), "exec")

    def execute(self, body=b'{"message":"","success":true}', content_type="application/json",
                status=200, error=None, env=None, content_encoding=None):
        environment = {"ANYROUTER_COOKIE": self.COOKIE, "ANYROUTER_USER_ID": "123"}
        if env is not None:
            environment.update(env)
        response = MagicMock()
        response.status = status
        response.headers = Message()
        response.headers["Content-Type"] = content_type
        if content_encoding is not None:
            response.headers["Content-Encoding"] = content_encoding
        response.read.side_effect = lambda size: body[:size]
        response.__enter__.return_value = response
        opener = MagicMock()
        opener.open.return_value = response
        opener.open.side_effect = error
        stdout, stderr = io.StringIO(), io.StringIO()
        exit_code = 0
        namespace = {}
        with patch.dict(os.environ, environment, clear=True), \
                patch("urllib.request.build_opener", return_value=opener), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                exec(self.code, namespace)
            except SystemExit as exc:
                exit_code = exc.code
        logs = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(self.COOKIE, logs)
        self.assertNotIn(self.PRIVATE_MESSAGE, logs)
        return exit_code, logs, opener, namespace

    def test_success_and_request_shape(self):
        code, logs, opener, namespace = self.execute()
        self.assertEqual(code, 0)
        self.assertIn("success=true", logs)
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://anyrouter.top/api/user/sign_in")
        self.assertEqual(request.get_method(), "POST")
        self.assertIsNone(request.data)
        self.assertEqual(request.get_header("Cookie"), self.COOKIE)
        self.assertEqual(request.get_header("New-api-user"), "123")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        redirect = namespace["NoRedirect"]()
        self.assertIsNone(redirect.redirect_request(
            request, None, 302, "Found", {}, "https://example.invalid/"))

    def test_raw_server_message_is_not_logged(self):
        for success in (True, False):
            with self.subTest(success=success):
                body = json.dumps({"success": success, "message": self.PRIVATE_MESSAGE}).encode()
                code, _, _, _ = self.execute(body=body)
                self.assertEqual(code, 0 if success else 1)

    def test_json_with_nonstandard_content_type(self):
        for content_type in ("text/plain", "text/html", "application/problem+json", self.PRIVATE_MESSAGE):
            with self.subTest(content_type=content_type):
                code, _, _, _ = self.execute(content_type=content_type)
                self.assertEqual(code, 0)

    def test_gzip_json_succeeds(self):
        body = gzip.compress(b'{"message":"","success":true}')
        code, logs, _, _ = self.execute(body=body, content_encoding="gzip")
        self.assertEqual(code, 0)
        self.assertIn("Content-Encoding=gzip", logs)

    def test_html_and_script_diagnostics(self):
        cases = [
            (b'<html><body>login</body></html>', "HTML/XML"),
            (f'<script>{self.PRIVATE_MESSAGE}</script>'.encode(), "JavaScript"),
        ]
        for raw, diagnostic in cases:
            for encoding in ("identity", "gzip"):
                with self.subTest(diagnostic=diagnostic, encoding=encoding):
                    body = gzip.compress(raw) if encoding == "gzip" else raw
                    code, logs, _, _ = self.execute(
                        body=body, content_type="text/html", content_encoding=encoding)
                    self.assertEqual(code, 1)
                    self.assertIn("Content-Type=text/html", logs)
                    self.assertIn(diagnostic, logs)
                    self.assertNotIn(raw.decode(), logs)

    def test_invalid_or_oversized_compression_fails(self):
        cases = [
            {"body": b'not gzip', "content_encoding": "gzip"},
            {"body": gzip.compress(b' ' * 65537), "content_encoding": "gzip"},
            {"content_encoding": "br"},
            {"content_encoding": self.PRIVATE_MESSAGE},
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                code, _, _, _ = self.execute(**case)
                self.assertEqual(code, 1)

    def test_complete_cookie_succeeds_without_logging_values(self):
        cookie = self.COOKIE + "; acw_tc=TEST_ONLY_TRAFFIC_COOKIE; acw_sc__v2=TEST_ONLY_SECURITY_COOKIE"
        code, logs, opener, _ = self.execute(env={"ANYROUTER_COOKIE": cookie})
        self.assertEqual(code, 0)
        self.assertEqual(opener.open.call_args.args[0].get_header("Cookie"), cookie)
        self.assertNotIn("TEST_ONLY_TRAFFIC_COOKIE", logs)
        self.assertNotIn("TEST_ONLY_SECURITY_COOKIE", logs)

    def test_bad_responses_fail(self):
        cases = [
            {"body": b'{"success":false}'},
            {"body": b'{"success":"true"}'},
            {"body": b'{"success":1}'},
            {"body": b'{}'},
            {"body": b'[]'},
            {"body": b'null'},
            {"body": b'not json'},
            {"body": b'\xff'},
            {"body": b'<html>challenge</html>', "content_type": "text/html"},
            {"body": b' ' * 65537},
            {"status": 302},
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                code, _, opener, _ = self.execute(**case)
                self.assertEqual(code, 1)
                opener.open.assert_called_once()

    def test_http_errors_fail_without_retry(self):
        for status in (301, 302, 401, 403, 429, 500):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://anyrouter.top/api/user/sign_in", status, self.PRIVATE_MESSAGE, {}, None)
                code, _, opener, _ = self.execute(error=error)
                self.assertEqual(code, 1)
                opener.open.assert_called_once()

    def test_network_errors_fail_without_retry(self):
        for error in (urllib.error.URLError(self.PRIVATE_MESSAGE), TimeoutError(), OSError()):
            with self.subTest(error=type(error).__name__):
                code, _, opener, _ = self.execute(error=error)
                self.assertEqual(code, 1)
                opener.open.assert_called_once()

    def test_invalid_secrets_never_send_requests(self):
        cases = [
            {"ANYROUTER_COOKIE": ""},
            {"ANYROUTER_COOKIE": "RAW_SESSION_VALUE="},
            {"ANYROUTER_COOKIE": "session="},
            {"ANYROUTER_COOKIE": "acw_tc=TEST_ONLY"},
            {"ANYROUTER_COOKIE": "Cookie: " + self.COOKIE},
            {"ANYROUTER_COOKIE": "Cookie: acw_tc=TEST_ONLY; " + self.COOKIE},
            {"ANYROUTER_COOKIE": "session=one\nother=two"},
            {"ANYROUTER_USER_ID": ""},
            {"ANYROUTER_USER_ID": "0"},
            {"ANYROUTER_USER_ID": "-1"},
            {"ANYROUTER_USER_ID": "not-a-number"},
            {"ANYROUTER_USER_ID": "１２３"},
        ]
        for index, env in enumerate(cases):
            with self.subTest(case=index):
                code, _, opener, _ = self.execute(env=env)
                self.assertEqual(code, 1)
                opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
