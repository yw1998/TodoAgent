# TodoAgent

IM 消息驱动的智能待办机器人：飞书发消息 → LLM Tool Calling 解析 → 创建待办 → 到期飞书提醒。
默认数据库 SQLite，自带 Web 管理页面（配置 + 待办浏览）。

设计文档见 `docs/`。

## 快速开始

```bash
cp .env.example .env                        # 填入 LLM 与飞书配置
docker compose up -d                        # Docker 方式（推荐）
```

本地运行：

```bash
uv venv && uv pip install -e ".[dev]"    # 或 pip install -e .
cp .env.example .env
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- 管理页面: http://localhost:8000/admin （初始账号见 .env）
- 飞书事件订阅 URL: `https://<你的域名>/feishu/events`
- API 文档: http://localhost:8000/docs

## 飞书配置

1. 开放平台创建自建应用，开启「机器人」能力。
2. 权限：`im:message`、`im:message:send_as_bot`、`im:chat`。
3. 事件订阅：订阅方式选「将事件发送至开发者服务器」，URL 填上面的回调地址，
   订阅事件 `接收消息 im.message.receive_v1`。
4. 将 Verification Token / Encrypt Key / App ID / App Secret 填入管理页「配置」或 .env。

## 测试

```bash
uv run pytest -q
```

## CI/CD 自动部署（GitHub Actions）

push 到 `master` 后，Actions 会 SSH 到服务器执行 `git pull + docker compose up -d --build`，
并对 `/healthz` 做部署后健康检查（失败则输出容器日志并置为失败）。

**一次性配置**

1. 服务器初始化（首次，以部署用户登录）：
   ```bash
   git clone https://github.com/yw1998/TodoAgent.git ~/TodoAgent
   cd ~/TodoAgent && cp .env.example .env && vi .env   # 填入 LLM/飞书/SECRET_KEY
   docker compose up -d
   ```
2. 将部署公钥加入服务器 `~/.ssh/authorized_keys`（公钥见仓库 Actions 配置文档或
   `todoagent_deploy.pub`）。若仓库为私有，同一公钥也加为仓库 Deploy Key（勾选写权限不需要，只读即可）。
3. 在 GitHub 仓库 Settings → Secrets and variables → Actions 添加：
   - `DEPLOY_USER`：服务器 SSH 用户名
   - `DEPLOY_KEY`：部署私钥内容（`-----BEGIN OPENSSH PRIVATE KEY-----` 开头的整段）

主机 `119.91.64.47`、端口 `36666` 已写死在 `.github/workflows/deploy.yml`。

> 注意：workflow 使用 `git reset --hard origin/master` 同步代码，服务器上 `~/TodoAgent`
> 目录请勿手工改代码；配置一律放 `.env`（未被 git 跟踪，不受影响）。

## 消息示例

| 你说 | 机器人做 |
|------|----------|
| 明天早上10点提醒我买菜 | create_todo |
| 我今天有什么待办 | list_todos |
| 把买菜的提醒改到下午3点 | update_todo |
| 完成 #1 | complete_todo |
| 取消周会提醒 | delete_todo |
