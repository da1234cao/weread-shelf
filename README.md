# weread-shelf · 微信读书数据看板

在国内，微信读书是最好的读书软件。只要它不倒闭，只要我能吃上饭，我每年续费。

但是，之前，微信读书是一套相对封闭的系统。我们无法向外部展示自己的书架，读书笔记。

现在，微信读书官方提供了 skill，以便接入外部的 AI 。

本项目由 AI 编码生成，用于每天自动拉取自己的微信读书数据，存进本地 SQLite，并以一个自托管的 Web 看板展示阅读时长曲线、书架、笔记/划线、个性化推荐。


## 数据来源：官方 Agent API Gateway

所有数据都来自官方 skill 背后的 HTTP 网关，使用一个**长期有效**、自动绑定账号的 API Key。

见: [微信读书 Skill](https://weread.qq.com/r/weread-skills)

## 快速开始（容器部署）

镜像已发布到 GitHub Container Registry：`ghcr.io/da1234cao/weread-shelf`（打 tag 时由 GitHub Actions 自动构建）。

任选下面一种方式启动，启动后浏览器访问 http://localhost:8765 。

### 方式一：docker run

```bash
docker run -d \
  --name weread-shelf \
  --restart unless-stopped \
  -p 8765:8765 \
  -v weread_db_data:/data \
  ghcr.io/da1234cao/weread-shelf:latest
```

### 方式二：docker compose

新建 `docker-compose.yml`：

```yaml
services:
  weread-shelf:
    image: ghcr.io/da1234cao/weread-shelf:latest
    container_name: weread-shelf
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      - weread_db_data:/data

volumes:
  weread_db_data:
    name: weread_db_data
```

然后运行 `docker compose up -d`。

首次启动后：

1. 打开 `http://localhost:8765/admin`，用默认账号 **`admin` / `admin`** 登录。
2. 系统会**强制你先修改用户名和密码**。
3. 登录 https://weread.qq.com/r/weread-skills 生成 `wrk-` 开头的 API Key，在管理页「网关连接」里填入（保存时会试调网关校验）。
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

## License

[MIT](LICENSE)

