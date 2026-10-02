"""Run one check-in through an ordinary, isolated browser on GitHub Actions."""

from contextlib import ExitStack
from dataclasses import dataclass
import os
import secrets
import sys
from urllib.parse import urlsplit


ORIGIN = "https://anyrouter.top"
SELF_PATH = "/api/user/self"
SIGN_IN_PATH = "/api/user/sign_in"
MARKER_HEADER = "x-anyrenew-check-in"
MAX_RESPONSE_BYTES = 65536
PROBE_ATTEMPTS = 6
CONTENT_TYPES = {"application/json", "text/html", "text/plain", "application/octet-stream"}

# Return only classified metadata, never the account data or raw response body.
FETCH_JSON = r"""async ({origin, path, method, userId, marker, limit, timeout}) => {
    if (location.origin !== origin || !['/api/user/self', '/api/user/sign_in'].includes(path)) {
        return {kind: 'offsite'};
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
        const headers = {'Accept': 'application/json', 'new-api-user': userId};
        if (method === 'POST') headers['x-anyrenew-check-in'] = marker;
        const response = await fetch(path, {
            method, headers, credentials: 'same-origin', mode: 'same-origin',
            redirect: 'error', cache: 'no-store', signal: controller.signal
        });
        const mime = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
        const knownTypes = ['application/json', 'text/html', 'text/plain', 'application/octet-stream'];
        const meta = {status: response.status, content_type: knownTypes.includes(mime) ? mime : 'other'};
        if (!response.body) return {...meta, kind: 'invalid_json'};
        const reader = response.body.getReader();
        const chunks = [];
        let length = 0;
        while (true) {
            const {done, value} = await reader.read();
            if (done) break;
            length += value.byteLength;
            if (length > limit) {
                await reader.cancel();
                return {...meta, kind: 'too_large'};
            }
            chunks.push(value);
        }
        const bytes = new Uint8Array(length);
        let offset = 0;
        for (const chunk of chunks) {
            bytes.set(chunk, offset);
            offset += chunk.byteLength;
        }
        const text = new TextDecoder().decode(bytes);
        let value;
        try {
            value = JSON.parse(text);
        } catch {
            const prefix = text.slice(0, 8192).trimStart().toLowerCase();
            const kind = prefix.includes('<script') ? 'script'
                : (mime === 'text/html' || prefix.startsWith('<')) ? 'html' : 'invalid_json';
            return {...meta, kind};
        }
        return {...meta, kind: 'json', success: value !== null && typeof value === 'object'
            && !Array.isArray(value) && value.success === true};
    } catch (error) {
        return {kind: error.name === 'AbortError' ? 'timeout' : 'network_error'};
    } finally {
        clearTimeout(timer);
    }
}"""


class CheckInError(Exception):
    """An error whose message is safe to show in public workflow logs."""


@dataclass(repr=False)
class Settings:
    cookies: list
    user_id: str


def load_settings(environ):
    cookie = environ.get("ANYROUTER_COOKIE", "").strip()
    user_id = environ.get("ANYROUTER_USER_ID", "").strip()
    if not cookie or not user_id:
        raise CheckInError("缺少 ANYROUTER_COOKIE 或 ANYROUTER_USER_ID，请配置仓库 Secrets。")
    if not cookie.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in cookie):
        raise CheckInError("Cookie 必须是一行有效的 ASCII 请求头值，不要包含换行。")
    if cookie.lower().startswith("cookie:"):
        raise CheckInError("ANYROUTER_COOKIE 只填请求头的值，不要添加 Cookie: 前缀。")
    parts = {}
    for item in cookie.split(";"):
        if not item.strip():
            continue
        name, separator, value = item.strip().partition("=")
        if not separator or not name or any(char.isspace() for char in name) or name in parts:
            raise CheckInError("Cookie 格式错误，请复制同一次请求的完整 Cookie 值。")
        parts[name] = value.strip()
    if not parts.get("session"):
        raise CheckInError("ANYROUTER_COOKIE 必须包含 session=会话值，不能只填写裸值。")
    if not user_id.isascii() or not user_id.isdecimal() or len(user_id) > 20 or int(user_id) <= 0:
        raise CheckInError("ANYROUTER_USER_ID 应填写 new-api-user 请求头中的正整数。")
    cookies = [
        {"name": name, "value": value, "url": ORIGIN + "/", "secure": True, "httpOnly": name == "session"}
        for name, value in parts.items()
    ]
    return Settings(cookies=cookies, user_id=user_id)


def is_site_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == "anyrouter.top"
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)
    except (TypeError, ValueError):
        return False


class SingleCheckIn:
    """Suppress automatic page POSTs; allow only one marked, controlled fetch."""

    def __init__(self, user_id):
        self.user_id = user_id
        self.marker = secrets.token_hex(16)
        self.sent = False

    def handle(self, route):
        request = route.request
        if not is_site_url(request.url) or urlsplit(request.url).path != SIGN_IN_PATH:
            route.continue_()
            return
        headers = {name.lower(): value for name, value in request.headers.items()}
        if request.method != "POST" or headers.get(MARKER_HEADER) != self.marker or self.sent:
            route.abort("blockedbyclient")
            return
        self.sent = True
        # This is a local routing marker, not a header for the remote server.
        headers.pop(MARKER_HEADER)
        headers["new-api-user"] = self.user_id
        route.continue_(headers=headers)


def fetch_result(page, path, settings, marker=""):
    if path not in (SELF_PATH, SIGN_IN_PATH) or not is_site_url(page.url):
        raise CheckInError("页面已离开本站或接口地址无效，已停止，不会向外站发送凭证。")
    return page.evaluate(FETCH_JSON, {
        "origin": ORIGIN, "path": path, "method": "POST" if path == SIGN_IN_PATH else "GET",
        "userId": settings.user_id, "marker": marker, "limit": MAX_RESPONSE_BYTES,
        "timeout": 20000 if path == SIGN_IN_PATH else 10000,
    })


def report_result(result, label):
    if not isinstance(result, dict):
        raise CheckInError("浏览器返回了异常结果；未输出原始数据。")
    status = result.get("status")
    status = status if type(status) is int and 100 <= status <= 599 else "未知"
    mime = result.get("content_type")
    mime = mime if mime in CONTENT_TYPES else "其他类型"
    print(f"{label}：HTTP {status}；Content-Type={mime}。", flush=True)


def require_success(result, label):
    report_result(result, label)
    status = result.get("status")
    if status in (401, 403):
        raise CheckInError("会话失效、用户 ID 不匹配或站点拒绝访问，请在浏览器检查。")
    if status == 429:
        raise CheckInError("HTTP 429：请求受限；本次停止，不重复签到。")
    messages = {
        "offsite": "页面已离开本站，已停止；不会自动操作 OAuth 登录。",
        "html": "普通浏览器仍收到 HTML 页面；登录或安全验证未通过。",
        "script": "普通浏览器仍收到 JavaScript 验证页面；云端访问尚未通过站点验证。",
        "too_large": "响应体超过大小限制；未输出原始响应。",
        "invalid_json": "响应不是有效 JSON；未输出原始响应。",
        "timeout": "请求超时，结果不明；本次不重复签到，请到网站确认。",
        "network_error": "浏览器请求失败或发生重定向，结果不明；本次不重复签到。",
    }
    kind = result.get("kind")
    if kind != "json":
        raise CheckInError(messages.get(kind, "浏览器返回了异常响应；未输出原始数据。"))
    if type(status) is not int or not 200 <= status < 300 or result.get("success") is not True:
        raise CheckInError("接口未返回成功 JSON；请检查登录态、用户 ID 和网站签到记录。")


def run_browser(playwright, settings, browser_error):
    with ExitStack() as resources:
        browser = playwright.chromium.launch(headless=True)
        resources.callback(browser.close)
        context = browser.new_context(service_workers="block", accept_downloads=False)
        resources.callback(context.close)
        context.add_cookies(settings.cookies)
        guard = SingleCheckIn(settings.user_id)
        context.route("**/*", guard.handle)
        page = context.new_page()
        resources.callback(page.close)
        try:
            page.goto(ORIGIN + "/console", wait_until="domcontentloaded", timeout=30000)
        except browser_error:
            if not is_site_url(page.url):
                raise CheckInError("本站页面未能正常打开；未输出浏览器原始错误。")

        # Only the read-only login probe can repeat while the normal page settles.
        for attempt in range(PROBE_ATTEMPTS):
            page.wait_for_timeout(2000 if attempt == 0 else 5000)
            if not is_site_url(page.url):
                raise CheckInError("页面已跳转到外站，可能需要重新登录；已停止。")
            try:
                result = fetch_result(page, SELF_PATH, settings)
            except browser_error:
                result = {"kind": "network_error"}
            report_result(result, "登录验证")
            if result.get("kind") in ("html", "script", "timeout", "network_error") and result.get("status") not in (401, 403, 429):
                if attempt + 1 < PROBE_ATTEMPTS:
                    continue
            require_success(result, "登录验证结果")
            break

        print("登录验证通过，准备发送一次签到请求。", flush=True)
        try:
            result = fetch_result(page, SIGN_IN_PATH, settings, guard.marker)
        except browser_error:
            raise CheckInError("签到请求执行中断，结果不明；不会再次发送，请到网站确认。")
        if not guard.sent:
            raise CheckInError("未确认受控签到请求已发出；本次停止，不自动重试。")
        require_success(result, "签到响应")
        print("签到接口返回 success=true。")
        print("接口未提供本次新增额度信息；请以网站余额或领取记录为准。")


def main(environ=None):
    try:
        settings = load_settings(os.environ if environ is None else environ)
        # Keep all offline tests runnable with the existing local Python alone.
        from playwright.sync_api import Error, sync_playwright
        with sync_playwright() as playwright:
            run_browser(playwright, settings, Error)
        return 0
    except CheckInError as error:
        print(str(error), file=sys.stderr)
    except ImportError:
        print("缺少浏览器依赖；请使用 GitHub Actions 中的安装步骤，本机无需安装。", file=sys.stderr)
    except Exception:
        print("浏览器执行失败；未输出可能包含凭证的异常详情。请检查安装步骤及站点访问状态。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
