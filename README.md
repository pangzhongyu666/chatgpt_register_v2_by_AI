# ChatGPT / Codex 自动注册工具 v2.0

基于 Skymail 自建邮箱服务的 ChatGPT / Codex 自动注册与 OAuth Token 生成工具。

## 功能特性

- 使用 Skymail 自建邮箱服务真实创建临时邮箱
- 支持多个邮箱域名，在 `config.json` 中配置
- 自动注册 ChatGPT 账号并轮询验证码
- 自动执行 OAuth 登录，获取 `access_token` / `refresh_token`
- 支持并发注册
- 支持注册成功后保存账号、Token JSON、AK / RK
- 支持 CPA 上传、CPA 失效清理、目标数量控制
- 内置常见 TLS / Cookie / 重试处理逻辑

## 项目结构

```text
.
├── lib/
│   ├── config.py
│   ├── skymail_client.py
│   ├── chatgpt_client.py
│   ├── oauth_client.py
│   ├── sentinel_token.py
│   ├── token_manager.py
│   └── utils.py
├── chatgpt_register_v2.py
├── config.json
├── config.example.json
└── README.md
```

## 环境要求

- Python 3.9+
- 可用的 Skymail 管理员账号
- 可访问 OpenAI 的网络环境
- 如启用 CPA 清理功能，建议安装 `aiohttp`

## 安装依赖

```bash
pip install curl_cffi requests aiohttp
```

如果暂时不用 CPA 清理，也可以不装 `aiohttp`。

## 配置说明

复制 `config.example.json` 为 `config.json`，按需修改：

```json
{
  "skymail_admin_email": "admin@example.com",
  "skymail_admin_password": "your_password_here",
  "skymail_domains": ["example.com"],
  "proxy": "http://127.0.0.1:7890",
  "output_file": "registered_accounts.txt",
  "enable_oauth": true,
  "oauth_required": true,
  "oauth_issuer": "https://auth.openai.com",
  "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
  "oauth_redirect_uri": "http://localhost:1455/auth/callback",
  "ak_file": "ak.txt",
  "rk_file": "rk.txt",
  "token_json_dir": "tokens",
  "cpa_base_url": "",
  "cpa_token": "",
  "cpa_workers": 20,
  "cpa_timeout": 12,
  "cpa_retries": 1,
  "cpa_used_threshold": 95,
  "cpa_clean": false,
  "cpa_upload": false,
  "cpa_target_count": 0,
  "cpa_prune_local": false,
  "cpa_user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
}
```

### 核心字段

- `skymail_admin_email` / `skymail_admin_password`
  - Skymail 管理员账号
  - 程序启动时会调用 `/api/public/genToken` 自动生成 API Token

- `skymail_domains`
  - 可用于创建临时邮箱的域名列表
  - 程序会随机选择一个域名并调用 `/api/public/addUser` 真实创建邮箱

- `proxy`
  - OpenAI / Skymail 请求使用的代理
  - 格式示例：`http://127.0.0.1:7890`

- `enable_oauth`
  - 是否在注册成功后继续执行 OAuth 登录

- `oauth_required`
  - 如果 OAuth 失败，是否视为整次注册失败

### CPA 字段

- `cpa_base_url`
  - CPA 管理平台根地址，例如 `https://your-cpa-host`

- `cpa_token`
  - CPA 管理接口 Bearer Token

- `cpa_upload`
  - 注册并拿到 Token 后自动上传到 CPA

- `cpa_clean`
  - 注册前后自动清理 CPA 中已失效的账号

- `cpa_used_threshold`
  - `used_percent` 阈值，达到或超过后仅做统计标记，不会参与清理

- `cpa_target_count`
  - CPA 中有效 token 达到这个数量后，程序会停止继续注册

- `cpa_prune_local`
  - 上传 CPA 成功后删除本地 token 文件，并从本地账号文件中移除对应账号

## 使用方法

```bash
python chatgpt_register_v2.py
python chatgpt_register_v2.py -n 5 -w 3
python chatgpt_register_v2.py -n 10 -w 5 --no-oauth
```

### 命令行参数

- `-n, --num`: 本次注册数量
- `-w, --workers`: 并发线程数
- `--no-oauth`: 禁用 OAuth 登录

## 输出文件

- `registered_accounts.txt`
  - 注册结果汇总，格式如 `email----password----oauth=ok`

- `accounts.txt`
  - 本地账号密码列表

- `ak.txt`
  - Access Token 列表

- `rk.txt`
  - Refresh Token 列表

- `tokens/`
  - 每个账号对应的完整 Token JSON 文件

## 工作原理

### 1. Skymail 邮箱创建

- 从 `skymail_domains` 中随机选择域名
- 生成随机邮箱前缀
- 调用 Skymail 的 `/api/public/addUser` 真正创建邮箱

### 2. ChatGPT 注册

- 访问 ChatGPT 首页并建立 session
- 获取 CSRF Token
- 提交邮箱并进入注册流程
- 发送邮箱验证码
- 轮询 Skymail 邮箱并提取 6 位验证码
- 完成账号资料创建

### 3. OAuth 登录

- Bootstrap OAuth session
- 提交邮箱和密码
- 如需要则再次处理邮箱 OTP
- 处理 consent / workspace / organization 选择
- 获取 authorization code
- 兑换 `access_token` / `refresh_token`

### 4. CPA 维护

- 可在注册前后拉取 CPA `auth-files`
- 可检测 `401` 失效账号，并统计 `used_percent >= cpa_used_threshold` 的账号
- 可自动删除失效账号
- 可在上传成功后清理本地文件

## 注意事项

### 1. Skymail

- 当前版本已修复“只拼邮箱字符串、不真实建号”的问题
- 如果后台查不到邮箱，请优先检查 Skymail `/api/public/addUser` 是否可用

### 2. OAuth

- 当前流程依赖 OpenAI 侧页面和接口行为，后续若变动可能需要更新
- 如果日志卡在 `workspace/select` 或 `authorization code`，通常是 consent 链路变化

### 3. CPA

- 开启 `cpa_clean` 时需要 `aiohttp`
- 建议先单独验证 `cpa_base_url` 与 `cpa_token` 是否可用，再批量跑

### 4. 并发

- 建议先从 `1` 线程开始验证环境
- 并发过高可能增加 TLS 错误、验证码延迟或 OAuth 不稳定概率

## 建议排查顺序

1. 先确认 Skymail 能生成 Token。
2. 再确认创建邮箱后后台能看到该用户。
3. 再确认注册阶段能正常收到验证码。
4. 最后再看 OAuth 与 CPA。
