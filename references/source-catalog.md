# 数据源目录

状态：第 1 阶段技术审计与实现记录
最后修订：2026-09-06

## 1. 目的

本文记录每个候选数据源在本项目中的用途、官方说明、实际可获得字段、访问条件、当前实测结果和降级策略。

本项目需要解决的不是单一“抓论文”问题，而是三种不同的数据需求：

1. **发现**：找到最近出现或变化的论文记录；
2. **新旧核验**：确认作品确实在监测窗口内首次公开或正式发表；
3. **分类核验**：取得可与用户兴趣集合执行交集的 PhilPapers 受控分类。

一个来源可以承担其中一种或多种功能，但不得因为某来源能发现记录，就假定它也能提供可靠发表日期或细粒度分类。

## 2. 状态值

- `confirmed`：官方说明与本机实测均支持预定用途；
- `provisional`：当前可访问，但接口、许可或字段仍需确认；
- `blocked`：当前访问条件不满足；
- `not_suitable`：可以访问，但字段语义不适合该用途；
- `deferred`：第一版暂缓；
- `untested`：尚未完成本机实测。

## 3. PhilPapers taxonomy

### 预定用途

- 把用户研究方向映射为真实存在的受控分类；
- 取得分类 ID、规范名称、父分类和主要父分类；
- 构建父子关系与后代分类展开；
- 检测分类改名、移动、合并或删除。

### 官方说明

PhilPapers 提供完整 taxonomy 的 JSON feed。官方文档列出的地址形式为：

```text
https://philpapers.org/philpapers/raw/categories.json?apiId=[API_ID]&apiKey=[API_KEY]
```

分类记录包括：

- 分类名称；
- 分类 ID；
- 父分类 ID 列表；
- 主要父分类 ID。

该 feed 需要 API ID 与 API key。参考：[PhilPapers JSON API](https://philpapers.org/help/api/json.html)。

### taxonomy 性质

PhilPapers 官方把它描述为五层层级体系。一个分类可以有多个父分类，并有一个主要父分类。论文细粒度分类工具允许一条记录进入最多三个分类。参考：[PhilPapers Categorization Project](https://philpapers.org/help/categorization.html)。

### 当前状态

`confirmed`（浏览器取得官方响应后，由本地验证器导入）

2026-09-05 已从官方 taxonomy 地址取得真实 JSON，并通过 `pfm taxonomy-import` 使用与直接网络
抓取相同的字段、数量、父子图和安全验证链生成完整快照。命令行直接认证请求仍会在 JSON API
响应前受到 Cloudflare challenge，因此“直接无人值守更新 taxonomy”仍是受限能力；这不影响当前
已验证快照供本地匹配使用。不得要求用户在聊天中粘贴凭据。

### 2026-09-05 未认证探测与实现状态

- 对固定 taxonomy 地址进行一次不带凭据的低频探测，返回 HTTP 403 和 Cloudflare HTML
  challenge，不是 JSON；这只能证明无凭据路径不可用，不能证明有效凭据也会失败；
- 取得凭据后进行的真实认证请求同样返回 HTTP 403；响应头为 `server: cloudflare`、
  `cf-mitigated: challenge`，正文有 Cloudflare 标记，没有无效 API key 或登录标记。这说明当前
  阻塞位于站点防护层，但仍不能单凭这一响应证明服务器已经核验并接受了凭据；
- `pfm taxonomy-fetch` 已实现固定 HTTPS 地址、禁止重定向、30 秒超时、32 MiB 响应上限、
  JSON content type、UTF-8 JSON 和完整分类图校验；
- 分类数少于 2000 时按疑似残缺响应拒绝。这个下限来自 PhilPapers 对“2000 多个叶分类”的
  官方描述，用作完整性防线，不是对分类体系规模的永久断言；
- 官方文档只列出四个字段的语义和顺序，没有给出可公开访问的真实响应示例。真实响应已确认是
  四元素记录列表，共 6154 条来源记录；规范化后有 6153 个可用分类；
- 来源中有一个名称为空、没有子项且不被任何记录引用的叶记录（ID `510734`）。它不被猜测命名，
  而是从可匹配分类中排除，并连同原因保留在 `excluded_source_records` 审计元数据中；
- 来源中有三个重名分类组：`Many-Valued Logic`、`Medieval Ethics`、`Pan-Africanism`。名称不是唯一键；
  精确名称查询若得到多个 ID 必须要求消歧，不能任取一个；
- 官方说明根分类 ID `1` 不包含在 feed 中。规范化器删除指向该省略根节点的父引用，但保留
  `omitted_root_id` 元数据；其他缺失父 ID 一律使快照失败；
- 只有完整校验通过的规范化快照才会原子化写入 `var/taxonomy/`。原始认证响应不落盘，快照
  只保留原始响应 SHA-256，不保存带查询参数的 URL。
- 已增加 `pfm taxonomy-import`：用户可以在正常浏览器会话中保存同一官方 JSON，再通过完全
  相同的数量、字段和分类图闸门导入。它不降低验证标准，也不会把 HTML challenge 当作 JSON。
- 已增加 `pfm taxonomy-audit`：比较两个已验证快照的新增、删除、改名、父集合、主要父分类和
  活动状态，重新计算私人兴趣展开与 feed 覆盖，并输出 `ready`、`review_required` 或 `blocked`；
  命令不修改配置，也不根据同名或近似名称自动迁移 ID；
- 规范化快照使用取得时刻与来源哈希命名，旧文件不因新导入被覆盖；同 ID 同内容重复导入保持
  幂等，同 ID 异内容冲突失败并保留原文件。历史画像可以按 snapshot ID 重新定位同目录旧快照。

### 真实快照验收记录

- 快照 ID：`philpapers:20260905T130303Z:e2538657e2ce`；
- 来源记录数：6154；可用分类数：6153；
- 来源字节 SHA-256：`e2538657e2ce1ad7a898ba56ff7db8cfd225a524a94f5d6ddb55724a41499006`；
- `complete: true`、`fixture: false`；
- 缺失父分类：0；非法主要父分类：0；分类图循环：0；
- 官方省略的根 ID `1` 只作为 `omitted_root_id` 保存，不伪造来源记录。

### 离线开发策略

在离线测试或公开仓库演示中，可以使用少量经 PhilPapers 官方分类页人工核验的固定测试分类开发数据结构，例如：

- `Plato`；
- `Plato: Epistemology`；
- `Plato: Knowledge and Belief`；
- `Plato: Theaetetus`；
- `Plato: Parmenides`；
- `Epistemology`。

当前离线夹具已从各分类公开页面核验分类名称、页面 URL 和数字 `cId`，但只包含九个分类，仍不能代表完整 taxonomy。它只供测试；正式运行使用上述已验证的私人完整快照。不能用页面 slug、测试集合或模型生成 ID 冒充全量官方 taxonomy。

## 4. PhilPapers 分类页

### 预定用途

- 核验分类名称是否实际存在；
- 核验一篇论文是否显示在某个分类中；
- 观察分类页面提供的新增项目监测能力；
- 在正式 API 可用前帮助建立最小测试夹具。

### 已核验页面

- [Plato](https://philpapers.org/browse/plato)
- [Plato: Epistemology](https://philpapers.org/browse/plato-epistemology)
- [Epistemology](https://philpapers.org/browse/epistemology)
- [Plato: Knowledge and Belief](https://philpapers.org/browse/plato-knowledge-and-belief)
- [Plato: Theaetetus](https://philpapers.org/browse/plato-theaetetus)
- [Plato: Parmenides](https://philpapers.org/browse/plato-parmenides)
- [Parmenides](https://philpapers.org/browse/parmenides)

### 官方监测功能

PhilPapers 官方说明：用户可以在满足任意搜索或筛选条件的页面上建立 RSS feed 或电子邮件提醒，以看到该页面的新材料。参考：

- [Introduction to PhilPapers](https://philpapers.org/help/whatyoucando.html)
- [PhilPapers FAQ: RSS feeds](https://philpapers.org/help/faq.html)

### 2026-09-05 本机实测

- 多个公开分类页返回 HTTP 200，并显示 `Monitor this page` 与 `RSS feed` 控件；
- `Plato: Theaetetus` 页面的 `allparams` 表单给出 `cId=74924`、`catId=74924` 和 `cn=plato-theaetetus`；
- RSS 控件会把两个辅助字段改名为 `noheader=1` 与 `__action=<原页面路径>`，然后以当前表单参数请求 `/utils/feed.pl`；
- `/utils/feed.pl` 首先返回 HTML 生成页，不直接返回 XML；生成页包含带 `dg` 令牌与 `format=rss` 的二级 RSS 链接；
- 请求二级链接返回有效的 RSS 1.0／RDF XML；2026-09-05 实测解析出 304 条 `Plato: Theaetetus` 分类记录；
- RSS 条目含题名、PhilPapers 稳定记录链接和书目描述，但实测条目没有可直接信任的 `pubDate`／`dc:date`；
- `/rec/...` 记录页的无浏览器 HTTP 请求可能触发 Cloudflare 403，因此程序不能依赖高频抓取记录页补齐日期。

### 当前状态

`confirmed`（用于分类页成员关系与 feed 发现）；`not_suitable`（单独用于本周发表日期核验）

### 实现限制

- 解析器按表单 `id`、字段 `name` 和 RSS 生成链接参数读取，不按屏幕位置或 CSS 排列猜测；
- 已保存最小表单与 RSS XML 夹具，但真实响应随分类内容变化，不把完整第三方响应提交到仓库；
- 必须检测登录页、错误页、验证码和限速页；
- 必须确认使用条款是否允许项目的预定自动访问频率；
- 每周只对用户实际选中的分类生成和读取官方 feed，使用超时、诚实 User-Agent 和本地状态减少重复请求；
- 仍需做跨周稳定性观察；一次成功实测不能证明 PhilPapers 永远不会修改表单或令牌机制。

## 5. PhilPapers recent 页面

### 预定用途

- 发现 PhilPapers 当前列出的新书与新论文；
- 观察新条目是否已经具有分类；
- 作为官方 RSS／页面监测方案的候选入口。

### 官方地址

[Recent books and articles](https://philpapers.org/recent)

### 2026-09-05 本机实测

- 页面返回 HTTP 200；
- 页面包含书籍、期刊论文、手稿等多种记录类型；
- 部分最新记录显示 `No categories`；
- 页面提供 `Monitor this page → RSS feed`；
- 页面说明某些筛选和“我的领域”功能需要登录；
- 页面展示顺序或“new items”含义尚需与正式发表日期分开核验。

### 当前状态

`provisional`

### 关键限制

出现在 recent 页面或当前 RSS 提醒流可以证明条目近期进入 PhilPapers 的新材料流，但不能自动
证明作品本周正式发表。程序先检查书目年份、共同标识符和同一作品的更早版本；没有发现旧作证据
时，可以形成 `confirmed_source_arrival`，并在报告中明确使用“新近进入 PhilPapers 提醒流”而非
“正式发表”的措辞。

## 6. PhilPapers article feed

### 预定用途

- 批量取得有限范围的论文书目数据与分类信息；
- 避免依赖网页布局。

### 官方说明

PhilPapers 表示可以在特定条件下向第三方提供有限子集的 article JSON feed，需要联系 PhilPapers。数据再分发受到条款限制。参考：

- [PhilPapers JSON API](https://philpapers.org/help/api/json.html)
- [PhilPapers API overview](https://philpapers.org/help/api)

### 当前状态

`blocked`

原因：尚未取得 article feed 访问许可和数据契约。

### 结论

第一版架构不能假设无密钥公开 article API 已经存在。取得访问前，以接口抽象和离线夹具开发，不硬编码未经验证的响应结构。

## 7. PhilPapers 官方 OAI

### 官方地址

```text
https://api.philpapers.org/oai.pl
```

官方说明 OAI-PMH 只包含开放获取内容。参考：[PhilPapers OAI-PMH](https://philpapers.org/help/oai.html)。

### 2026-09-05 本机实测

请求 `Identify` 和 `ListRecords` 均返回 HTTP 403，响应说明数据查询需要有效 API key。

### 当前状态

`blocked`

### 结论

不把官方 OAI 当作当前无密钥可用来源。取得官方访问方式前，不用关闭 TLS、伪造浏览器或其他规避方式绕过。

## 8. PhilArchive OAI 兼容端点

### 实测地址

```text
https://philarchive.org/oai.pl
```

### 2026-09-05 本机实测

使用能够识别项目的 User-Agent 请求：

```text
ListRecords?metadataPrefix=oai_dc&from=2026-09-04
```

结果：

- HTTP 200；
- `Content-Type: text/xml`；
- 第一响应页包含 999 条记录；
- 响应包含 `resumptionToken`，必须实现分页；
- 样本含出版年份 2014、2015、2018 的旧作品；
- OAI header `datestamp` 为 2026-09-04，说明它表示记录近期变化，而不是作品近期发表；
- 999 条记录的 `dc:subject` 均只有 `Philosophy`；
- OAI header 没有 `setSpec`；
- 因而该响应不提供可用于 PhilPapers 细粒度分类交集的标签。

### 当前状态

- 作为记录发现源：`provisional`；
- 作为新论文日期源：`not_suitable`；
- 作为细粒度分类源：`not_suitable`。

### 结论

该端点可以帮助发现发生变化的开放记录，但每条记录必须另行核验首次公开／正式发表日期和 PhilPapers 分类。绝不能直接把 OAI 最近 `datestamp` 结果作为本周新论文推送。

## 9. OpenAlex

### 预定用途

- 发现候选论文；
- 补充 DOI、作者、作品类型、来源与出版日期；
- 在取得 PhilPapers 分类之前提供跨来源身份核验。

### 2026-09-05 本机实测

Python 3.12 请求 OpenAlex Works API 返回 HTTP 200 和 `application/json`。

端到端 dry-run 对 PhilPapers 记录 `SCHPOO-4` 的精确题名与首位作者查询未得到唯一 OpenAlex
匹配；程序没有把搜索排序靠前但题名不完全相同的结果当作同一作品。

### 2026-09-06 即时拉取接口复核

- OpenAlex Works API 已实测接受同一 `doi` 过滤字段的管道符 OR 批量值，也接受
  `title.search.exact` 的同字段 OR 值；项目分别以每批 50 个 DOI、20 个规范化题目请求，并把
  `per_page` 设为 100；
- 批量题目结果仍在本地按分层作品同一性规则复核：共同标识符最强，其后是作者相容与重音、姓名
  顺序、题目实词、词序和高置信度拼写变体；搜索排名不作为身份断言；完全跨语言题名在没有共同
  标识时只形成语义复核候选；
- `publication_date` 是日精度日期；OpenAlex 官方说明通常选择最早电子发表日期，但它仍作为
  `provisional` 来源并接受与 Crossref 冲突时的保守处理；
- 当前 OpenAlex 允许匿名轻量查询，但匿名日额度低于免费账户 key。连续开发测试已真实得到
  HTTP 429；程序将其概括为速率或当日预算耗尽，不回显查询 URL；
- 可选 `OPENALEX_API_KEY` 只从进程环境读取，不进入 watchlist、日志或报告。无 key 时仍可尝试
  匿名首次体验；需要同日重复主动拉取时建议使用免费 key；
- 2026-09-06 使用一组不公开具体兴趣内容的多分类本地配置做即时测试，把 2039 条当前记录按书目
  年份缩小为 345 条候选；DOI 直接链接只覆盖 7 条，因此必须保留批量题目路径。使用免费 key 并
  把单次退回上限提高到 300 后，完整七日运行成功：79 条批量确认、266 条逐篇退回，最终形成 122 个唯一
  书目作品、219 条 unresolved、确认窗口内新作 0、匹配 0；周报状态未改变；
- 独立聚合诊断显示，把批量题名过滤从宽搜索改为 `title.search.exact`，严格候选匹配由 72 提高到
  91，但仍约有 247 条需要逐篇退回。这是有益的小幅优化，不足以消除冷启动成本；即时模式因此
  保留为实验能力，公开 v0.1 的稳定核心仍是建立基线后的每周增量监测。
- 2026-09-06 后续实现不再把 OpenAlex 当作新论文的必要登记机关：查到更早的等价作品时用于排除
  旧文重录；查到窗口内日期时支持 `confirmed_new`；查无记录而非请求失败时，允许 PhilPapers
  当前提醒证据形成 `confirmed_source_arrival`。2026-09-07 09:02 的多分类即时运行在书目来源均可用
  时形成 33 条 `confirmed_source_arrival`，验证了该通道不再等待 OpenAlex／Crossref 收录；其中具体
  题名是否属于支持作品类型仍需继续加强确定性过滤，不把来源到达等同于论文质量评价。
- 2026-09-06 后续连接层重构为：一次完整 feed 加载复用一个 PhilPapers HTTP 连接池；默认逐篇
  退回阶段分别复用一个 Crossref 与一个 OpenAlex 连接池。该修改减少重复 TCP／TLS 建连，不减少
  实际书目查询数、不绕过提供方限额，也不改变任何证据门槛；自动测试固定了客户端复用行为，真实
  网络耗时改善仍待下一次端到端运行测量。

### 2026-09-07 冷启动与额度复核

- 当天 09:02 的真实七日即时拉取出现 345 个候选的典型冷启动：OpenAlex 题名 OR 响应只要
  `meta.count` 超过首屏容量，旧实现就递归重查整个批次，包括已经取得结果的题名；最终 OpenAlex
  达到 130 个逻辑请求。该行为不是 OpenAlex 额度本身不足，而是本地拆分策略放大了请求。
- 修复后先消费首屏结果，只重查首屏没有代表记录的题名，并把拆分深度限制为一层；每个初始 20
  题名批次最多 3 个逻辑请求。默认 1000 个总候选、50 个逐篇候选下，OpenAlex／Crossref 的计划
  硬上界分别为 220／50 个逻辑请求，并由运行统计单独输出；有界重试次数仍另由网络遥测记录。
- 15:21 使用 346 个候选、327 个题名和仅 5 个逐篇远程名额真实复跑：OpenAlex 实际 31 个逻辑
  请求，低于 57 个本轮上界；Crossref 为 5 个，所有请求首次成功，无等待、重试或熔断。
- 随后增加一小时题名批次调度标记。它只以题名散列说明“刚参加过成功批次”，不形成 OpenAlex
  `not_found` 结论；未取得正候选的论文仍进入逐篇轮转。15:31 的首轮写入中，115 个题名命中正
  缓存、212 个题名完成新批次，OpenAlex 实际 17 个逻辑请求。15:33 紧接复跑时，116 个题名命中
  正缓存、211 个命中调度标记、题名批量 API 值为 0；5 个逐篇远程候选中只有 3 个需要 OpenAlex，
  因而 OpenAlex 实际为 3 个逻辑请求，Crossref 为 5 个。两轮都没有失败、重试或等待。
- 逐篇预算同时改为只计算真正需要远程查询的候选；新鲜完整缓存全部重新执行证据门槛但不占名额。
  独立缓存保存 31 天的 PhilPapers 来源 ID 散列与完成尝试时间，使下一次拉取优先处理未尝试／最久
  未尝试的延期候选。三轮各给 5 个远程名额后，轮转表包含 15 个不同散列，证明候选没有反复停在
  同一前五条。传输失败和熔断跳过不写成完成记录；该轮转不读写周报通知或检查点。
- 19:04 的下一次真实复跑在 347 个候选中，于任何 OpenAlex／Crossref 查询之前识别出 3 条题名
  明确标为书评／评论的记录，并按共用作品类型门槛终局排除。最终报告包含 8 条匹配，题名中不再有
  `Review of ...`。该次一小时题名调度标记已经到期，因此 OpenAlex 又执行 30 个逻辑请求；Crossref
  为 5 个，全部请求无失败、重试或等待。这说明调度标记只负责短时去重，而非长期缓存来源未收录
  结论；明确书评的本地排除则独立于外部书目额度生效。

接口语义依据 OpenAlex 官方的 [Filter](https://help.openalex.org/api/filtering/)、
[Authentication](https://help.openalex.org/api/authentication/) 和
[Works attributes](https://help.openalex.org/data/works/attributes/)；其中 OR 单字段上限为 100、
`per_page` 上限为 100，`publication_date` 为 ISO 8601 日精度日期。

### 当前状态

- 基本连通性：`confirmed`；
- 新论文日期语义：`provisional`；
- PhilPapers 分类来源：`not_suitable`。

### 关键限制

OpenAlex topics／concepts 不是 PhilPapers category ID。只有建立明确、经过测试的 crosswalk 后，才能映射为项目受控分类；原始 OpenAlex topic 不能直接与用户 PhilPapers 兴趣集合做 ID 交集。

当前 API 使用按日预算；HTTP 429 既可能表示瞬时速率过高，也可能表示当日额度耗尽。匿名模式不应
被描述为无限免费。官方建议使用每页 100、OR 批量、最小 `select` 字段和可选免费 API key；项目
不得把 key 写入错误 URL、配置或报告。

运行时适配器现在读取 `Retry-After`、`X-RateLimit-Remaining` 与 `X-RateLimit-Reset`：剩余量明确为
0 时报告当日预算耗尽；头部不足时不武断区分速率与预算。短时错误最多三次有界尝试，要求长时间
等待时返回安全重试秒数而不阻塞到次日。详见[重试与熔断政策](source-retry-policy.md)。

连接复用只能减少本地重复建连成本，不能修复 OpenAlex 尚未收录、字段缺失、当天 credits 耗尽或
远程服务暂时不可用。出现这些情况时必须保留“查无记录”与“请求失败”的区别；前者在完成旧作
检查后可以支持 PhilPapers 来源到达路径，后者必须显式计入未完成核验。

## 10. Crossref

### 预定用途

- 核验 DOI；
- 补充期刊、出版方、卷期和日期；
- 帮助排除数据库近期补录的旧作品。

### 2026-09-05 本机实测

Python 3.12 请求 Crossref REST API 返回 HTTP 200 和 `application/json`。

端到端 dry-run 对 PhilPapers 记录 `SCHPOO-4`（*Plato on Object Perception*）取得唯一 DOI
`10.1353/hph.2025.a958785`，但 Crossref 只给出 `2025-04` 月精度的卷期日期。该精度不能
证明论文属于某一周，却足以证明它早于 2026 年 9 月监测窗口，因此程序将其排除为旧作补录，
没有推送，也没有继续放入待日期重试队列。

### 当前状态

- 基本连通性：`confirmed`；
- DOI 与出版元数据核验：`provisional`；
- PhilPapers 分类来源：`not_suitable`。

### 访问要求

请求应在 `User-Agent` 和 `mailto` 中提供可识别的脚本与联系信息。参考：[Crossref REST API access and authentication](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)。

### 关键限制

- Crossref 的记录创建或更新时间不自动等于论文首次发表日期；
- `published-online`、`published-print`、`issued`、`created`、`indexed` 必须分开保存；
- 来源缺失或日期冲突时不能由程序猜测；
- Crossref subjects 不是 PhilPapers taxonomy。

Crossref 官方建议缓存很少变化的结果、提供有效 `mailto`、检查响应状态和限额响应头。当前即时
模式已经增加独立的细粒度查询缓存：逐个 DOI 批量结果与逐篇正记录保存 24 小时，成功的 DOI／
逐篇查无记录保存 15 分钟；题名批量只把有返回的单题名候选保存为书目缓存并在 1 小时后过期，
空题名结果不保存为负书目结论。成功批量调用可以另存不含题名的一小时散列调度标记，防止立即
重发同一批次；未解决候选仍进入逐篇轮转。请求失败与语义复核不缓存；缓存不能压制即时报告与
周报之间允许发生的重复推送。

Crossref 运行时保持串行逐篇查询并对 408、429、选定 5xx 和传输错误有界退避；不硬编码可能变化
的请求池速率。三次尝试后仍失败会在本次运行打开 Crossref 熔断，但不会关闭 OpenAlex 或把失败
写成查无记录。

## 10.1 来源限制与项目责任的边界

可以归属于第三方来源能力的限制包括：

- PhilPapers 分类 RSS 不提供可靠日级条目时间，或者一篇作品尚未进入用户所选分类 feed；
- PhilPapers 自动采集或人工分类尚未完成，题名、作者、分类或出版信息存在延迟／错误；
- PhilPapers OAI-PMH 官方只提供开放获取内容，不能代表完整 PhilPapers 索引；
- OpenAlex／Crossref 尚未登记某篇新稿、只提供低精度日期或返回相互冲突的数据；
- 免费／匿名 API 达到服务方限额，或者远程服务返回 403、429、5xx、TLS／网络错误。

不能归咎于第三方、仍由项目负责的事项包括：

- 没有区分“查无记录”和“请求失败”；
- 把来源到达时间、索引时间或 OAI `datestamp` 写成正式发表日期；
- 在没有完成旧作核验时推送，或者把旧刊重录当作新作；
- 把失败或延期候选隐去后声称“没有相关论文”；
- 缓存过期、键冲突或错误复用造成漏报／误报；
- 本地去重、兴趣集合、调度、状态事务、凭据保护和报告转义错误。

因此，对外说明应采用“来源覆盖与及时性不保证，程序处理过程可审计”的表述，而不是笼统写成
“网站不完善，本项目不负责”。PhilPapers 官方 [FAQ](https://philpapers.org/help/faq.html)说明 RSS
用于查看页面新到材料，并承认自动采集信息可能出现错误；其
[OAI 说明](https://philpapers.org/help/oai.html)明确接口只包含开放获取内容。OpenAlex 官方
[Authentication](https://help.openalex.org/api/authentication/)记录 credits、429、响应限额头和
批量建议；Crossref 官方[2026 限额说明](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137)
建议缓存结果、提供 `mailto` 并检查状态码。

## 11. 第一版来源组合建议

在尚未取得 PhilPapers article feed 访问前，第一版按以下职责组合开发：

```text
PhilPapers 分类页／官方 RSS
    → 发现“新项目流”和分类页面成员关系

Crossref + OpenAlex + 可核验的出版方元数据
    → 核验作品身份与首次公开／正式发表日期

PhilPapers taxonomy snapshot
    → 核验用户兴趣分类和层级关系

确定性集合交集
    → 决定是否进入周报
```

### 优先实现：分类 feed 驱动

分类页官方 RSS 已在本机以当前页面公开流程成功生成和读取，第一版采用：

```text
用户兴趣分类集合
    → 每个分类的官方 RSS
    → 合并 feed 新条目
    → 以 feed 来源分类作为匹配证据
    → 使用 Crossref／出版方等核验真实首次发表日期
    → 去重并生成周报
```

这种方式不要求先取得所有新论文的完整分类列表。它仍然需要处理旧论文后归类、feed 截断、同一论文跨 feed 重复、分类层级展开和来源条款。

如果无法合法、稳定取得论文分类，则第一版不能退回到“模型阅读标题后猜标签”并仍声称完成了 PhilPapers 分类交集。应把记录留在 `awaiting_categories`，或暂时缩小功能声明。

## 12. 后续技术验证

已完成：申请并本地保存 API 凭据、真实 taxonomy 响应契约和图结构审计、真实兴趣集合确认、
正式历史基线、手动 dry-run、第一次正式周报、一次完整七日即时运行、失败时不推进检查点的自动化测试、重复周窗口
保护实测、当地计划时刻缺口检测、连续漏周分窗补齐，以及本机 Codex 每周计划任务创建。

仍需继续：

1. 明确 article feed 的申请条件与允许用途；
2. 对分类 RSS 表单和二级 `dg` 链接做跨周稳定性观察；
3. 选择以后实际出现的多篇 feed 新增记录，以 Crossref、OpenAlex 和出版方元数据交叉核对作品
   身份与出版日期；
4. 为旧刊补录、新预印本、online first、正式卷期版继续补充日期夹具；
5. 持续检查数据源条款、缓存范围和再分发限制；
6. 已完成 mock 网络层的 HTTP 429／403／5xx、`Retry-After`、次日重置、传输失败和运行级熔断
   演练；仍需在不人为冲击服务的条件下观察以后自然发生的真实错误；
7. 观察第一次真实无人值守周一运行，并核对桌面应用权限、网络访问、报告交付和失败提醒；
8. 错过计划时刻后继续依靠 `pfm catch-up` 在下一次可运行机会推导和补齐缺口；登录瞬间触发不再
   属于项目验收要求。
