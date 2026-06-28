# weread-shelf · 微信读书数据看板

每天自动拉取你自己的微信读书数据，存进本地 SQLite，并以一个自托管的 Web 看板展示
阅读时长曲线、书架进度、笔记/划线增量、个性化推荐。


## 数据来源：官方 Agent API Gateway

所有数据都来自官方 skill 背后的 HTTP 网关，使用一个**长期有效**、自动绑定你账号的
API Key。

```
POST https://i.weread.qq.com/api/agent/gateway
Authorization: Bearer wrk-xxxx
Content-Type: application/json

{"api_name": "/readdata/detail", "skill_version": "1.0.3", "mode": "monthly"}
```

接口目录与字段说明见 [doc/api.md](doc/api.md)；产品需求见 [doc/requirements.md](doc/requirements.md)。

## 快速开始（容器部署）

```bash
docker compose up -d --build
open http://localhost:8765
```

首次启动后：

1. 打开 `http://localhost:8765/admin`，用默认账号 **`admin` / `admin`** 登录。
2. 系统会**强制你先修改用户名和密码**。
3. 登录 https://weread.qq.com/r/weread-skills 生成 `wrk-` 开头的 API Key，
   在管理页「网关连接」里填入（保存时会试调网关校验）。
4. 配置好 Key 后会自动开始首次拉取；也可在管理页点「立即刷新」。

SQLite 存放在挂载卷 `/data/weread.db`（compose 中映射为命名卷 `weread_db_data`），
容器重建后数据仍在。拉取间隔默认每 24 小时，可在管理页改为 6 / 12 / 24 小时。

## 管理页面 `/admin`

- **网关连接**：填入 / 替换 API Key（只写不回显）。
- **设置**：显隐「概览」「发现」模块；普通用户是否需要登录；拉取间隔；时区；`skill_version`。
- **拉取状态**：上次拉取时间 / 结果，以及「立即刷新」按钮。
- **用户管理**：创建 / 改密 / 删除普通用户（仅当开启「需要登录」时才用得上）。
- **我的账号**：随时修改管理员自己的用户名和密码。
- 当网关建议升级 `skill_version` 时，管理页会给出提示。

## 本地开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

python cli.py serve         # 启动 Web + 调度器（http://localhost:8765）
# 然后在 /admin 配置 API Key（CLI 也从 DB 读 key，需先在网页配置一次）

python cli.py pull          # 一次性拉取全部数据
python cli.py backfill -m 13   # 回填最近 13 个月的每日阅读时长历史
python cli.py probe /readdata/detail -p mode=overall   # 调试：原始网关调用
pytest                      # 运行测试
```
