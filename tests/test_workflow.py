"""Offline tests: no real account, network access, or local browser install."""

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("anyrenew_checkin", ROOT / "scripts/checkin.py")
checkin = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checkin
SPEC.loader.exec_module(checkin)


class BrowserError(Exception):
    pass


class WorkflowTests(unittest.TestCase):
    COOKIE = "session=TEST_ONLY_NOT_A_REAL_CREDENTIAL==; acw_tc=TEST_ONLY_TRAFFIC_COOKIE"
    PRIVATE = "server-private-marker"
    SUCCESS = {"kind": "json", "status": 200, "content_type": "application/json", "success": True}

    def setUp(self):
        self.env = {"ANYROUTER_COOKIE": self.COOKIE, "ANYROUTER_USER_ID": "123"}
        self.settings = checkin.load_settings(self.env)
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stderr = contextlib.redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def tearDown(self):
        for secret in (self.COOKIE, "TEST_ONLY_NOT_A_REAL_CREDENTIAL", "TEST_ONLY_TRAFFIC_COOKIE", self.PRIVATE):
            self.assertNotIn(secret, self.output.getvalue())

    def route(self, url=None, method="POST", headers=None):
        route = MagicMock()
        route.request.url = url or checkin.ORIGIN + checkin.SIGN_IN_PATH
        route.request.method = method
        route.request.headers = headers or {}
        return route

    def browser(self, probes=None, post=None, post_error=None, automatic=False):
        runtime = MagicMock()
        browser = runtime.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.url = checkin.ORIGIN + "/console"
        probe_results = list(probes or [dict(self.SUCCESS)])
        post_result = dict(self.SUCCESS) if post is None else post
        auto_route = self.route(headers={"new-api-user": "123"})

        def navigate(*args, **kwargs):
            if automatic:
                context.route.call_args.args[1](auto_route)

        def evaluate(expression, args):
            self.assertEqual(expression, checkin.FETCH_JSON)
            self.assertEqual(args["origin"], checkin.ORIGIN)
            self.assertEqual(args["userId"], "123")
            if args["path"] == checkin.SELF_PATH:
                result = probe_results.pop(0) if len(probe_results) > 1 else probe_results[0]
                if isinstance(result, Exception):
                    raise result
                return result
            self.assertEqual(args["path"], checkin.SIGN_IN_PATH)
            request = self.route(headers={checkin.MARKER_HEADER: args["marker"]})
            context.route.call_args.args[1](request)
            request.continue_.assert_called_once()
            if post_error:
                raise post_error
            return post_result

        page.goto.side_effect = navigate
        page.evaluate.side_effect = evaluate
        return runtime, browser, context, page, auto_route

    def assert_closed(self, browser, context, page):
        page.close.assert_called_once()
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_cookie_scope_and_secret_repr(self):
        session, traffic = self.settings.cookies
        self.assertEqual(session["value"], "TEST_ONLY_NOT_A_REAL_CREDENTIAL==")
        self.assertTrue(session["httpOnly"])
        self.assertFalse(traffic["httpOnly"])
        for cookie in self.settings.cookies:
            self.assertEqual(cookie["url"], checkin.ORIGIN + "/")
            self.assertTrue(cookie["secure"])
            self.assertNotIn("domain", cookie)
        self.assertNotIn("TEST_ONLY", repr(self.settings))

    def test_invalid_settings_fail_without_browser_import(self):
        cases = [
            {"ANYROUTER_COOKIE": ""}, {"ANYROUTER_USER_ID": ""},
            {"ANYROUTER_COOKIE": "RAW_VALUE="}, {"ANYROUTER_COOKIE": "session="},
            {"ANYROUTER_COOKIE": "Cookie: " + self.COOKIE},
            {"ANYROUTER_COOKIE": "session=one\nother=two"},
            {"ANYROUTER_COOKIE": "session=one; session=two"},
            {"ANYROUTER_COOKIE": "session=one; malformed"},
            {"ANYROUTER_COOKIE": "session=中文"},
            {"ANYROUTER_USER_ID": "0"}, {"ANYROUTER_USER_ID": "-1"},
            {"ANYROUTER_USER_ID": "１２３"}, {"ANYROUTER_USER_ID": "not-an-id"},
            {"ANYROUTER_USER_ID": "1" * 5000},
        ]
        for index, values in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(checkin.CheckInError):
                checkin.load_settings(dict(self.env, **values))
        self.assertNotIn("playwright.sync_api", sys.modules)

    def test_site_origin_validation(self):
        for url in (checkin.ORIGIN, checkin.ORIGIN + "/login", checkin.ORIGIN + ":443/console"):
            self.assertTrue(checkin.is_site_url(url))
        for url in ("http://anyrouter.top", "https://anyrouter.top.example.com", "https://github.com/login",
                    "https://user@anyrouter.top", "https://anyrouter.top:444", "https://anyrouter.top:bad", "about:blank"):
            self.assertFalse(checkin.is_site_url(url))

    def test_guard_blocks_automatic_and_duplicate_sign_ins(self):
        guard = checkin.SingleCheckIn("123")
        automatic = self.route()
        guard.handle(automatic)
        automatic.abort.assert_called_once_with("blockedbyclient")
        self.assertFalse(guard.sent)
        marked = self.route(headers={checkin.MARKER_HEADER: guard.marker, "accept": "application/json"})
        guard.handle(marked)
        self.assertTrue(guard.sent)
        marked.continue_.assert_called_once_with(headers={"accept": "application/json", "new-api-user": "123"})
        duplicate = self.route(headers={checkin.MARKER_HEADER: guard.marker})
        guard.handle(duplicate)
        duplicate.abort.assert_called_once()

    def test_guard_never_adds_headers_to_other_sites_or_paths(self):
        guard = checkin.SingleCheckIn("123")
        for url in ("https://example.com/api/user/sign_in", checkin.ORIGIN + checkin.SELF_PATH):
            route = self.route(url=url, method="GET")
            guard.handle(route)
            route.continue_.assert_called_once_with()
        self.assertFalse(guard.sent)

    def test_guard_counts_a_failed_send_without_retry(self):
        guard = checkin.SingleCheckIn("123")
        first = self.route(headers={checkin.MARKER_HEADER: guard.marker})
        first.continue_.side_effect = BrowserError(self.PRIVATE)
        with self.assertRaises(BrowserError):
            guard.handle(first)
        self.assertTrue(guard.sent)
        second = self.route(headers={checkin.MARKER_HEADER: guard.marker})
        guard.handle(second)
        second.abort.assert_called_once()

    def test_result_requires_boolean_success_and_success_status(self):
        checkin.require_success(dict(self.SUCCESS), "测试")
        cases = [dict(self.SUCCESS, success=value) for value in (False, "true", 1, None)]
        cases += [dict(self.SUCCESS, status=value) for value in (302, 401, 403, 429, 500, "200", True)]
        cases += [dict(self.SUCCESS, kind=kind) for kind in ("html", "script", "too_large", "invalid_json", "timeout", "network_error", "offsite")]
        for index, result in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(checkin.CheckInError):
                checkin.require_success(result, "测试")
        checkin.require_success(dict(self.SUCCESS, content_type=self.PRIVATE, message=self.PRIVATE), "测试")

    def test_fetch_refuses_other_origins_and_paths(self):
        page = MagicMock()
        page.url = "https://example.com/"
        with self.assertRaises(checkin.CheckInError):
            checkin.fetch_result(page, checkin.SELF_PATH, self.settings)
        page.url = checkin.ORIGIN
        with self.assertRaises(checkin.CheckInError):
            checkin.fetch_result(page, "/api/admin", self.settings)
        page.evaluate.assert_not_called()

    def test_browser_succeeds_without_duplicate_page_request(self):
        runtime, browser, context, page, automatic = self.browser(automatic=True)
        checkin.run_browser(runtime, self.settings, BrowserError)
        automatic.abort.assert_called_once()
        browser.new_context.assert_called_once_with(service_workers="block", accept_downloads=False)
        context.add_cookies.assert_called_once_with(self.settings.cookies)
        methods = [call.args[1]["method"] for call in page.evaluate.call_args_list]
        self.assertEqual(methods, ["GET", "POST"])
        self.assertIn("success=true", self.output.getvalue())
        self.assert_closed(browser, context, page)

    def test_read_only_probe_can_wait_for_normal_page_navigation(self):
        runtime, browser, context, page, _ = self.browser(probes=[
            {"kind": "script", "status": 200, "content_type": "text/html"},
            BrowserError(self.PRIVATE), dict(self.SUCCESS),
        ])
        checkin.run_browser(runtime, self.settings, BrowserError)
        methods = [call.args[1]["method"] for call in page.evaluate.call_args_list]
        self.assertEqual(methods, ["GET", "GET", "GET", "POST"])
        self.assert_closed(browser, context, page)

    def test_persistent_challenge_never_sends_sign_in(self):
        runtime, browser, context, page, _ = self.browser(probes=[{"kind": "script", "status": 200}])
        with self.assertRaises(checkin.CheckInError):
            checkin.run_browser(runtime, self.settings, BrowserError)
        self.assertEqual(page.evaluate.call_count, checkin.PROBE_ATTEMPTS)
        self.assertTrue(all(call.args[1]["method"] == "GET" for call in page.evaluate.call_args_list))
        self.assert_closed(browser, context, page)

    def test_expired_session_and_rate_limits_stop_immediately(self):
        for result in (dict(self.SUCCESS, success=False), {"kind": "html", "status": 403}, {"kind": "html", "status": 429}):
            with self.subTest(result=result):
                runtime, browser, context, page, _ = self.browser(probes=[result])
                with self.assertRaises(checkin.CheckInError):
                    checkin.run_browser(runtime, self.settings, BrowserError)
                page.evaluate.assert_called_once()
                self.assert_closed(browser, context, page)

    def test_navigation_to_oauth_does_not_send_credentials(self):
        runtime, browser, context, page, _ = self.browser()
        page.url = "https://github.com/login/oauth/authorize"
        with self.assertRaises(checkin.CheckInError):
            checkin.run_browser(runtime, self.settings, BrowserError)
        page.evaluate.assert_not_called()
        self.assert_closed(browser, context, page)

    def test_sign_in_failure_or_interruption_is_not_retried(self):
        for post, error in (({"kind": "timeout"}, None), (dict(self.SUCCESS, success=False), None),
                            (None, BrowserError(self.PRIVATE))):
            with self.subTest(error=error is not None):
                runtime, browser, context, page, _ = self.browser(post=post, post_error=error)
                with self.assertRaises(checkin.CheckInError):
                    checkin.run_browser(runtime, self.settings, BrowserError)
                self.assertEqual(page.evaluate.call_count, 2)
                self.assert_closed(browser, context, page)

    def test_partial_browser_creation_is_cleaned_up(self):
        runtime, browser, context, _, _ = self.browser()
        context.add_cookies.side_effect = BrowserError(self.PRIVATE)
        with self.assertRaises(BrowserError):
            checkin.run_browser(runtime, self.settings, BrowserError)
        context.close.assert_called_once()
        browser.close.assert_called_once()

    def test_main_does_not_print_browser_exception_details(self):
        runtime, _, _, _, _ = self.browser()
        runtime.chromium.launch.side_effect = BrowserError(self.COOKIE + self.PRIVATE)
        manager = MagicMock()
        manager.__enter__.return_value = runtime
        api = types.ModuleType("playwright.sync_api")
        api.Error = BrowserError
        api.sync_playwright = lambda: manager
        parent = types.ModuleType("playwright")
        with patch.dict(sys.modules, {"playwright": parent, "playwright.sync_api": api}):
            self.assertEqual(checkin.main(self.env), 1)
        self.assertIn("未输出", self.output.getvalue())

    def test_runner_context_is_only_used_inside_steps(self):
        workflow = (ROOT / ".github/workflows/anyrouter-checkin.yml").read_text(encoding="utf-8")
        job_configuration, steps = workflow.split("    steps:\n", 1)
        self.assertNotIn("${{ runner.", job_configuration)
        browser_path = "          PLAYWRIGHT_BROWSERS_PATH: ${{ runner.temp }}/anyrenew-browsers"
        self.assertEqual(steps.count(browser_path), 2)
        install, execution = steps.split("      - name: Check in with an isolated browser\n", 1)
        self.assertIn(browser_path, install)
        self.assertIn(browser_path, execution)
        self.assertNotIn("secrets.ANYROUTER_COOKIE", install)

    def test_browser_javascript_with_node_builtins(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed; browser JavaScript test not run locally.")
        probe = subprocess.run([node, "-p", "typeof Response + '/' + typeof ReadableStream"], capture_output=True, text=True, timeout=10)
        if probe.stdout.strip() != "function/function":
            self.skipTest("Node 18+ is needed for the JavaScript-only test; no package was installed.")
        result = subprocess.run([node, str(ROOT / "tests/check_fetch.js")], input=checkin.FETCH_JSON,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Browser fetch checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
