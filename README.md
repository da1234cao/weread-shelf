# weread-shelf · 微信读书数据看板

每天自动拉取你自己的微信读书数据，存进本地 SQLite，并以一个自托管的 Web 看板展示
**长期趋势**（阅读时长曲线、书架进度、笔记/划线增量、个性化推荐）——这些是官方 App
本身不长期保留的东西。专为 Linux + 容器部署设计。

## 数据来源：官方 Agent API Gateway

所有数据都来自官方 skill 背后的 HTTP 网关，使用一个**长期有效**、自动绑定你账号的
API Key（不像社区 cookie 方案那样每 ~2 小时就要刷新 `wr_skey`）。

```
POST https://i.weread.qq.com/api/agent/gateway
Authorization: Bearer wrk-xxxx
Content-Type: application/json

{"api_name": "/readdata/detail", "skill_version": "1.0.3", "mode": "monthly"}
```

用到的接口（完整目录可用 `python cli.py probe /_list` 查看）：

| 用途 | api_name | 说明 |
|------|----------|------|
| 阅读统计 | `/readdata/detail` | `mode`=weekly/monthly/annually/overall；`readTimes` 是 `{时间戳:秒}` 的每日序列 |
| 书架 | `/shelf/sync` | 全部书籍 + `archive` 分组 + `finishReading` |
| 笔记本概览 | `/user/notebooks` | 每本书的进度、划线数、想法数（分页 `lastSort`） |
| 划线 | `/book/bookmarklist` | 用户的划线（`markText`），含 `removed` 删除列表 |
| 想法 | `/review/list/mine` | 用户的个人想法（参数是小写 `bookid`） |
| 推荐 | `/book/recommend` | 个性化推荐 |

> 所有时长字段单位为**秒**。

## 获取 API Key

登录 https://weread.qq.com/r/weread-skills ，在页面生成 `wrk-` 开头的 API Key，
填入 `.env` 的 `WEREAD_API_KEY`。

## 快速开始（容器部署）

```bash
cp .env.example .env       # 填入 WEREAD_API_KEY
docker compose up -d --build
# 首次启动若无数据会自动后台拉取一次；也可手动触发：
curl -X POST http://localhost:8765/api/refresh
# 打开看板
open http://localhost:8765
```

SQLite 存放在挂载卷 `./data/weread.db`，容器重建后数据仍在。每天按 `PULL_CRON`
（默认 `0 3 * * *`，即每天 03:00）自动拉取。

## 本地开发

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # 填入 WEREAD_API_KEY

python cli.py pull          # 一次性拉取全部数据
python cli.py backfill -m 13   # 回填最近 13 个月的每日阅读时长历史
python cli.py serve         # 启动 Web + 调度器（http://localhost:8765）
python cli.py probe /readdata/detail -p mode=overall   # 调试：原始网关调用
pytest                      # 运行测试
```

## 配置项（环境变量 / `.env`）

| 变量 | 默认 | 说明 |
|------|------|------|
| `WEREAD_API_KEY` | — | 必填，`wrk-` 开头 |
| `TZ` | `Asia/Shanghai` | 日界与调度时区 |
| `PULL_CRON` | `0 3 * * *` | 每日拉取的 cron 表达式 |
| `PORT` | `8765` | 看板端口 |
| `DB_PATH` | `./data/weread.db`（容器内 `/data/weread.db`） | SQLite 路径 |
| `SKILL_VERSION` | `1.0.3` | 网关协议版本 |

## 架构

```
APScheduler (每日 cron) ─┐
POST /api/refresh ───────┼─→ fetcher.run_daily_pull()
首次启动自动拉取 ─────────┘        │  WeReadClient → i.weread.qq.com/api/agent/gateway
                                   ▼
                          SQLite（带日期的快照，可算趋势）
                                   ▼
                 FastAPI：Jinja2 看板页 + JSON API + Markdown 导出
```

- `app/client.py` — 网关封装（重试、限速、鉴权错误识别）
- `app/models.py` / `app/db.py` — SQLModel 表与 SQLite 引擎
- `app/fetcher.py` — 每日拉取与历史回填（单本失败不影响整体）
- `app/repository.py` — 入库去重 + 看板聚合查询
- `app/scheduler.py` — APScheduler cron
- `app/web.py` — FastAPI 路由 / 页面 / JSON API / `/api/refresh`
- `cli.py` — `pull` / `backfill` / `probe` / `serve`

页面：`/` 概览（趋势图） · `/shelf` 书架 · `/notes` 笔记（可导出 Markdown） · `/discover` 推荐。

## 安全

- 默认**私网 / 无鉴权**，请只在可信内网或本机访问。
- `.env`（含 API Key）已被 `.gitignore` 忽略，不要提交。
- 若要暴露公网，请在反向代理层加鉴权，或在 `app/web.py` 增加 Token 中间件。
