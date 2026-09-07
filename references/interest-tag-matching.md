# 兴趣标签映射与匹配规则

状态：第 0 阶段基线初稿
规则版本：`0.2`
最后修订：2026-09-05

## 1. 目的

本文规定如何把用户用自然语言描述的研究方向转换为受控哲学分类集合，以及如何用确定性的集合交集判断一篇新论文是否进入每周推送。

本规则不评价论文质量、重要性、创新性或论证成败。模型只参与“研究方向到 taxonomy 分类”的映射，不参与运行时论文质量筛选。有限推理的深度、账户证据和用户确认边界见[兴趣标签有限推理政策](interest-inference-policy.md)。

## 2. 术语

### 用户标签 `user tag`

用户容易理解的界面术语，可以来自中文或英文研究方向描述。

### 受控分类 `controlled category`

在特定版本的 PhilPapers taxonomy 中实际存在、具有稳定分类 ID 或可核验规范名称的分类。

### taxonomy

PhilPapers 的层级化哲学分类体系。它不是任意关键词列表。官方说明该体系具有五层结构，一个分类可以有多个父分类并有一个主要父分类：

- [PhilPapers Categorization Project](https://philpapers.org/help/categorization.html)
- [PhilPapers JSON API](https://philpapers.org/help/api/json.html)

### 已确认兴趣集合 `confirmed_interest_set`

用户研究方向映射、经 taxonomy 核验并由用户确认后得到的受控分类 ID 集合。只有这个集合参与每周匹配。

### 推理候选集合 `proposed_interest_set`

AI 根据用户描述或经授权的辅助证据提出、已在 taxonomy 中核验、但尚未获得用户确认的分类集合。它不参与 feed 订阅和每周匹配。

### 论文分类集合 `paper_category_set`

数据源明确关联到某篇论文、并经 taxonomy 核验的分类 ID 集合。

### 匹配 `match`

对已经通过新论文门槛的作品，执行：

```text
confirmed_interest_set ∩ paper_category_set
```

交集非空则匹配；交集为空则不匹配。

## 3. 系统边界

系统负责：

- 解析用户研究方向中的人物、问题、著作、传统、时期和主题；
- 在 taxonomy 中查找真实存在的候选分类；
- 处理中文名称、英文名称、常见别名和拼写变体；
- 利用 taxonomy 的父子关系展开用户选择的范围；
- 保存映射理由与 taxonomy 版本；
- 对新论文执行集合交集；
- 告诉用户是哪几个分类导致了匹配。

系统不负责：

- 判断匹配论文是不是好论文；
- 判断作者论证是否成功；
- 因为论文“看起来不重要”而丢弃有效匹配；
- 因为摘要语义相似而在没有分类交集时默认推送；
- 自动把不存在于 taxonomy 的自由关键词当作正式分类；
- 用论文标题猜测分类并伪装成来源分类。

## 4. 自然语言到分类集合的映射

用户可以自由描述研究方向，例如：

```text
我研究柏拉图关于知识的学说，尤其关注《泰阿泰德》和《巴门尼德篇》。
```

映射分为四步。

### 4.1 提取研究概念

从原始描述中提取：

- 人物：Plato；
- 领域：Epistemology；
- 具体问题：Knowledge and Belief；
- 著作：Theaetetus、Parmenides；
- 传统、时期或方法：如果用户明确提到则提取；
- 排除项：如果用户明确说“不关注……”则提取。

提取结果只是候选概念，还不是受控分类。

### 4.2 taxonomy 查找

每个候选概念必须在当前 taxonomy snapshot 中查找：

- 精确规范名称；
- 大小写无关名称；
- 已维护的中英文别名；
- 人物加主题的组合分类；
- 人物加著作的组合分类；
- 父分类和子分类。

只有查找到真实分类 ID 或经官方分类页核验的规范名称后，才能进入正式兴趣集合。

### 4.3 语境消歧

相同词语可能对应不同分类。例如 `Parmenides` 可能指：

- 历史人物巴门尼德；
- 柏拉图的《巴门尼德篇》；
- 与巴门尼德相关的前苏格拉底哲学主题。

如果用户语句明确说“柏拉图的《巴门尼德篇》”，应映射到 `Plato: Parmenides`，不能仅映射到人物 Parmenides。语境仍不足时，候选分类标记为 `ambiguous`，不静默加入正式集合。

### 4.4 生成可审查结果

映射结果必须把“已确认／直接映射”与“有限推理候选”分开显示。候选至少记录关系类型、理由、证据来源、范围提示和确认状态。

```yaml
original_text: "我研究柏拉图关于知识的学说，尤其关注《泰阿泰德》和《巴门尼德篇》。"
taxonomy: "philpapers"
taxonomy_snapshot: "2026-09-05"
categories:
  - category_id: "<verified-id>"
    category_name: "Plato: Epistemology"
    display_name_zh: "柏拉图：认识论"
    mapping_source: "contextual_mapping"
    evidence: "Plato + knowledge doctrine"
    include_descendants: true
  - category_id: "<verified-id>"
    category_name: "Plato: Theaetetus"
    display_name_zh: "柏拉图：《泰阿泰德》"
    mapping_source: "explicit_work"
    evidence: "用户明确提到《泰阿泰德》"
    include_descendants: true
```

示例中的 ID 必须由真实 taxonomy 数据填入，不能保留 `<verified-id>` 进入运行配置。

## 5. 标签集合的范围

用户兴趣集合可以很大，也可以很小。系统不强制所有用户使用相同粒度。

### 宽范围

例如加入：

```text
Plato
Epistemology
```

只要新论文带有其中任一分类，就可能匹配。这会包括大量一般认识论论文和大量柏拉图研究。

### 中等范围

例如：

```text
Plato: Epistemology
Plato: Knowledge and Belief
Plato: Theaetetus
Plato: Parmenides
```

这会比同时保留一般 `Plato` 和一般 `Epistemology` 更窄。

### 窄范围

例如只保留：

```text
Plato: Theaetetus
Plato: Knowledge and Belief
```

结果数量会更少，也可能漏掉尚未被细分到这些分类的相关论文。

系统可以预览各分类及其子分类数量，帮助用户理解范围；不得用质量判断替用户自动删除宽泛分类。也不得因为候选看起来学术上合理，就跳过用户确认把它加入活动集合。

## 6. 层级展开

PhilPapers taxonomy 是层级结构。内部匹配必须明确父子分类如何处理。

### 默认规则

当用户确认 `include_descendants: true` 时，将该分类的所有后代加入展开后的兴趣集合：

```text
expanded_interest_set = selected_categories ∪ descendants(selected_categories)
```

例如用户选择 `Plato`，则其下的 `Plato: Epistemology`、`Plato: Theaetetus` 等后代分类都可以匹配。

“可以展开”不等于“可以静默展开”。确认上位分类以前，程序必须按
[兴趣范围与资源提醒政策](resource-and-scope-warnings.md)计算展开总数、相对于现有画像的净新增
分类 feed 数和基线影响。新增 11 个或更多 feed 时须先提醒并再次取得明确确认；超过 50 个时还应
提供较窄子分支方案。该闸门只说明运行范围，不评价用户的研究兴趣，也不授权程序擅自删减标签。

### 不自动加入无关祖先

选择 `Plato: Theaetetus` 不表示用户希望收到所有 `Plato` 分类论文，因此系统不把祖先 `Plato` 本身加入用户的选择集合用于反向扩大范围。

### 多父分类

一个分类可能有多个父分类。展开时依据 taxonomy 提供的全部父子关系构图，不能只使用显示页面上的主要父分类。

### taxonomy 更新

分类被改名、移动、合并或删除时：

- 保留旧 ID 和旧名称；
- 记录新 taxonomy snapshot；
- 尝试根据官方关系迁移；
- 无法确定时暂停该分类并提示复核；
- 不因名称相似自动迁移到另一个分类。

## 7. 别名与翻译

用户语言别名只用于查找受控分类，不参与最终集合交集。

别名表可以包含：

```yaml
aliases:
  "柏拉图": "Plato"
  "柏拉图认识论": "Plato: Epistemology"
  "泰阿泰德": "Plato: Theaetetus"
  "泰阿泰德篇": "Plato: Theaetetus"
  "Theaetetus": "Plato: Theaetetus"
```

别名记录必须说明目标分类和维护来源。别名不改变 PhilPapers 的正式分类名称。

## 8. 论文分类集合

一篇论文的 `paper_category_set` 只能由以下证据构成：

- PhilPapers 论文记录明确显示的分类；
- PhilPapers 分类页明确列出的论文归属；
- 论文从某个 PhilPapers 分类页对应的官方 RSS feed 出现；
- 获得许可的 PhilPapers article feed 中的分类字段；
- 经审核的跨来源分类映射，其目标是实际存在的 PhilPapers 分类。

下列内容不能直接冒充 PhilPapers 分类：

- OAI `dc:subject=Philosophy`；
- 模型根据标题自行生成的关键词；
- OpenAlex concept 或 topic 的原始 ID；
- Crossref subject 的原始字符串；
- 作者摘要中偶然出现的词；
- 期刊名称。

如果使用 OpenAlex 等来源的主题，需要维护显式 crosswalk，并标注 `mapped_category` 与映射置信度。默认情况下，未经人工或测试确认的 crosswalk 不进入正式匹配。

## 9. 确定性集合匹配

只有论文先通过[时间与版本语义](date-and-version-semantics.md)和[监测政策](monitoring-policy.md)规定的“确实新出”门槛，才执行标签匹配。

```python
matched_category_ids = expanded_interest_category_ids & paper_category_ids
should_notify = is_confirmed_new and bool(matched_category_ids)
```

不得在集合交集为空时，让语言模型根据摘要“补判相关”。如果以后用户明确需要语义补充召回，应作为单独、可关闭且清楚标识的实验模式，不能改变默认规则。

### 9.1 分类 feed 驱动的等价实现

如果程序分别订阅兴趣集合中每个分类的官方 RSS feed，那么一条论文记录从分类 `C` 的 feed 出现，可以直接生成来源断言：

```text
C ∈ paper_category_set
```

程序把同一作品在多个分类 feed 中的出现合并后，所得命中分类集合等价于集合交集结果。该路线不需要先取得全站所有论文的完整分类列表，但仍须：

- 确认 feed 确实对应目标分类与筛选条件；
- 保存 feed URL、分类 ID、抓取时间和条目 ID；
- 核验论文的真实首次公开／发表日期；
- 排除旧论文后来加入分类页所形成的新增 feed 条目；
- 跨 feed 按作品去重；
- 完整处理 feed 自身的截断、翻页或历史窗口限制。

## 10. 排除标签

用户可以明确设置排除分类：

```text
effective_match =
    (expanded_interest_set ∩ paper_category_set ≠ ∅)
    and
    (excluded_set ∩ paper_category_set = ∅)
```

排除集合只在用户明确配置后生效。系统不根据质量、作者或期刊自动创建排除标签。

如果同一论文同时命中包含和排除分类，默认以排除为准，并在本地匹配记录中保存冲突原因。

## 11. 未分类和迟分类论文

PhilPapers 的分类工作是持续进行的，新条目可能暂时显示 `No categories`。官方也说明其目标是把条目归入最多三个领域，但分类可能由自动工具、用户或编辑后续完成。

因此：

- 没有来源分类时，不把论文立即判为“不相关”；
- 将其记为 `awaiting_categories`；
- 在可配置期限内重新检查分类；
- 后来获得匹配分类且论文原始首次公开日期仍在允许窗口内时，可以补发；
- 补发必须标为“迟分类补发”，不能伪装成本周新发表；
- 超过补发期限后只保留记录，不混入普通周报。

## 12. 兴趣集合更新

用户扩大或缩小兴趣集合，或者接受一个推理候选时：

- 创建新的 `interest_profile_version`；
- 记录带时区的 `effective_from`，表示新版确认集合何时开始生效；
- 保存增加、删除和迁移的分类；
- 默认从变更后的下一次监测开始生效；
- 不把所有历史匹配论文自动混入当周新论文；
- 用户明确要求时可以生成单独的历史回溯报告；
- 历史回溯必须标为 `backfill`，与常规周报分开。

补跑发现最近一次成功运行使用的画像版本不同于当前版本时，必须比较漏跑窗口的计划交付时刻与
`effective_from`。每个正式活动版本保存一份不可变的本地运行快照；程序按各窗口的计划交付时刻
选择当时最后一个已生效版本。旧画像快照不可用、所需 taxonomy 快照已丢失或历史调度边界无法
安全重建时，必须在联网前停止，不能把当前标签集合无声追溯到旧周次。

## 13. 去重与通知

- 同一作品通过多个分类匹配时，只推送一次，并列出全部命中分类；
- 同一作品来自多个数据源时，只推送一次，并列出来源；
- 已推送预印本后来正式发表，默认记录版本更新但不再次作为“新论文”推送；
- 用户可以另行启用“正式版本更新通知”；
- 标题变化、来源重新收录或分类变化本身不构成新论文；
- 每次通知保存 `work_id`、兴趣配置版本、命中分类和发送时间。

## 14. 匹配解释

匹配解释只陈述集合事实：

```text
匹配原因：论文分类中包含 “Plato: Theaetetus”，该分类位于你的兴趣标签集合中。
```

不使用：

```text
这是一篇重要而有创新性的论文，非常值得优先阅读。
```

如果命中多个分类，应全部列出，以便用户判断为什么收到这篇论文。

## 15. 匹配记录

每次匹配至少保存：

```yaml
match_id: "pfm:match:..."
work_id: "pfm:work:..."
interest_profile_version: 3
taxonomy_snapshot: "2026-09-05"
paper_category_ids:
  - "<category-id>"
matched_category_ids:
  - "<category-id>"
excluded_category_ids: []
freshness_status: "confirmed_new"
decision: "notify"
decided_at: "2026-09-05T10:00:00Z"
decision_method: "set_intersection_v1"
```

## 16. 测试要求

至少测试：

- 中文研究方向能映射到真实 taxonomy 分类；
- 不存在的模型生成标签不能进入正式集合；
- 同义词映射到同一个分类 ID；
- 《巴门尼德篇》与历史人物巴门尼德能够区分；
- 父分类正确展开到后代分类；
- 多父分类不会漏掉展开关系；
- 交集非空时匹配；
- 交集为空时不匹配；
- 一个作品命中多个分类仍只通知一次；
- 没有分类的论文进入等待队列；
- 兴趣集合更新不会把历史旧论文混进常规周报；
- taxonomy 分类改名或删除时不会静默错配；
- 新论文门槛未通过时，即使标签匹配也不推送。
