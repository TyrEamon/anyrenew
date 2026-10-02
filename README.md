# AnyRouter 每日签到

使用个人账号的登录 Cookie，在 GitHub Actions 的临时机器上启动普通 Playwright Chromium，访问控制台并执行一次签到。只在站点允许此类自动化的前提下使用。

- 每天北京时间 **10:15**（UTC 02:15）运行，也支持手动运行。
- 浏览器和依赖只安装在 GitHub 临时 runner 上，不需要在个人电脑安装。
- 仅在同源登录验证通过后，发送一次 `POST https://anyrouter.top/api/user/sign_in`。
- 使用正常浏览器配置，不使用 stealth、指纹伪装、代理轮换或验证码破解。
- 不自动操作 OAuth、不刷新失效会话。浏览器方案也不保证 GitHub 的云端 IP 能通过站点验证。

## 配置

1. 将项目文件放入 GitHub 仓库默认分支，保留 `.github/workflows/anyrouter-checkin.yml` 的路径。推荐使用私有仓库。
2. 打开仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，添加两个 **Repository secrets**，无需创建 Environment：

   | Secret 名称 | 值 |
   | --- | --- |
   | `ANYROUTER_COOKIE` | 同一次浏览器请求的完整 Cookie 值，例如 `session=你的会话值; acw_tc=对应值; cdn_sec_tc=对应值; acw_sc__v2=对应值`。必须含非空 `session`，不要添加 `Cookie:` 前缀、外层引号或换行。 |
   | `ANYROUTER_USER_ID` | 同一账号请求头中 `new-api-user` 的数字值，不是 API Key。 |

   在浏览器登录站点后，按 F12 → Network → Fetch/XHR，刷新控制台，点击 `sign_in`，即可查看相应请求头。不要把凭证写入代码、Issue、聊天或日志。

3. 打开 **Actions → AnyRouter Daily Check-in → Run workflow**，选择默认分支（本仓库为 `main`），新建一次运行。
4. 成功时日志显示 `签到接口返回 success=true。`。再到网站检查余额或领取记录，确认是否实际到账。

**代码更新后请新建运行，不要点击旧记录的 Re-run jobs。** 重跑旧记录会继续使用旧提交的工作流。浏览器版的步骤包括 `Run offline tests`、`Install browser only on the temporary runner` 和 `Check in with an isolated browser`。

只有工作流已提交到默认分支，GitHub 才会触发定时任务。调度可能延迟，不能保证精确在 10:15 启动。Actions 用量和私有仓库额度以账号套餐为准；公共仓库连续 60 天没有活动时，定时工作流会被自动禁用。

## 如何避免重复签到

控制台可能在页面加载时自动发起签到。脚本提前注册仅针对本站签到接口的拦截器，阻止这些自动请求，避免它们与脚本重复。

页面正常加载和执行自身脚本后，脚本最多进行 6 次只读登录检查，等待普通页面导航或验证完成。只有收到登录验证成功的 JSON，才在页面中发送一次受控、同源的签到请求。用于区分该请求的临时标记会在发送前移除，不发送到服务器。请求失败、超时或结果不明时不会再发一次。

签到请求拒绝重定向，Cookie 只绑定到 `anyrouter.top`，用户 ID 不设置为全局跨域请求头。页面若转到外站 OAuth 登录，脚本停止，不自动登录。

## 凭证与磁盘安全

- `session` 是登录凭证。如果曾公开或发送到聊天，优先使用站点的会话撤销功能使旧会话失效，再获取新值。仅关闭浏览器或重新登录，不一定会让旧 Cookie 在服务端失效。
- 更新 Cookie 时替换 GitHub Secret；更换账号时同时更新用户 ID。工作流不会延长 Cookie 有效期。
- 只输出预定义状态、常见响应类型等诊断，不输出 Cookie、原始响应、账号资料或浏览器异常详情。
- 不保存截图、trace、storage state 或登录态文件，不上传浏览器产物。
- Playwright 固定为 `1.63.0`，要求 Python 3.10+；依赖、临时虚拟环境及浏览器只在 GitHub runner 中安装，pip 缓存关闭，浏览器位于 runner 临时目录。
- 正常结束或抛出异常时都会关闭页面、context 和浏览器；整个临时 runner 在作业结束后回收。
- 本地离线测试兼容现有 Python 3.8，不需要升级本机 Python、安装 Playwright、下载 Chromium 或创建虚拟环境。

## 失败排查

- **Cookie / 用户 ID 格式错误**：核对 Secret 名称与值。Cookie 不能只填写裸 session 值。
- **登录验证失败、HTTP 401 / 403**：会话可能过期、用户 ID 不匹配，或者站点拒绝该运行环境。先在浏览器检查账号状态。
- **浏览器持续收到 HTML / JavaScript 页面**：说明普通浏览器方案也没有通过站点登录或安全验证。完整 Cookie 并不保证能跨机器复用；需要人工验证时停止，不尝试绕过。
- **跳转到外站**：可能需要重新 OAuth 登录。请在自己的浏览器完成，然后更新 Secrets。
- **HTTP 429**：请求受限，不要连续手动触发。
- **签到超时或执行中断**：请求可能已到达服务器，先在网站确认结果，不要盲目重跑。
- **浏览器依赖安装失败**：查看安装步骤的日志。这与账号 Cookie 无关，也不需要在本机安装依赖。
- **HTTP 200 / `success=true`**：200 本身不代表接口成功；严格的 `success=true` 也只说明接口处理成功，不证明本次新增了额度。

浏览器中显示的 `127.0.0.1:10808` 是本地代理地址，不是站点服务器，不要填进 GitHub 工作流。

## 本地离线测试

```bash
python -B -m unittest discover -s tests -v
```

Python 测试使用标准库 unittest/mock，模拟浏览器和账号信息，不连接 AnyRouter。`-B` 禁止生成 `__pycache__`。若已有 Node 18+，还会用 Node 内置功能验证实际的浏览器 fetch 函数；没有 Node 时明确跳过这一项，不自动安装。

覆盖凭证校验、Cookie 作用域、同源限制、单次发送保护、页面自动请求拦截、登录等待、严格成功判断、HTML/脚本响应、重定向控制、超时与资源关闭，以及日志不泄露凭证或原始响应。

这些是离线测试，不代表真实 Chromium 或 GitHub 云端已经通过站点验证。最终需使用自己的新会话凭证在 GitHub 新建运行，并以网站记录核实额度。
