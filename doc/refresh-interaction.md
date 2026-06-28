# 刷新交互逻辑(前端如何知道同步结束)

本文说明点「立即刷新」之后,前端与后端是怎么配合让你**知道这次同步到底跑完了没、成没成功**的。
配套文档:数据流见 [data-flow.md](data-flow.md),需求见 [requirements.md](requirements.md)。

## 要解决的问题

后端的同步是**发后即忘**:`POST /api/refresh` 起一个后台线程就立刻返回,HTTP 这一下并不等同步
跑完。所以光看这一个请求,前端无从得知"什么时候结束、结果如何"——只能让用户反复手动刷页面靠猜。

**做法**:后端再开一个**只读的状态接口** `GET /api/refresh/status`,前端点完刷新后**轮询**它,
直到这次同步真正结束,再弹结果提示(成功时附带条数),并按场景决定是否自动刷新页面。

> 心智模型:`POST /api/refresh` = "按下启动按钮";`GET /api/refresh/status` = "盯着仪表盘"。
> 按钮只负责点火,知不知道"到站了"全靠盯仪表盘。

---

## 一、后端:一把锁 + 两个接口

### 三个触发,同一把锁

同步有三种触发,全部汇聚到 `fetcher.run_pull_locked(kind)`,共用 **fetcher 里唯一的进程级 mutex**
(非阻塞 try-lock):谁先抢到谁跑,抢不到的直接跳过。因此**全局任何时刻最多一个同步在执行**。

| 触发 | kind | 代码 |
|---|---|---|
| 进程启动、库里没数据 | `startup` | `web.py` lifespan 起后台线程 |
| 定时器到点(每 N 小时) | `daily` | `scheduler._job` |
| 点「立即刷新」 | `manual` | `web.py` `POST /api/refresh` 起后台线程 |

`fetcher.pull_running()` 把这把锁的状态暴露给 web 层,状态接口用它回答"现在是否在跑"。
正因三路共用一把锁,**定时后台同步也能被状态接口如实看到**,而不只是手动那次。

### `POST /api/refresh`——点火

| 情况 | 返回 |
|---|---|
| 未配置 API key | `400 {"detail": "API key not configured"}` |
| 已有同步在跑(锁被占) | `202 {"status": "already_running"}` |
| 正常启动 | `202 {"status": "started"}` |

它**不等同步结束**,起完线程立刻返回上表之一。

### `GET /api/refresh/status`——盯仪表盘(只读,不触发任何同步)

```json
{
  "running": true,
  "has_data": false,
  "last": {
    "id": 7,
    "kind": "manual",
    "ok": false,
    "finished_at": null,
    "error": "",
    "counts": { "shelf": 0, "books": 0, "bookmarks": 0, "...": 0 }
  }
}
```

| 字段 | 含义 | 前端拿来干嘛 |
|---|---|---|
| `running` | 当前是否有同步持锁运行 | 判断"还在跑 / 已结束" |
| `has_data` | 历史上是否**成功**同步过(`PullRun.ok` 存在) | 决定"首次同步才自动刷新页面" |
| `last` | 最近一条 run(**不限成功失败**) | 见下表 |
| `last.id` | `PullRun` 主键,**单调递增** | **判定"哪一条 run 是我这次等的"**(关键,见第三节) |
| `last.kind` | `startup` / `daily` / `manual` | 区分来源 |
| `last.ok` | 成功与否 | 决定弹"完成"还是"失败" |
| `last.finished_at` | 完成时间;**`null` 表示仍在跑** | 区分"跑完的 run"与"刚建好还没跑完的 run" |
| `last.error` | 失败原因 | 失败提示文案 |
| `last.counts` | 各类抓取条数 | 成功提示里展示"书架 N、划线 M…" |

> `last`(`repo.latest_pull`,取最近一条)和概览里的"上次拉取时间"(`repo.last_pull`,取最近一条
> **成功**的)是两个不同查询:状态接口要能反映"正在跑 / 刚失败"的 run,所以不能只看成功的。

---

## 二、前端:轮询状态机(`base.html`)

所有逻辑在 `base.html` 的 `<script>` 里,`admin.html` 因 `extends base.html` 自动复用。

### 公共件

- `getStatus()`:GET 状态接口;若未登录被重定向到登录页(返回 HTML),`r.json()` 会抛错,由调用方
  吞掉——所以未登录场景不会炸。
- `pollUntilDone(matchId)`:**每 2.5 秒**轮询一次,**上限 10 分钟**(只约束前端盯多久,不约束同步
  本身)。命中条件:`!running && last.finished_at && matchId(last.id)`,命中即返回该 run;超时返回
  `null`(同步仍在服务端继续,提示用户稍后自行刷新)。
- `reportPull(last, reload)`:
  - `null` → 弹「同步仍在进行,请稍后刷新页面查看」
  - `ok` → 弹「刷新完成:书架 12,划线 30…」,`reload` 为真则 0.9s 后 `location.reload()`
  - 失败 → 弹「刷新失败:<error>」
- `setBusy(btn, busy)`:全程禁用按钮并把文案切到「刷新中…」。

### 流程 A——用户点「立即刷新」(`refreshNow`)

1. 先 `getStatus()` 记下基线 `baseId = last?.id ?? 0`。
2. `POST /api/refresh`,按返回分支设定"哪条 run 算我的"(`matchId`):
   - `started` → `matchId = id > baseId`(等**新建**的那条 run)。
   - `already_running` → 弹「已有同步任务在进行中,正在等待完成…」,`matchId = id >= baseId`(改为等
     **正在跑**的那条 run 跑完)。
   - 其它 → 直接弹 `刷新:<status>` 收场。
3. `reportPull(await pollUntilDone(matchId), reload=true)`——用户主动点的,成功后**自动刷新页面**。

### 流程 B——打开页面时发现已有同步在跑(`checkBackgroundPull`,`DOMContentLoaded` 触发)

为的就是"启动首次同步 / 定时后台同步"——你没点任何东西,但同步正在进行,得让你看见进度。

1. `getStatus()`;若 `!running || !last` 直接返回(没在跑,什么都不做)。
2. 否则把刷新按钮置「刷新中…」,弹「检测到后台同步,正在等待完成…」。
3. `targetId = last.id`,`firstSync = !has_data`,`pollUntilDone(id => id >= targetId)`。
4. `reportPull(last, reload=firstSync)`——**仅当是首次同步(此前库里没数据)才自动刷新**;已有数据的
   增量定时同步只弹提示,不打断你正在看的内容。

---

## 三、为什么用 `run.id` 比较,而不是只看 `running`

光看 `running` 从 `true` 变 `false` 不够,有两个时间窗会误判,所以用**单调递增的 `run.id`** 锚定
"我等的到底是哪一条 run":

- **`started` 用 `> baseId`**:`baseId` 是上一条**已完成**的 run。从 `POST` 返回到后台线程真正建好
  新 run 行之间有个窗口,这期间 `running` 可能仍是 `false`、`last` 还是那条旧的已完成 run——`id > baseId`
  为假,于是继续轮询,**不会**把旧结果误当本次结果。等新 run 建好、跑完,`id` 必然大于 `baseId`,命中。
- **`already_running` 用 `>= baseId`**:此时 `baseId` 正是那条**正在跑**的 run 的 id(`latest_pull`
  返回它,`finished_at=null`)。它跑完后 id 不变,`>= baseId` 命中;又因初始 `running=true`,`!running`
  这道闸保证不会在它跑完前误命中任何旧 run。

> 一句话:`> baseId` 等一条"将要出现的新 run";`>= baseId` 等一条"已经存在、正在跑的 run"。

---

## 四、场景一览(你会看到什么)

| 场景 | 表现 |
|---|---|
| 点刷新 | 按钮变「刷新中…」全程禁用 → 完成弹「刷新完成:书架 12,划线 30…」并自动刷新页面 |
| 点刷新,但后台已在跑 | 「已有同步任务在进行中,正在等待完成…」→ 那次跑完再弹结果 |
| 打开页面正赶上**首次**同步 | 自动「检测到后台同步…」→ 完成后**自动刷新**页面显示数据 |
| 打开页面正赶上**定时增量**同步 | 自动「检测到后台同步…」→ 完成只弹提示,**不刷新**(不打断浏览) |
| 同步失败 | 弹「刷新失败:<错误信息>」(来自 `PullRun.error`) |
| 同步超过 10 分钟还没完 | 前端停止轮询,弹「同步仍在进行…」;**同步在服务端继续**,稍后自行刷新即可 |
| 未配置 API key | `POST` 返回 400,弹错误 |

---

## 五、边界与约定

- **不存在跨标签页的实时同步指示**:只在页面**加载那一刻**检测一次"是否已有同步在跑"。同步若是在你
  停留期间才由定时器启动,需刷新页面才会被检测到——这是有意为之,避免常驻后台轮询。
- **超时只停轮询、不停同步**:10 分钟是前端"盯多久"的上限;后台同步不受影响,会继续到自然结束。
- **未登录安全**:状态接口非公开路径;未登录时 `getStatus()` 拿到的是登录页 HTML,解析失败被吞掉,
  轮询静默退出,不报错。
- **超长同步的悬挂行**:进程在同步途中被杀,会留下一条 `finished_at=null` 的 run;但 `running` 基于
  进程内的锁(新进程锁是空的),所以重启后状态接口如实报 `running=false`,下一次成功同步会产生新行盖过它。

---

## 六、涉及文件

| 关注点 | 位置 |
|---|---|
| 锁 + 加锁运行 + `pull_running()` | `app/fetcher.py` |
| `POST /api/refresh`、`GET /api/refresh/status` | `app/web.py` |
| 定时器走同一把锁 | `app/scheduler.py` |
| `latest_pull()`(取最近一条,不限成败) | `app/repository.py` |
| 轮询状态机、按钮状态、提示 | `app/templates/base.html` |
| `PullRun` 表(`id` / `ok` / `finished_at` / `counts_json` …) | `app/models.py` |
