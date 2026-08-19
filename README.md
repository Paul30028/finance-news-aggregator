# 即时财经新闻爬取与聚合软件（Finance News Aggregator）

一款合规优先的财经新闻聚合工具：从多个**公开、免费、无需登录**的新闻源（优先 RSS/Atom，网页抓取仅作补充）
异步低频抓取，经过清洗、去重、关键词分类后存入 SQLite，并提供 Web 查看界面与可选的 Webhook/Telegram 推送。

> ⚠️ **合规声明**：本项目默认只抓取公开 RSS/Atom 源，严格限速并遵守 robots.txt。
> 若你自行添加数据源，请务必确认该源允许自动化抓取、不涉及登录墙/付费墙，并遵守其服务条款。
> `config/sources.yaml` 中内置的示例源仅供参考，使用前请自行核实其当前可用性与合规性。

---

## 一、整体架构

```
┌─────────────┐      ┌──────────────────┐      ┌───────────────────┐
│  sources.yaml│ ---> │   CrawlEngine     │ ---> │  processing 清洗层  │
│ (数据源配置)  │      │ (调度并发/限速/重试) │      │ 清洗/去重/分类       │
└─────────────┘      └──────────────────┘      └─────────┬──────────┘
                              │  robots.txt 校验                   │
                              │  per-domain 限速                   ▼
                      ┌──────────────────┐            ┌───────────────────┐
                      │  RSS / HTML 解析  │            │  SQLite (Article)  │
                      └──────────────────┘            └─────────┬──────────┘
                                                                 │
                        ┌───────────────┐        ┌───────────────────────┐
                        │ Webhook/TG 推送│ <----- │ FastAPI Web 界面/API   │
                        └───────────────┘        └───────────────────────┘
```

核心设计原则：

1. **合规优先**：单域名最小请求间隔可配置，严格遵守 `robots.txt`，
   遇到 `429/403` 自动指数退避降速，绝不"硬抗"目标站点的限流。
2. **RSS 优先**：`type=rss` 的源直接用 `feedparser` 解析标准 Feed；`type=html` 仅作为补充，
   通过可配置的 CSS 选择器做通用的"标题+链接"抽取，不针对特定站点写死解析逻辑。
3. **异步高并发但受控**：基于 `httpx.AsyncClient` + `asyncio`，跨源并发由信号量控制，
   同域名请求由独立的限速器串行化。
4. **单源故障不影响整体**：任意一个源抓取失败（网络错误、robots 禁止、解析异常）都会被捕获、
   记录到 `source_stats` 表，其余源正常继续。
5. **即时性**：每个源拥有独立的抓取循环（互不等待），抓到新文章立即入库、立即通过 SSE
   推送到所有打开着的浏览器、立即触发 Webhook/Telegram——不存在"等整轮抓取结束才统一报出"
   的延迟。详见下方"即时性设计"一节。
6. **分析透明可审计**：`/insights` 策略简报页对新闻做的是公开、可读的关键词规则匹配统计，
   不是黑箱模型推理，每一条统计都能追溯到具体文章和具体命中的关键词。详见下方
   "策略简报与信号分析"一节，**该功能不构成投资建议**。

## 二、目录结构

```
finance-news-aggregator/
├── app/
│   ├── main.py                # FastAPI 应用入口（生命周期管理、路由挂载）
│   ├── config.py               # 配置加载（config.yaml + sources.yaml）
│   ├── logging_conf.py         # 日志配置（控制台 + 滚动文件）
│   ├── scheduler.py            # 周期抓取调度器
│   ├── crawler/
│   │   ├── http_client.py      # 合规 HTTP 封装：robots 校验 + 限速 + 重试
│   │   ├── robots.py           # robots.txt 解析与缓存
│   │   ├── rate_limiter.py     # 按域名限速 + 429/403 自动降速
│   │   ├── rss_parser.py       # RSS/Atom 解析（feedparser）
│   │   ├── html_fallback.py    # 网页兜底抓取（BeautifulSoup）
│   │   ├── dedup.py            # 标题+链接哈希去重
│   │   └── engine.py           # 抓取引擎，编排上述所有组件
│   ├── processing/
│   │   ├── cleaner.py          # 内容清洗（去HTML、截断摘要、屏蔽词过滤）
│   │   ├── classifier.py       # 关键词分类
│   │   └── signals.py          # 关键词信号提取（利好/利空事件识别 + 情绪打分）
│   ├── analysis/
│   │   └── briefing.py         # 策略简报聚合（窗口内信号/分类统计，供 /insights 使用）
│   ├── storage/
│   │   ├── models.py           # SQLAlchemy ORM 模型
│   │   ├── db.py                # 异步引擎/会话
│   │   └── repository.py       # 数据访问层（含并发安全的去重插入）
│   ├── notify/
│   │   └── dispatcher.py       # Webhook / Telegram 推送
│   └── web/
│       ├── routes.py           # HTML 页面 + JSON API（含 /insights 策略简报）
│       ├── schemas.py          # Pydantic 请求模型
│       ├── templates/          # Jinja2 模板（原生 HTML + 少量 JS 轮询刷新）
│       └── static/              # CSS / JS
├── config/
│   ├── config.yaml              # 主配置（抓取参数、分类关键词、通知开关等）
│   └── sources.yaml             # 数据源列表
├── tests/                       # 单元测试（去重/分类/清洗，均无网络依赖）
├── scripts/run.py               # 本地开发启动脚本
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 三、快速开始

### 方式一：本地 Python 运行

```bash
cd finance-news-aggregator
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # 含测试依赖；仅运行则用 requirements.txt

cp .env.example .env   # 按需填写 Webhook/Telegram 变量（可留空）

python scripts/run.py
# 或：uvicorn app.main:app --reload
```

访问 http://localhost:8000 查看新闻列表，http://localhost:8000/sources 管理数据源。

运行单元测试：

```bash
pytest
```

### 方式二：Docker 一键部署

```bash
cd finance-news-aggregator
cp .env.example .env   # docker-compose 会读取该文件，必须存在（可留空）

docker compose up -d --build
```

- `./data` 挂载为容器内 `/srv/app/data`：SQLite 数据库与日志会持久化在宿主机。
- `./config` 挂载为容器内 `/srv/app/config`：可直接在宿主机编辑 `sources.yaml`，
  编辑后调用 `curl -X POST http://localhost:8000/api/reload` 热加载，无需重启容器。

停止服务：`docker compose down`

## 四、如何添加新的数据源

**方式 A：编辑配置文件（推荐用于批量/长期源）**

编辑 `config/sources.yaml`，新增一项：

```yaml
- name: "某财经网 - RSS"
  type: rss
  url: "https://example.com/rss/finance.xml"
  category_hint: "宏观"    # 可选
  tier: mainstream         # 可选：official / mainstream / aggregator
  enabled: true
```

保存后调用 `POST /api/reload`（或重启进程）即可生效。

**方式 B：通过 Web 界面 / API 动态添加（无需重启）**

访问 `/sources` 页面填写表单，或直接调用：

```bash
curl -X POST http://localhost:8000/api/sources \
  -H "Content-Type: application/json" \
  -d '{
        "name": "某财经网 - RSS",
        "type": "rss",
        "url": "https://example.com/rss/finance.xml",
        "enabled": true,
        "category_hint": "宏观",
        "interval_seconds": 30,
        "tier": "mainstream"
      }'
```

新源保存后会立即拥有自己独立的抓取循环并马上抓一次（见"即时性设计"一节），
不需要重启进程，也不用等下一个全局周期。`interval_seconds` 可省略，省略则使用全局默认值。

删除源：`DELETE /api/sources/{name}`；启停切换：`POST /api/sources/{name}/toggle`。
这些操作都会写回 `config/sources.yaml`，保证重启后配置不丢失。

**多源与权威度分级（`tier`）**

单一信息源既有覆盖面局限，也有被误导/滞后的风险，因此本项目默认就配置了多个不同类型的源，
并给每个源标注权威度分级，前端可按分级筛选、每条新闻也会显示对应徽标：

| tier 值 | 含义 | `config/sources.yaml` 中的默认示例 |
|---|---|---|
| `official` | 官方权威源：监管机构、央行等一手信息发布方，权威性最高 | 美联储新闻发布、SEC 新闻稿（默认关闭，需先验证 URL） |
| `mainstream` | 主流财经媒体：有采编团队、长期信誉的新闻机构 | BBC Business、Yahoo Finance、MarketWatch、CNBC、WSJ Markets |
| `aggregator` | 聚合/补充源：搜索式聚合结果，可能转载自其他媒体，权威性弱于一手来源 | Google News 财经搜索 |
| 不填 | 未分级 | - |

`tier` 纯粹是展示/筛选元数据，不参与抓取逻辑、也不影响去重与分类。新闻页的"权威度"筛选框
和每条新闻左侧的徽标都基于这个字段。**默认关闭的官方源（如美联储/SEC）标注了
"⚠️ 请先验证"**：官方机构网站改版可能导致 RSS 地址失效，请先在浏览器中打开确认可访问、
内容符合预期后再启用——这也是为什么它们默认是 `enabled: false` 而不是直接上线。

**新增网页兜底源（`type: html`）时**，必须提供 `list_selector`（CSS 选择器，定位列表页中
每条新闻的 `<a>` 标签），例如 `list_selector: "a.article-title"`。添加前请：

1. 确认该站点没有提供 RSS（网页抓取只是最后手段）；
2. 检查其 `robots.txt` 是否允许抓取目标路径（本项目会自动检查并跳过被禁止的 URL，
   但建议提前人工确认，避免误判）；
3. 确认列表页/详情页不需要登录、不是付费墙内容。

## 五、即时性设计

财经新闻的价值很大程度上取决于"多快看到"，因此本项目在多个层面专门做了即时性优化，
同时不牺牲第九节所述的合规底线：

1. **按源独立调度**（`app/scheduler.py`）：不再是"所有源排队跑完一整轮再一起休眠"，
   每个源各自有一条抓取循环，抓完立刻按自己的 `interval_seconds`（未设置则用全局
   `crawler.interval_seconds`，默认 60 秒）休眠、再抓。慢源/大源不会拖慢其他源。
   突发新闻类的源可以在 `sources.yaml` 中单独设置更短的 `interval_seconds`
   （参见配置文件中的 `MarketWatch - Top Stories` 示例，设为 30 秒）。
2. **抓到即报，不等整轮结束**（`CrawlEngine.run_source_once`）：一个源产生新文章后，
   立即写入数据库、立即通过 SSE 广播给前端、立即触发 Webhook/Telegram 推送，
   全部并发进行，互不等待。
3. **SSE 实时推送**（`/events/stream`）：新闻页面通过浏览器原生 `EventSource` 订阅，
   新文章几乎零延迟地推送到页面（首页右上角有"实时推送中"状态指示，断线会自动重连）；
   30 秒轮询仅作为 SSE 异常时的兜底，不是主要的更新机制。
4. **动态源立即生效**：通过 `/sources` 页面或 API 新增/启用一个源后，调度器会立刻
   为它创建独立循环并马上抓一次，不需要重启，也不用等下一个全局周期。
5. **HTTP 条件请求（ETag / If-Modified-Since）**：更短的轮询间隔如果每次都完整下载
   Feed，会给源站增加不必要的负担。因此每次请求都会带上上一次响应的 `ETag`/
   `Last-Modified`；若内容未变化，源站按标准会返回 `304 Not Modified`（几乎零字节），
   本项目据此跳过解析，既保证了"该快的时候快"，也保证了"不该抓的时候不多抓"。
6. **"刚刚 / N 分钟前 + NEW 徽标"**：列表按 `published_at` 展示相对时间，
   `fetched_at` 在 5 分钟内的文章额外标红显示 `NEW` 徽标，直观呈现新鲜度。

## 六、策略简报与信号分析

> ⚠️ **免责声明**：本节描述的功能是对已抓取公开新闻做**关键词规则匹配**与**结果统计**，
> 全部逻辑都在 `app/processing/signals.py`（规则表）和 `app/analysis/briefing.py`（聚合）
> 中，可直接阅读源码核实。它不是情感分析模型、不做语义理解、不预测走势，**输出结果不构成
> 任何投资建议**，仅作为快速浏览大量新闻时的辅助整理工具。据此做出的任何投资决策，风险自负。

**信号词库**（`app/processing/signals.py` 中的 `SIGNAL_RULES`）把常见财经事件分成 13
类，每类标注一个极性（利好 `+1` / 利空 `-1` / 中性 `0`，中性代表"方向不确定，需结合上下文"，
如并购、IPO）：

| 信号 | 极性 | 示例关键词 |
|---|---|---|
| 货币宽松/降息 | + | 降息、降准、rate cut |
| 货币紧缩/加息 | − | 加息、rate hike |
| 业绩超预期 | + | 业绩超预期、beats estimates |
| 业绩不及预期 | − | 业绩预警、misses estimates |
| 评级/目标价上调 | + | 上调评级、upgrades |
| 评级/目标价下调 | − | 下调评级、downgrades |
| 回购/增持/分红 | + | 回购、share buyback |
| 减持/抛售 | − | 减持、stake sale |
| 并购重组 | 0 | 收购、merger |
| 监管/合规风险 | − | 调查、处罚、investigation |
| 违约/破产风险 | − | 违约、bankruptcy |
| IPO/新上市 | 0 | IPO、goes public |
| 供给端变化 | 0 | 减产、supply cut |

每篇文章抓取时会自动匹配这份规则表，命中的信号类型（去重后）汇总出一个整数"情绪分"
（极性之和），随文章一起存入数据库；新闻卡片上会显示对应的信号标签（如"▲ 货币宽松/降息"）。

**"建议关注"——从"检测到什么"到"接下来该核实什么"**：每类信号在
`app/processing/signals.py` 的 `ACTION_HINTS` 中都配有一句"建议关注"文字，例如"降息"对应
"后续关注同期 CPI/PMI 等数据是否印证宽松基调，以及权益/债券市场的实际反应"。命中信号越多的
文章，`conclusion_for_codes()` 给出的"信号强度"描述也会从"单一信号"升级到"双重信号叠加"
直至"多重信号叠加，建议优先关注"——**这只是"多少条独立规则同时给出了提示"的客观计数，
不是对走势的预测或确信度**。这些内容会显示在新闻卡片、`/insights` 页面和 API 返回中。

**`/insights` 策略简报页**基于这些数据在选定时间窗口（6小时/24小时/3天/7天）内做聚合：

- 窗口内新闻总数、偏利好/偏利空/中性数量
- **🔔 本窗口重点信号**：情绪分绝对值达到 `signals.alert_threshold`（默认 2）门槛的新闻，
  即多个规则同时命中、强度较高的条目，附带各自的"建议关注"文字
- 情绪分最高（偏利好）与最低（偏利空）的新闻排行
- 热门分类排行（哪个板块新闻最多）
- 信号词命中排行（哪类事件本轮窗口内出现最频繁）

也可以调用 `GET /api/insights?window=24` 获取同样的数据（JSON 格式，含 `disclaimer` 字段）。

**重点信号推送**：新文章一入库就会判断是否达到 `alert_threshold`：
- **Web 端**：达标文章通过 SSE 立即推送，页面右上角弹出可关闭的"🔔 检测到重点信号"悬浮提示
  （20 秒后自动消失），并附最多 3 条标题的直达链接；新闻卡片本身也会显示"🔔 重点信号"徽标。
- **Webhook/Telegram**（可选）：若已启用对应通道且 `signals.push_alerts: true`，除常规的
  "新文章"推送外，还会为达标信号额外发一条独立提醒（`app/notify/dispatcher.py` 中的
  `dispatch_signal_alerts`），内容包含命中信号、建议关注文字与免责声明，方便和普通新闻流分开处理。

**如何扩展规则**：直接编辑 `app/processing/signals.py` 中的 `SIGNAL_RULES`（关键词与极性）
和 `ACTION_HINTS`（建议关注文字）即可，无需改动其他代码——`/insights` 页面、API 与推送都会
自动反映新规则。`config/config.yaml` 中的 `signals.alert_threshold` 控制"多强才算重点"。

## 七、配置说明（`config/config.yaml`）

| 配置项 | 说明 |
|---|---|
| `crawler.interval_seconds` | 全局抓取轮询间隔，默认 60 秒；单个源可在 `sources.yaml` 中用 `interval_seconds` 覆盖 |
| `crawler.max_concurrency` | 跨源最大并发数 |
| `crawler.per_domain_min_interval_seconds` | **同一域名**两次请求最小间隔（核心合规参数，与轮询间隔是两回事） |
| `crawler.max_retries` / `retry_backoff_base_seconds` | 网络错误/5xx 重试次数与退避基数 |
| `crawler.throttle_on_429_403` | 命中 429/403 时的降速惩罚策略（初始值/上限/倍数） |
| `crawler.respect_robots_txt` | 是否遵守 robots.txt（生产环境请保持 `true`） |
| `crawler.summary_max_length` | 摘要截断长度 |
| `storage.database_url` | SQLAlchemy 异步 DSN，默认 SQLite，可替换为 PostgreSQL |
| `classification.categories` | 分类关键词表，可自由增删分类/关键词 |
| `classification.block_keywords` | 全局屏蔽词，命中则丢弃该条新闻 |
| `notify.webhook` / `notify.telegram` | 推送开关，实际密钥通过环境变量注入（见 `.env.example`） |

## 八、扩展到 PostgreSQL

只需修改 `config/config.yaml`：

```yaml
storage:
  database_url: "postgresql+asyncpg://user:pass@host:5432/dbname"
```

并将 `psycopg`/`asyncpg` 加入 `requirements.txt`。ORM 模型（`app/storage/models.py`）未使用
任何 SQLite 专属方言特性，无需修改代码。

## 九、合规性设计要点

- **优先 RSS**：RSS/Atom 是网站主动公开、明确用于聚合分发的数据格式，抓取合规风险最低。
- **轮询更快但不等于更粗暴**：全局默认 60 秒一轮，但真正约束"礼貌程度"的是同域名请求间隔
  （默认 3 秒）与 robots.txt 中的 `Crawl-delay`；同时每次请求都带条件请求头
  （ETag/If-Modified-Since），内容未变化时源站只需返回 304，不产生实质抓取负载。
- **遵守 robots.txt**：抓取前逐域名检查并缓存 `robots.txt`，尊重其中的 `Disallow` 与 `Crawl-delay`。
- **如实 User-Agent**：`User-Agent` 中标注了软件名称与联系方式，不伪装成浏览器绕过限制。
- **429/403 自动降速**：命中后对该域名施加指数增长的降速惩罚，而不是持续重试硬抗。
- **不抓取登录/付费内容**：设计上仅面向公开可访问的 RSS 与网页，不包含任何登录态模拟、
  Cookie 注入或付费墙绕过逻辑。
- **只存摘要与原文链接**：不整篇存储/展示版权内容，摘要经截断处理，用户需点击"查看原文"
  跳转到源站阅读完整内容。

## 十、后续可扩展方向

- 接入更多推送渠道（企业微信、飞书机器人等），只需在 `app/notify/` 下新增模块并在
  `dispatcher.py` 中注册。
- 引入更精细的正文抽取（如 `readability-lxml`）替换 `html_fallback.py` 中的轻量实现。
- 分类升级为轻量文本分类模型，替换当前的关键词打分规则。
- 抓取统计接入 Prometheus，便于监控各源健康状况与新增文章趋势。
