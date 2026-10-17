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

### 提醒卡片按钮

到期提醒卡片自带「✅ 完成」「⏰ 延后30分钟」两个按钮，点击后飞书向同一回调地址
发送卡片回调（旧版 `type=action` 或事件 `card.action.trigger`，二者均兼容），
服务端同步执行并返回新卡片（状态更新、按钮消失）+ toast 提示；也可直接回复
「完成」「延后10分钟」文字。若开发者控制台有独立的「卡片回调」订阅项，请确认
回调地址与加密配置和事件订阅一致。

## 一键更新部署（服务器）

```bash
./update.sh              # git 同步 → 重建镜像 → compose 重启 → 健康检查
FORCE=1 ./update.sh      # 代码未变也强制重建
BRANCH=dev ./update.sh   # 指定分支
HEALTH_URL=http://127.0.0.1:8010/healthz ./update.sh   # 端口非 8000 时
```

健康检查失败会打印容器最近日志并以非 0 退出，成功后自动清理旧镜像。

## 测试

```bash
uv run pytest -q
```

## 消息示例

| 你说 | 机器人做 |
|------|----------|
| 明天早上10点提醒我买菜 | create_todo |
| 我今天有什么待办 | list_todos |
| 把买菜的提醒改到下午3点 | update_todo |
| 完成 #1 | complete_todo |
| 取消周会提醒 | delete_todo |
