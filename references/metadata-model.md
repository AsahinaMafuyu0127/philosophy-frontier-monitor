# 元数据模型

状态：第 0 阶段基线初稿
模型版本：`0.4`
最后修订：2026-09-06

## 1. 目的

本模型用于保存哲学论文的书目信息、来源记录、首次公开与出版时间、版本关系、PhilPapers taxonomy、用户兴趣分类集合、集合匹配证据和通知历史。

本项目不保存论文质量评价、创新性评分或阅读优先级。

模型必须区分：

- 数据源直接提供的事实；
- 经过确定性规则规范化的值；
- 跨来源映射或日期判断；
- 用户的兴趣配置；
- 确定性集合匹配结果。

任何规范化、合并或映射都不得导致原始来源信息丢失。时间语义以[时间与版本语义](date-and-version-semantics.md)为准；标签语义以[兴趣标签映射与匹配规则](interest-tag-matching.md)为准。

## 2. 核心实体

### `work`

抽象作品。例如同一篇论文可能先有预印本，后来有正式期刊版本。

### `version`

作品的具体文本或出版形态，例如预印本、作者接受稿、正式记录版本。

### `source_record`

某个数据源中描述作品或版本的元数据条目。

### `taxonomy_snapshot`

在特定时间取得并验证的 PhilPapers 分类体系快照。

### `category`

taxonomy 中实际存在的受控分类，具有分类 ID、规范名称和父子关系。

### `interest_profile`

用户的原始研究方向、选中分类、排除分类、层级展开规则和版本。

### `interest_category_proposal`

经 taxonomy 核验、但仍等待用户接受、拒绝或暂缓的有限推理候选。候选不参与每周匹配。

### `paper_category_assignment`

数据源明确将某篇论文关联到某个受控分类的证据。

### `match_record`

在特定兴趣配置与 taxonomy snapshot 下执行集合交集的结果。

### `notification`

一篇作品何时、因哪些匹配分类进入哪一次周报。

### `on_demand_report`

用户明确请求后生成的临时即时报告。它有独立请求 ID 和滚动窗口，但不是持久化通知实体，不进入
周报去重历史。

## 3. 最小作品记录

一条可进入匹配流程的作品至少需要：

- 内部作品标识 `work_id`；
- 原始题目；
- 至少一个来源记录；
- 本系统首次观察时间；
- 可用于去重的来源 ID、DOI 或候选签名；
- 作品类型；
- 新旧状态 `freshness_status`；
- 分类状态 `category_status`。

`work_type` 优先采用来源提供的结构化书目类型。来源没有结构化类型时，本地规则只允许识别题名中
明确出现的受控书目形式标签：当前包括英语、德语、法语、西班牙语、意大利语、葡萄牙语和中文的
书评／评论前缀，并统一规范为 `book-review`。这类记录在调用 Crossref／OpenAlex 之前形成终局
`unsupported_work_type`，不进入 `unresolved_records`。不得依据论文主题、作者、期刊名称或模型对
内容的判断推断作品类型；没有明确标签的可疑记录仍应保留，等待结构化来源证据，而不是静默排除。

`work_type_status` 取值为 `confirmed`、`compatible`、`explicit_label`、`defaulted`、`unknown` 或
`conflict`。`work_type_evidence[]` 逐项保存来源、经安全处理的原始类型、统一类型、来源记录 ID 和
取得方法。结构化未知值和支持／不支持冲突不得降级成默认 article；它们分别形成
`unknown_structured_work_type` 和 `structured_work_type_conflict`。完全缺少结构化类型时仍允许当前
PhilPapers 提醒流走 `defaulted` 候选路径，但报告必须说明这不是来源断言。完整映射与门槛见
[结构化作品类型政策](work-type-policy.md)。

作者、DOI、摘要或出版日期缺失时可以为 `null`。但是没有足够时间证据的记录不能设为 `confirmed_new`，没有受控分类的记录不能设为 `matched`。

## 4. 作品记录示例

```yaml
schema_version: "0.2"

work_id: "pfm:work:01HXYZEXAMPLE"

canonical:
  title: "Example Paper Title"
  subtitle: null
  authors:
    - name: "Example Author"
      orcid: null
      position: 1
  language: "en"
  work_type: "article"

work_type_status: "confirmed"
work_type_evidence:
  - source: "crossref"
    raw_type: "journal-article"
    normalized_type: "article"
    source_record_id: "10.0000/example"
    method: "structured"

identifiers:
  doi: "10.0000/example"
  philpapers_id: null
  philarchive_id: null
  openalex_id: null

publication:
  container_title: "Example Journal"
  publisher: "Example Publisher"
  volume: "1"
  issue: "2"
  pages: "1-20"
  article_number: null

dates:
  observed_at:
    value: "2026-09-05T08:30:00Z"
    precision: "second"
    source: "local-system"
    inferred: false
  published_online_at:
    value: "2026-09-03"
    precision: "day"
    source: "publisher"
    inferred: false
  deposited_at: null
  availability_at: null
  source_updated_at: null

freshness:
  status: "confirmed_new"
  event: "recently_published_online"
  window_start: "2026-08-31T00:00:00Z"
  window_end: "2026-09-07T00:00:00Z"
  evidence:
    - source: "publisher"
      field: "published_online_at"
      value: "2026-09-03"
  excluded_as_backfill: false

version:
  type: "version_of_record"
  relations: []

categories:
  status: "available"
  taxonomy: "philpapers"
  taxonomy_snapshot: "2026-09-05"
  assignments:
    - category_id: "<verified-id>"
      category_name: "Plato: Epistemology"
      assignment_source: "philpapers-category-page"
      retrieved_at: "2026-09-05T09:00:00Z"

source_records:
  - source: "crossref"
    source_id: "10.0000/example"
    retrieved_at: "2026-09-05T08:30:00Z"
    parser_version: "0.1.0"
    raw_record_hash: "sha256:example"

notification_state:
  first_notified_at: null
  notifications: []
```

示例中的人物、标识和 DOI 均为结构演示，不是实际记录。`<verified-id>` 必须在真实运行前由 taxonomy 数据替换。

## 5. taxonomy snapshot

```yaml
taxonomy_snapshot_id: "philpapers:20260905T000000Z:0123456789ab"
source: "philpapers-taxonomy-json-api"
retrieved_at: "2026-09-05T00:00:00Z"
source_url: "https://philpapers.org/philpapers/raw/categories.json"
source_content_hash: "sha256:<hash-of-authenticated-response>"
content_hash: "sha256:<hash-of-normalized-snapshot>"
source_record_count: 6154
category_count: 6153
excluded_source_records:
  - category_id: "510734"
    reason: "empty_category_name_unreferenced_leaf"
duplicate_name_groups:
  - category_name: "Many-Valued Logic"
    category_ids: ["5247", "6700"]
access_method: "official-category-feed"
complete: true
fixture: false
parser_version: "0.1.0"
omitted_root_id: "1"
```

快照必须保存：

- 获取时间；
- 来源地址；
- 获取方式；
- 内容哈希；
- 分类数量；
- 解析器版本；
- 是否完整；
- 访问条件和错误状态。

上述数量与异常记录取自 2026-09-05 的真实导入快照，而哈希仍使用占位符表示结构。API ID、
API key、带凭据的查询字符串和认证响应正文不得保存到快照或来源 URL 日志中。
`source_record_count` 是来源数组长度；`category_count` 是通过验证、可用于匹配的分类数量；二者
不能因排除异常记录而被写成同一含义。`source_content_hash` 用于证明所解析的来源字节，
`content_hash` 用于证明规范化后本地快照文件；二者语义不同。

分类 ID 是唯一身份键，分类名称不是。重名组必须保留全部 ID 以供消歧；一个无法安全使用的来源
记录也不能悄悄删除或由模型补名，必须在 `excluded_source_records` 中保留来源 ID 和确定性原因。

PhilPapers 官方 category feed 省略根分类 ID `1`。当前规范化模型不伪造一个来源中不存在的
根分类实体，而是从顶层分类的 `parent_ids` 中移除该根引用，并用 `omitted_root_id: "1"` 保留
这项来源语义。除 ID `1` 外的任何缺失父引用均为结构错误。

## 6. 分类实体

```yaml
category_id: "<source-id>"
taxonomy_snapshot_id: "philpapers:2026-09-05"
category_name: "Plato: Epistemology"
primary_parent_id: "<parent-id>"
parent_ids:
  - "<parent-id>"
child_ids: []
active: true
```

分类以 ID 为主要匹配依据，以规范名称供人阅读。不能只依赖名称，因为 taxonomy 可能改名或存在近似名称。

## 7. 兴趣配置

```yaml
interest_profile_id: "pfm:interest:default"
version: 1
created_at: "2026-09-05T10:00:00Z"
effective_from: "2026-09-05T10:00:00Z"
original_text: "我研究柏拉图关于知识的学说，尤其关注《泰阿泰德》和《巴门尼德篇》。"
taxonomy_snapshot_id: "philpapers:2026-09-05"
inference_mode: "adaptive"
selected_categories:
  - category_id: "<verified-id>"
    category_name: "Plato: Epistemology"
    mapping_source: "contextual_mapping"
    include_descendants: true
    evidence: "柏拉图 + 知识学说"
excluded_categories: []
expanded_category_ids:
  - "<verified-id>"
ambiguous_candidates: []
proposed_categories:
  - category_id: "5481"
    category_name: "Defining Knowledge"
    relation: "contemporary_bridge"
    rationale: "用户关心柏拉图知识定义与当代知识分析的关系。"
    evidence_sources:
      - "user_text_inference"
      - "taxonomy_verified"
    breadth_note: "会增加不以柏拉图为对象的当代知识定义论文。"
    status: "pending"
```

兴趣配置的旧版本不覆盖。每次增加、删除、迁移或改变层级展开规则时创建新版本。
只有 `selected_categories` 及其明确展开结果参与 weekly-run；`proposed_categories` 即使具有真实
分类 ID，也不能参与匹配。

私人 YAML 保存完整研究描述、映射证据和候选决定；SQLite 的 `interest_profiles` 只保存重放
一次确定性匹配所需的活动运行快照，包括已确认分类、展开结果、排除分类、feed、taxonomy 快照、
生效时刻和调度语义。为减少私人研究内容的重复副本，运行快照不复制 `original_text`、候选标签、
理由或账户证据。两者都是本地私人数据，但用途不同，不能把精简的运行快照误当作完整兴趣档案。

同一 `interest_profile_id + version` 的运行快照不可修改：再次写入完全相同的规范化内容是幂等的；
内容不同则必须增加版本号。补跑按照该周计划交付时刻，选择当时已经生效的最后一个快照。旧快照
缺失、所需 taxonomy 快照不可用，或历史调度语义与当前窗口规划不一致时，必须在访问远程 feed
之前停止。

## 8. 分类映射证据

自然语言概念到分类的映射至少记录：

- 原始片段；
- 识别语言；
- 候选规范名称；
- 最终分类 ID；
- 映射方式；
- 消歧上下文；
- 是否由用户明确提到；
- taxonomy snapshot；
- 是否包含后代分类。

允许的 `mapping_source`：

- `exact_name`：用户使用正式分类名称；
- `verified_alias`：经维护的别名表；
- `explicit_work`：明确提到特定著作；
- `contextual_mapping`：根据人物、问题和著作组合映射；
- `user_selected`：用户直接选择；
- `migrated`：因 taxonomy 更新迁移。

模型猜测但未能在 taxonomy 中核验的词只能进入 `ambiguous_candidates`，不能进入 `selected_categories`。

推理候选另外保存：

- `relation`：`directly_implied`、`primary_text`、`conceptual_adjacency`、
  `historical_reception`、`contemporary_bridge` 或 `methodological_context`；
- `rationale`：为什么与当前研究描述有关；
- `evidence_sources`：用户原文、历史确认、经授权的账户资料或 taxonomy 关系；
- `breadth_note`：加入后可能扩大到什么范围；
- `status`：`pending`、`deferred` 或 `rejected`。

接受候选时将它移入 `selected_categories` 并创建新版兴趣配置，不能只把 `status` 改成 confirmed
却仍放在候选数组中。

## 9. 论文分类分配

```yaml
assignment_id: "pfm:assignment:..."
work_id: "pfm:work:..."
category_id: "<verified-id>"
taxonomy_snapshot_id: "philpapers:2026-09-05"
assignment_source: "philpapers-entry"
source_record_id: "<source-record>"
retrieved_at: "2026-09-05T09:00:00Z"
mapping_method: "direct"
confidence: "source_asserted"
```

`mapping_method` 允许：

- `direct`：PhilPapers 直接给出分类；
- `category_page_membership`：论文出现在明确分类页；
- `category_feed_membership`：论文从明确分类页对应的官方 feed 出现；
- `crosswalk`：其他来源主题经审核映射；
- `manual`：用户或维护者确认。

根据题目或摘要自动生成的标签不得保存为 `direct`。实验性模型标签如需保留，必须放入单独的候选字段，默认不参与匹配。

## 10. 分类状态

`category_status` 允许：

- `available`：至少一个受控分类可用于匹配；
- `awaiting_categories`：来源尚无细粒度分类，等待重查；
- `taxonomy_unavailable`：taxonomy 或分类来源本次不可用；
- `mapping_uncertain`：只有未经确认的 crosswalk 或候选分类；
- `not_applicable`：记录类型不进入论文匹配；
- `expired_unclassified`：超过迟分类补发期限仍无分类。

只有 `available` 的分类进入默认集合交集。

## 11. 新旧状态

`freshness_status` 允许：

- `confirmed_new`：有足够证据表明在监测窗口内首次公开或正式发表；
- `confirmed_source_arrival`：作品新近进入 PhilPapers 当前提醒流，旧作检查未发现更早同一作品；
  允许 `publication_date = null`，但必须保存分开的 `availability_date` 和来源限定语；
- `previously_notified_version_update`：同一作品的新版本，但已作为作品通知；
- `newly_indexed_old_work`：本周被数据库收录的旧作品；
- `source_record_updated`：来源记录更新，正文或发表状态未确认改变；
- `rediscovered`：系统重新发现已有作品；
- `backfill`：因历史回溯或兴趣范围变化而得到；
- `uncertain`：时间证据不足或冲突；
- `not_new`：明确不在监测窗口内。

`confirmed_new` 与 `confirmed_source_arrival` 默认允许进入常规周报。后者不得在显示层被改写为
“本周正式发表”。

## 12. 匹配记录

```yaml
match_id: "pfm:match:01HXYZ..."
work_id: "pfm:work:01HXYZ..."
interest_profile_id: "pfm:interest:default"
interest_profile_version: 1
taxonomy_snapshot_id: "philpapers:2026-09-05"
freshness_status: "confirmed_new"
paper_category_ids:
  - "<category-id-a>"
  - "<category-id-b>"
matched_category_ids:
  - "<category-id-a>"
excluded_category_ids: []
decision: "notify"
decision_method: "set_intersection_v1"
decided_at: "2026-09-05T10:30:00Z"
```

`decision` 允许：

- `notify`；
- `no_category_overlap`；
- `excluded_category_match`；
- `not_confirmed_new`；
- `awaiting_categories`；
- `already_notified`；
- `unsupported_work_type`。

匹配记录只表达规则结果，不包含论文质量判断。

## 13. 通知记录

```yaml
notification_id: "pfm:notification:..."
work_id: "pfm:work:..."
match_id: "pfm:match:..."
report_window_start: "2026-08-31T00:00:00Z"
report_window_end: "2026-09-07T00:00:00Z"
notification_type: "weekly_new_papers"
created_at: "2026-09-07T01:00:00Z"
matched_category_ids:
  - "<category-id>"
```

同一作品默认只保留一次 `weekly_new_papers` 通知。迟分类补发和用户明确启用的版本更新使用不同 `notification_type`。

即时拉取不得建立伪造的 `notification_type=on_demand` 记录。其最小结果只在本次命令输出中包含：

```yaml
request_id: "pfm:on-demand:..."
mode: "on_demand"
window_start: "2026-09-02T04:00:00Z"
window_end: "2026-09-09T04:00:00Z"
interest_profile_id: "pfm:interest:..."
interest_profile_version: 2
taxonomy_snapshot_id: "philpapers:..."
state_advanced: false
weekly_notifications_written: false
weekly_overlap_allowed: true
```

顶层来源请求在有界重试后仍失败时，命令错误可以附加一个不含 URL／查询／响应正文的结构对象：

```yaml
source_failure:
  source: "OpenAlex"
  failure_kind: "daily_budget_exhausted"
  attempts: 1
  stop_reason: "server_wait_exceeds_run_budget"
  http_status: 429
  retry_after_seconds: 43200
  retry_safe: true
  recovery: "等待额度重置后重试，或检查本机是否正确提供了可用 API key。"
```

`retry_safe=true` 表示以后按等待或诊断建议重试不会破坏本地状态，不表示应当立即连续重试。永久
HTTP 错误使用 `retry_safe=false`。周报／即时拉取在书目来源熔断后仍能形成部分报告时，不产生顶层
错误对象，而是在 `SourceCoverage` 和 `bibliographic_source_circuits_open` 中记录覆盖缺口。

CLI 可附加一个不含内容与凭据的运行级网络遥测对象：

```yaml
network_telemetry:
  logical_requests: 12
  first_attempt_successes: 10
  retried_successes: 1
  failed_requests: 1
  attempts: 14
  wait_seconds: 3.0
  deferred_long_retry_after: 1
  circuit_skipped: 8
  sources:
    OpenAlex:
      logical_requests: 2
      rate_limit_remaining: 73
      rate_limit_reset_seconds: 3600
```

限额字段仅在响应头提供可解析非负数字时出现有效值；缺失值为 `null`，不能解释为 0。该对象不保存
请求 URL、查询、响应正文、论文元数据、API key、Cookie 或邮箱。

报告内可以临时构造 `WorkRecord` 与 `MatchRecord` 来完成作品合并和集合交集，但不把它们写入
SQLite。即时模式调用匹配器时固定使用 `already_notified=false`；这表示“不执行周报历史抑制”，
不是声称数据库中从未通知过该作品。

## 14. 内部标识

内部 ID 必须：

- 在本地数据库中唯一；
- 不依赖当前标题拼写；
- 标题或作者纠正后不改变；
- 不包含用户私人研究内容；
- 不使用临时查询结果顺序。

推荐使用带实体前缀的 UUID 或 ULID，例如 `pfm:work:<ulid>`。算法在实现后应保持稳定。

## 15. DOI 和来源 ID

DOI 规范化应：

- 去除首尾空白；
- 转为小写；
- 去除 `https://doi.org/`、`http://dx.doi.org/` 和 `doi:` 前缀；
- 保留原始来源值；
- 验证基本格式，但不因格式通过便认定 DOI 实际存在。

来源 ID 由 `(source, source_id)` 共同识别。相同裸 ID 在不同来源之间不假定相同。

## 16. 题目与作者

- 保留原始题目；
- 另建仅用于比较的规范化题目；
- 不自动翻译后覆盖原题；
- 保留作者顺序；
- 不根据姓名相同自动认定为同一人；
- ORCID 只能来自来源或核验，不能猜测；
- Unicode 重音、希腊文和非拉丁文字必须保存。

作品同一性采用证据层级，而不是简单的名称完全相等：

- 共同 DOI 或共同来源 ID 是强证据，但作者明显冲突时仍须复核；
- 大小写、标点、Unicode 重音、作者姓名顺序和缩写差异可以规范化比较；
- 作者相容且题目实词集合、词序或拼写只有高置信度差异时，可以确认书目等价；
- 同一作者名下的完全跨语言题目、较大改题或翻译题名只能形成 `review_required`；
- Skill／模型可以解释两个题目的意义关系并请求用户确认，但确认前不得把模型相似度当作稳定 ID；
- 经确认的跨语言别名应作为有来源的别名或版本关系保存，不能覆盖原题。

## 17. 去重

### 可以自动合并

- 相同来源和相同来源 ID；
- 相同有效 DOI，且没有明显作者或作品类型冲突；
- 来源明确提供同一作品或版本关系。

### 只能作为候选重复

- 标题高度相似；
- 作者集合和年份相近；
- 摘要高度相似；
- 一个记录缺少 DOI；
- 预印本与正式版本标题有变化。

错误合并比暂时保留重复更难恢复。证据不足时保留两条作品候选，并进入复核。

## 18. 来源与字段溯源

所有规范显示字段、分类分配和日期判断都应能回答：

- 来自哪个来源记录；
- 原始值是什么；
- 执行了什么格式规范化；
- 是否经过 crosswalk；
- 是否经过人工确认；
- 何时取得或决定；
- 使用哪个解析器和规则版本。

不同来源冲突时保存全部候选值，不由模型无证据地综合出第三个值。

## 19. 原始数据与私人数据

来源原始 XML、JSON 或 RSS 与用户兴趣配置分开存储。用户研究方向、兴趣分类和通知历史属于本地私人数据，默认不进入 Git。

原始响应只保留复核和重放测试所需的最小范围；凭据和认证请求头必须在落盘前清除。详细规则见[安全与隐私政策](security-and-privacy.md)。

## 20. Schema 迁移

- 所有持久化记录带有 `schema_version`；
- 字段删除、改名或语义变化属于不兼容变更；
- 不兼容变更必须提供迁移脚本或明确重建策略；
- 迁移前备份状态库；
- 迁移后验证作品数、来源记录数、taxonomy 分类数、兴趣配置版本、匹配记录和通知历史；
- 不得在代码更新时静默改变 `confirmed_new` 或集合匹配的语义。

## 21. 配置 schema 0.3、0.4 与 0.5

配置 schema `0.3` 在 `0.2` 基础上增加：

- `schedule.local_time`、错过运行后的登录补跑、高优先级延迟和 Codex 失败通知策略；
- `interest.effective_from`，用于防止把新版兴趣画像追溯应用到在其生效前已经到期的漏跑窗口；
- `interest.inference.mode`；
- `interest.proposed_categories`。

解析器继续接受 `0.2`，并为新增字段使用安全默认值。候选标签会被结构化读取，但运行时活动集合
仍只来自 `confirmed_categories`。

配置 schema `0.4` 把容易被误解为“Windows 登录瞬间触发”的 `schedule.catch_up_after_login`
改名为 `schedule.catch_up_missed_windows`。这不改变补跑算法：缺口仍由计划交付时刻与成功运行历史
推导，并在 Codex 下一次获得执行机会时处理。解析器继续接受 schema `0.2` 和 `0.3` 的旧字段作为
兼容输入，但新配置只写 `catch_up_missed_windows`。

配置 schema `0.5` 增加首次对话的最小同意记录：

- `onboarding.completed_at`；
- `onboarding.philpapers_account.decision`，只允许 `declined`、`deferred` 或
  `authorized_public_only`；
- `decision_recorded_at`；
- 用户主动提供的不含凭据的 HTTPS PhilPapers／PhilPeople 公开资料 URL；
- `authorized_scopes`，逐项记录公开自报兴趣、公开书目、公开阅读清单或 `My Works`。

`declined` 和 `deferred` 不得保留 URL 或范围。旧 schema 继续读取，其账户决定在运行时显示为
`not_recorded`，既不构成授权，也不阻断已有周报。只读 `pfm onboarding-status` 仅输出是否需要补问、
决定类型和范围名称，不输出研究方向原文或资料 URL。

## 22. 第一版 SQLite 运行状态

模型版本 `0.4` 对应的本地 SQLite schema 版本为 `4`，使用以下运行表：

- `feed_subscriptions`：每个分类 feed 的基线完成时间、最后成功扫描时间、条目数和内容哈希；
- `source_records`：来源 ID、显示题目、稳定链接、最小记录哈希、首次／最后观察时间、处理状态和
  已解析作品 ID；
- `source_record_categories`：来源记录从哪些分类 feed 出现，以及该成员关系的首次观察时间；
- `unresolved_records`：唯一书目匹配失败、日期证据不足或标识冲突的原因、尝试次数和下次重试时间；
- `notifications`：按作品与通知类型去重的历史；
- `checkpoints`：只有完整成功运行才能推进的来源检查点；
- `runs`：窗口、开始／完成时间、报告路径、统计、成功状态、taxonomy 快照、兴趣配置版本、
  流水线版本和匹配规则版本。
- `interest_profiles`：不可变的活动运行画像；保存版本、生效时刻、taxonomy 快照、已确认／展开／
  排除分类、对应 feed 和调度语义的规范 JSON 与 SHA-256，不保存研究方向原文或待确认候选。

补跑状态不另建一个容易失真的布尔字段。`pfm catch-up` 使用 `runs` 中的成功窗口、配置的当地
星期和 `schedule.local_time` 在运行时推导 `up_to_date` 或 `pending_catch_up`；多窗口中途失败时，
已成功窗口保留在 `runs`，未成功窗口继续由同一规则推导为缺口。

schema 4 使 `pfm catch-up` 能在跨兴趣版本的多个缺口中逐周选择当时生效的画像。例如旧版在第一
个漏跑周的交付时刻仍有效，而新版在第二个漏跑周前生效，则两次运行分别记录旧、新版本；不会
把新版标签追溯应用到第一周。文件型状态库从旧 schema 打开时，程序先使用 SQLite backup API
在状态库同级 `backups/` 目录建立副本并执行 `PRAGMA integrity_check`，验证通过后才迁移。

当前状态库不保存 RSS description、摘要、全文、原始 XML／JSON、用户研究方向原文、待确认
候选理由或 API 凭据。它会保存已确认活动分类与 feed，因为这是重建历史匹配的必要私人状态。
`display_title` 只用于书目查询和故障复核；远程文本写入 Markdown 前必须折叠换行并转义结构字符。

`pfm pull-now` 不打开周报状态数据库，也不写入上述任何表。当前 feed 条目、候选 feed 日期、从
description 书目串提取的候选年份与规范化 DOI、OAI header datestamp、OAI `dc:date`／`dc:type`
提示、临时作品合并、匹配结果和未解决原因仅存在于本次进程中；description 本身、候选年份和只
用于查询的中间标题批次均不进入命令报告。如果用户
另行保存即时报告文件，该文件按私人报告处理，但它仍不成为周报运行记录或通知历史。

默认即时命令另行使用 Git 忽略目录中的 `bibliography-cache.sqlite3`。它与上述周报 schema 没有
外键，也不保存 `notification`、来源观察、重试、运行或检查点。当前最小表为：

```text
exact_lookup_cache(
  source,
  query_hash,
  status,          -- found | not_found
  payload_json,    -- 精简后的公开书目字段；不保存完整 API 响应
  cached_at,
  expires_at,
  PRIMARY KEY(source, query_hash)
)

fallback_attempt_log(
  source_id_hash,  -- PhilPapers 来源记录 ID 的 SHA-256，不保存原 URL
  attempted_at,    -- 最近一次完成逐篇核验尝试的 UTC 时间
  PRIMARY KEY(source_id_hash)
)
```

`source` 区分逐篇 Crossref、逐篇 OpenAlex、OpenAlex 单 DOI 批量键、OpenAlex 单题名正候选键和
`openalex-title-batch-attempt` 调度键。
逐篇键由规范化题名与首位作者提示散列，DOI 键由规范化 DOI 散列，题名批量键由规范化题名散列；
均不保存原始查询串。正结果只保存 DOI／OpenAlex ID、题名、作者、载体、类型、稳定链接和已有
日期证据。DOI 与逐篇正结果默认 24 小时过期；成功的 DOI／逐篇 `not_found` 默认 15 分钟过期；
题名批量只把有结果的候选列表写成书目结果，1 小时过期，空题名批次不写成负书目结果；成功调用
还会为参与题名写入一小时的散列调度标记，使重复拉取不立即重发相同批次。该标记不包含题名，
也不表示 OpenAlex 查无记录；候选仍进入逐篇轮转。传输失败、HTTP 限额错误、身份
冲突和语义复核结果不得写入缓存。缓存命中以后仍重新执行当前窗口的新近性、作品同一性判断与
分类交集，不能充当通知历史。
`fallback_attempt_log` 最多保留 31 天，只用于让跨次冷启动优先处理从未尝试或最久未尝试的延期
候选。它不表示“查无记录”，不改变证据结论，也不能抑制即时报告或周报；因传输失败或来源熔断
而没有完成核验的候选不写入该表。

即时交付结果另含 `report_delivery`、`report_character_count`、`report_path` 和
`on_demand_report_file_written`。`inline` 时 `report_markdown` 含全文且 `report_path=null`；用户明确
选择 `file` 时，全文写入私人报告目录，`report_markdown=null`，路径只用于交付。两者都不是周报
运行记录。

一条新增来源记录在第一次书目／日期核验失败后进入 `unresolved_records`。只有达到
`next_retry_at` 且尝试次数小于配置上限时才再次访问书目服务，避免每周无上限地重复请求。

## 23. Taxonomy 迁移审计记录

`pfm taxonomy-audit` 生成一次只读比较结果，不直接写入 SQLite。结果至少包含：

- 新旧 snapshot ID 和分类数；
- 新增、删除和同 ID 属性变化；
- 被删除分类的同名 ID 候选，但明确标记为未确认；
- 受影响分类在私人配置中的 `confirmed`、`proposed`、`excluded`、`expanded` 角色；
- 旧、新展开集合及其差异；
- 新展开集合缺少或多余的 feed；
- `ready`、`review_required` 或 `blocked` 激活状态；
- `automatic_migration_performed: false`。

分类迁移的审计输出不是新的兴趣画像。只有用户或维护者完成复核、更新私人配置、增加画像版本并
设置 `effective_from` 后，新 taxonomy 才能进入正式运行。详细合同见
[taxonomy 快照生命周期与迁移审计](taxonomy-lifecycle.md)。
