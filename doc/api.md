# 微信读书 Agent API Gateway 接口文档

本项目所有数据均来自微信读书官方 skill 背后的 HTTP 网关。本文档整理该网关**全部 17 个接口**，
内容由实测 `/_list` 目录接口获得（`python cli.py probe /_list`）。最近一次核对：2026-06-28，共 17 个接口，参数与返回字段均与线上一致。

## 调用方式

所有接口走同一个端点，通过 `api_name` 区分：

```
POST https://i.weread.qq.com/api/agent/gateway
Authorization: Bearer wrk-xxxxx
Content-Type: application/json

{"api_name": "/readdata/detail", "skill_version": "1.0.3", "mode": "monthly"}
```

- 请求体：`api_name` + `skill_version` + 各接口的扁平参数（顶层平铺，不嵌套）。
- API Key：登录 https://weread.qq.com/r/weread-skills 生成，`wrk-` 开头，长期有效、自动绑定账号。
- `need_login=true` 的接口返回的是**当前账号自己的数据**；`need_login=false` 的是公开数据。

### 通用约定

- **所有时长字段单位为秒。**
- 业务错误以非零 `errcode` 返回（常伴随 HTTP 499）；`errcode=0` 为成功。
- 鉴权失效错误码：`-2001 / -2010 / -2012`（key 过期/无效/未授权）。
- 返回体若带 `upgrade_info`，表示服务端建议升级 `skill_version`。
- 分页有两种风格：游标式（`synckey` / `lastSort` / `sessionId`）和偏移式（`maxIdx`）。

### 自查目录

```bash
python cli.py probe /_list                      # 完整接口目录（本文档来源）
python cli.py probe /readdata/detail -p mode=overall   # 调试单个接口
```

---

## 接口总览

共 17 个，本项目已用 8 个（标 ✅），未用 9 个。

| # | api_name | 用途 | 登录 | 本项目 |
|---|----------|------|:----:|:------:|
| 1 | `/readdata/detail` | 阅读统计（周/月/年/总） | 是 | ✅ |
| 2 | `/shelf/sync` | 书架列表 | 是 | ✅ |
| 3 | `/user/notebooks` | 有笔记的书籍清单（概览） | 是 | ✅ |
| 4 | `/book/bookmarklist` | 个人划线列表 | 是 | ✅ |
| 5 | `/review/list/mine` | 个人想法 + 书评（按 `type` 区分） | 是 | ✅ |
| 6 | `/book/recommend` | 个性化推荐书 | 是 | ✅ |
| 7 | `/book/info` | 书籍基本信息 | 否 | ✅ |
| 8 | `/book/chapterinfo` | 书籍章节目录 | 否 | ✅ |
| 9 | `/book/getprogress` | 单本阅读进度 | 是 | — |
| 10 | `/book/similar` | 相似书推荐 | 是 | — |
| 11 | `/book/bestbookmarks` | 热门划线（含文本） | 是 | — |
| 12 | `/book/underlines` | 章节划线热度统计（无文本） | 是 | — |
| 13 | `/book/readreviews` | 划线范围下的想法/评论 | 是 | — |
| 14 | `/review/list` | 某书公开点评/想法 | 是 | — |
| 15 | `/review/single` | 单条想法/评论详情 | 是 | — |
| 16 | `/store/search` | 全站搜索 | 是 | — |
| 17 | `/discover/interact/type3` | 发现页好友在读动态 | 是 | — |

---

## 一、阅读数据

### 1. `/readdata/detail` ✅
获取用户阅读统计数据（周/月/年/总），包含阅读时长、天数、读书排行、偏好分析等。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `mode` | string | 否 | `monthly` | `weekly`=本周, `monthly`=本月, `annually`=本年, `overall`=总计 |
| `baseTime` | int | 否 | `0` | 基准时间戳（0=当前周期；传历史时间戳查历史周期，服务端会归一化到周期起点） |

**返回字段**：`readTimes`（`{时间戳:秒}` 每日序列）、`readDays`、`totalReadTime`、`dayAverageReadTime`、
`compare`（同比）、`baseTime`、`readLongest`、`readStat`、`preferCategory`、`preferCategoryWord`、
`preferTime`、`preferTimeWord`、`preferAuthor`、`authorCount`、`preferPublisher`、`preferCp`、
`readRate`、`wrReadTime`、`wrListenTime`、`rank`、`registTime`、`medals`、`preferBooks`、
`recordReadingTime`、`readRecordsWord`、`readDistributionWord`。

> 本项目用法：每次同步刷新「当前 + 上一」周/月/年与 `overall`（累计），按 `(mode, baseTime)` 归一化存入
> `period_stat` 表。**首次同步**额外用历史 `baseTime` 逐周期回填 周/月/年 的全部历史（注册至今，由 `registTime`
> 定起点），之后只增量刷新当前周期。「概览」页按 `mode + offset` 直接读库渲染，浏览时不再联网（见
> `app.stats` / `app.fetcher._pull_stats`）。

---

## 二、书架与书籍信息

### 2. `/shelf/sync` ✅
获取用户书架列表（含听书/讲书）。**无参数。**

**返回字段**：`books`（书目，含 `bookId`/`title`/`author`/`cover`/`finishReading`/`secret`/`updateTime`）、
`archive`（分组文件夹，含 `name` 与 `bookIds`）、`albums`（听书）、`mp`（公众号）。

> 本项目用法：主同步第二步拉取，**无参数**。用 `archive` 建 `book_id → 分组名` 映射，`books` 逐本 upsert 基本信息，
> 并把每本的 `finishReading/secret/updateTime` 存为当日书架快照。`albums`(听书)/`mp`(公众号) 暂未使用。
> 也用作 `/admin` 保存 API Key 时的连通性自检调用。

### 7. `/book/info` ✅
获取书籍基本信息（书名、作者、简介等）。`need_login=false`，公开数据。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `bookId` | string | 是 | 书籍 ID |

**返回字段**：`bookId`、`deepLink`、`title`、`author`、`translator`、`cover`、`intro`、`category`、
`publisher`、`publishTime`、`isbn`、`wordCount`、`newRating`、`newRatingCount`、`newRatingDetail`（评分）。

> 本项目用法：主同步提交后，对所有缺元数据的书（`info_fetched==0`）逐本补全简介/分类/出版社/出版时间/ISBN，
> 每本一个独立短事务。**写一次**（拉到即标记 `info_fetched`），此后每天只补新书。

### 8. `/book/chapterinfo` ✅
获取书籍的章节目录。`need_login=false`，公开数据。是 `/book/underlines`、`/book/bestbookmarks` 取 `chapterUid` 的前置接口。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `bookId` | string | 是 | 书籍 ID |

**返回字段**：`bookId`、`synckey`、`chapterUpdateTime`、`chapters`。

> 本项目用法：同样在主同步之后逐本补章节目录，供书籍详情页按章节归并划线/想法。与 `/book/info` 的写一次不同，
> 当书架报告有更新章节（`chapterUpdateTime` 变新）时会**重新拉取**。

### 9. `/book/getprogress`
获取用户对某本书的阅读进度（比 `/user/notebooks` 中的进度更精确，可单本查询）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `bookId` | string | 是 | 书籍 ID |

**返回字段**：`bookId`、`book`、`timestamp`。

### 10. `/book/similar`
获取与某本书相似的推荐书籍。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `bookId` | string | 是 | — | 书籍 ID |
| `count` | int | 否 | `12` | 每页数量 |
| `maxIdx` | int | 否 | `0` | 翻页偏移 |
| `sessionId` | string | 否 | — | 翻页会话 ID（首次不传，后续用回包中的值） |

**返回字段**：`booksimilar`。

### 6. `/book/recommend` ✅
获取个性化推荐书籍（"为你推荐"）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `count` | int | 否 | `12` | 每页数量 |
| `maxIdx` | int | 否 | `0` | 翻页偏移 |

**返回字段**：`books`（含 `bookId`/`title`/`author`/`cover`/`category`/`intro`）。

> 本项目用法：主同步最后一步，按 `count=12` 拉一页"为你推荐"存当日快照；推荐书也会 upsert 进书籍表。
> 属**非关键步骤**——失败只计 `errors` 并继续，不中断整次同步。

---

## 三、个人笔记（划线 + 想法 + 书评）

### 3. `/user/notebooks` ✅
获取用户所有有笔记的书籍列表（笔记本概览）。游标分页。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `count` | int | 否 | `20` | 每页数量 |
| `lastSort` | int | 否 | — | 翻页游标（上一页最后一条的 `sort` 值） |

**返回字段**：`synckey`、`totalBookCount`、`totalNoteCount`、`noBookReviewCount`、`hasMore`、
`books`（每本含 `book` 元数据、`readingProgress`、`noteCount`、`bookmarkCount`、`reviewCount`、`sort`）。
其中 **`noteCount` 才是划线总数；`bookmarkCount` 实测恒为 0**（不要用它），`reviewCount` 含想法 + 书评。

> 本项目用法：主同步第三步。按 `count=50` + `lastSort` 游标翻页拉全（安全上限 100 页），
> 每本写入 `readingProgress/noteCount/reviewCount` 计数（`bookmarkCount` 恒 0，不存）；再据计数决定是否逐本拉划线
> （`noteCount>0`）与想法/书评（`reviewCount>0`），从而避免对无笔记的书做多余请求。

### 4. `/book/bookmarklist` ✅
获取用户对某本书的划线列表（不含书签）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `bookId` | string | 是 | 书籍 ID |

**返回字段**：`synckey`、`updated`（划线，含 `bookmarkId`/`markText`/`chapterUid`/`chapterIdx`/
`colorStyle`/`type`/`range`/`createTime`）、`removed`（已删除划线 ID）、`chapters`（章节标题映射）、`book`。

> 本项目用法：仅当某书 `/user/notebooks` 报告 `noteCount>0` 时逐本拉取；用 `chapters` 把 `chapterUid`
> 映射成章节标题随划线一起入库，`updated` upsert、`removed` 删除。

### 5. `/review/list/mine` ✅
获取用户在某本书上写的**全部个人评论**——既包含挂在段落上的「想法」，也包含对整本书的「书评」，
二者由 `type` 字段区分（见下）。**注意参数是小写 `bookid`。**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `bookid` | string | 是 | — | 书籍 ID（小写） |
| `synckey` | int | 否 | `0` | 翻页游标 |
| `count` | int | 否 | `20` | 每页数量 |

**顶层返回字段**：`reviews`、`totalCount`、`hasMore`、`synckey`、`removed`。
其中 `reviews` 数组的每一项是 `{reviewId, review}` 容器，真正的内容嵌在 **`review`** 子对象里。

**`review` 子对象按 `type` 分两类**（同一接口一并返回，需自行按 `type` 拆分）：

| `type` | 含义 | 是否挂章节 | 关键字段 |
|:------:|------|:----------:|----------|
| `1` | **段落想法**（在某句划线上写的批注） | 是 | `chapterUid`>0、`chapterIdx`、`chapterTitle`（与 `chapterName` 同值）、`abstract`（被划的原文）、`contextAbstract`（上下文）、`range`（字符区间）、`content`（想法正文）、`isPrivate` |
| `4` | **整本书书评**（读完后对全书的评价） | 否 | `chapterUid`==0、**无** `chapterTitle`/`chapterIdx`/`abstract`/`range`；独有 `star`（评分）、`newRatingLevel`（评级）、`isFinish`（是否读完后所写）、`htmlContent`（富文本正文）、`isDeepV` |

两类共有字段：`reviewId`、`bookId`、`content`、`htmlContent`、`type`、`createTime`、`book`、`author`、`isLike`、`topics`。

> **实测分布**（2026-06-28，本人账号）：482 条评论 = 459 条 `type=1` 段落想法 + 23 条 `type=4` 书评。
> 所有 `type=4` 均为 `chapterUid=0` 且无章节名——这正是若不按 `type` 拆分、书评会被归并进「未命名章节」桶的根因。

> 本项目用法：仅当某书 `reviewCount>0` 时按 `count=100` 拉取；入库前先把每项的 `review` 子对象取出
> （列表项本身是包了一层的容器），再做分桶归类。
>
> **分桶判据：`chapter_uid == 0` → 书评桶，其余 → 想法（按章节归并）。**
> 不用 `type==4` 而用 `chapter_uid==0` 作判据，是因为它一举两得：既把整本书书评单独拎出来，
> 又顺带根除了「未命名章节」空桶（该空桶本质就是无章节的评论堆出来的）。书评的语义标签仍对应
> `type==4`，但即便将来冒出未知 `type`，只要它无章节就不会污染想法的章节列表。

---

## 四、社区内容（他人 / 公开数据）

### 11. `/book/bestbookmarks`
获取某本书的热门划线列表（含划线文本和人数，按热度排序，最多 20 条）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `bookId` | string | 是 | — | 书籍 ID |
| `chapterUid` | int | 否 | `0` | 章节 UID（0=全部章节，从 `/book/chapterinfo` 获取） |
| `synckey` | int | 否 | `0` | 增量同步 key |

**返回字段**：`synckey`、`totalCount`、`items`、`chapters`。

### 12. `/book/underlines`
获取某本书某章节的划线热度统计（每条划线的人数/得分/类型，**不含划线文本**）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `bookId` | string | 是 | — | 书籍 ID |
| `chapterUid` | int | 是 | — | 章节 UID（从 `/book/chapterinfo` 获取） |
| `synckey` | int | 否 | `0` | 增量同步 key |

**返回字段**：`bookId`、`chapterUid`、`underlines`、`synckey`。

### 13. `/book/readreviews`
获取章节中某些划线范围下的想法/评论列表（每个划线最多 20 条）。

| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `bookId` | string | 是 | 书籍 ID |
| `chapterUid` | int | 是 | 章节 UID |
| `reviews` | array | 是 | 要查询的划线范围数组，每项含 `range`/`maxIdx`/`count`/`synckey` |

**返回字段**：`bookId`、`chapterUid`、`reviews`。

### 14. `/review/list`
获取某本书的公开点评/想法。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `bookId` | string | 是 | — | 书籍 ID |
| `reviewListType` | int | 否 | `0` | 0=全部, 1=推荐, 2=不行, 3=最新, 4=一般 |
| `count` | int | 否 | `20` | 每页数量 |
| `maxIdx` | int | 否 | `0` | 翻页偏移 |
| `synckey` | int | 否 | `0` | 翻页游标 |

**返回字段**：`synckey`、`reviews`、`reviewsHasMore`、`reviewsHas5Star`、`reviewsHas1Star`、
`reviewsHasRecent`、`reviewsCnt`、`recentTotalCnt`、`friendCommentCount`、`friendUniqueCount`、
`friendCommentUsers`、`deepVRecommendInfo`、`deepVRecommendValue`、`deepVUniqueCount`。

### 15. `/review/single`
获取单条想法/评论的详情（含评论列表和点赞信息）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `reviewId` | string | 是 | — | 想法/评论 ID |
| `commentsCount` | int | 否 | `10` | 拉取评论数量 |
| `commentsDirection` | int | 否 | `1` | 评论排序：0=倒序, 1=正序 |
| `likesCount` | int | 否 | `10` | 拉取点赞数量 |
| `likesDirection` | int | 否 | `0` | 点赞排序：0=倒序 |
| `synckey` | int | 否 | `0` | 增量同步 key |

**返回字段**：`reviewId`、`review`、`synckey`、`htmlContent`、`bookReviewCount`。

---

## 五、搜索与发现

### 16. `/store/search`
搜索书籍/作者/书单/听书/公众号/文章/全文等（通过 `scope` 切换 tab）。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `keyword` | string | 是 | — | 搜索关键词 |
| `scope` | int | 否 | `10` | 0=全部, 10=电子书, 14=微信听书, 6=作者, 12=全文, 13=书单, 2=公众号, 4=文章 |
| `maxIdx` | int | 否 | `0` | 翻页偏移 |
| `count` | int | 否 | （服务端默认 15） | 每页数量 |

**返回字段**：`sid`、`hasMore`、`results`。

### 17. `/discover/interact/type3`
获取发现页朋友在读动态，返回转换后的 type=3 书籍卡片，按 `updateTime` 从新到旧排序。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|------|------|
| `count` | int | 否 | `20` | 最终返回数量 |
| `maxIdx` | int | 否 | — | 翻页游标，传上一页回包中的 `nextMaxIdx` |
| `synckey` | int | 否 | — | 同步游标，首次不传；刷新可传上一页回包中的 `synckey` |

**返回字段**：`synckey`、`count`、`hasMore`、`nextMaxIdx`、`items`。
