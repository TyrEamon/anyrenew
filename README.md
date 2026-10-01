# AnyRouter 每日签到

使用个人账号登录后的 Cookie，定时调用 `POST https://anyrouter.top/api/user/sign_in`。只在站点允许此类自动化的前提下使用。

- 每天北京时间 **10:15**（UTC 02:15）运行，也支持手动运行。
- 直接使用 GitHub 托管的 Ubuntu runner 上的 Python 标准库，无需安装依赖。
- 不自动操作 OAuth、不刷新会话、不处理验证码或绕过安全验证。
- 不打印 Cookie、用户 ID、请求头或接口原始响应，不自动跟随重定向。
- 仅将 JSON 中严格的 `success: true` 判为接口处理成功，不把它解释为本次一定新增了额度。

## 配置

1. 创建一个 GitHub 仓库，推荐设为私有。将 `.github/workflows/anyrouter-checkin.yml` 按原路径放入默认分支；注意不要漏掉以点开头的 `.github` 文件夹。
2. 打开仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，添加：

   | Secret 名称 | 值 |
   | --- | --- |
   | `ANYROUTER_COOKIE` | 重新登录后，请求头 `Cookie` 的完整值，例如 `session=你的会话值; 其他Cookie名=对应值`。不要添加 `Cookie:` 前缀，也不要添加外层引号或换行。 |
   | `ANYROUTER_USER_ID` | 同一账号、同一次会话的 `new-api-user` 请求头值，只填写数字。不是 API Key。 |

   在浏览器登录站点后，按 F12 → Network → Fetch/XHR，刷新控制台，点击 `sign_in`，即可查看相应请求头。不要把凭证写入工作流、README、Issue 或聊天。

3. 打开仓库 **Actions → AnyRouter Daily Check-in → Run workflow**，在默认分支上手动运行一次。
4. 成功时日志显示 `签到接口返回 success=true。`。再到网站检查余额或领取记录，确认是否实际到账。

只有工作流已经提交到默认分支，GitHub 才会触发定时任务。定时任务可能延迟，不能保证精确在 10:15 启动。GitHub Actions 用量和私有仓库免费额度以账号当前套餐为准；公共仓库连续 60 天没有活动时，定时工作流会被自动禁用。

## 凭证安全

`session` 是登录凭证。若完整 Cookie 曾被公开或发送到聊天，优先使用站点提供的会话撤销功能使旧会话失效，再重新登录获取新值。仅退出浏览器或重新登录，不一定能让旧 Cookie 在服务端失效，具体取决于站点实现。

不要使用之前暴露过的 Cookie。更新凭证时，在 GitHub Secrets 中替换 `ANYROUTER_COOKIE`；更换账号时也要更新 `ANYROUTER_USER_ID`。工作流不会自动延长 Cookie 有效期。

## 失败排查

- **缺少 Secret / 用户 ID 格式错误**：核对 Secret 名称、内容以及所在仓库。
- **HTTP 401 / 403**：可能是 Cookie 过期、会话与用户 ID 不匹配，或安全验证拦截；先在浏览器检查。
- **HTTP 429**：服务器限制请求频率，不要连续触发工作流。
- **不是 JSON / 重定向 / `success` 不为 `true`**：可能是登录失效、安全验证或站点接口变更。在浏览器查看最新请求和响应，不要把原始凭证贴到日志中排查。
- **网络或超时错误**：请求可能已到达服务器，因此工作流不自动重试；先到网站确认领取状态。
- **本机正常、GitHub 运行失败**：浏览器的安全验证 Cookie 可能与运行环境有关；不能保证在 GitHub 的云端 IP 上复用。工作流不尝试绕过安全验证。

浏览器中出现的 `127.0.0.1:10808` 是本地代理地址，不是站点接口地址，不要配置到 GitHub 工作流中。

## 本地离线测试

在项目根目录执行：

```bash
python -B -m unittest discover -s tests -v
```

测试使用模拟 Cookie 和模拟网络响应，不连接 AnyRouter，不需要真实凭证。`-B` 避免生成 `__pycache__`；无需安装测试依赖。

覆盖成功响应、请求方法和请求头、禁止重定向、失败响应、网络与 HTTP 错误、不自动重试、无效配置以及日志不泄露凭证或原始响应。

## 验证范围

接口路径、空 POST 请求、`new-api-user` 请求头和成功响应格式依据浏览器抓包信息配置。真实 GitHub runner 的网络访问、站点安全验证以及实际额度发放，需要配置自己的新凭证后通过手动工作流验证。
