# 结构化作品类型政策

状态：稳定
最后修订：2026-09-08

## 1. 目的与边界

本政策只判断一条记录属于论文、预印本、手稿、书籍、书章、书评或其他书目形式，不评价内容质量、
论证水平、新颖性或研究价值。作品类型门槛先于分类交集；只有默认支持的论文形式才可能进入周报或
即时报告。

类型判断不得读取论文正文，不得让模型根据题名主题或作者声誉猜测。结构化识别只使用当前书目请求
已经返回的受控 `type` 字段，以及 PhilPapers feed 题名／描述中明确出现的有限书目形式标签。

“当前书目请求已经返回”是资源边界：程序不会仅为了取得第二个类型标签而追加 OpenAlex 或
Crossref 请求。例如 Crossref 已经给出足够精确的身份与日期、原流程因而不需要 OpenAlex 时，
类型层只使用 Crossref 证据。它不把“只得到一个来源”冒充双源确认，也不为了形式识别耗费额外
额度。后续正常核验或缓存若取得另一个来源，合并逻辑会重新解析完整证据集合。

## 2. 当前证据来源

按来源分别保留原始受控值、规范化值、来源记录 ID 和取得方法：

1. PhilPapers 题名开头的明确书评标签，规范为 `book-review`；
2. PhilPapers feed 描述中明确出现的 manuscript、working paper、preprint、forthcoming 等标签；
3. PhilArchive OAI `dc:type` 中的 `info:eu-repo/semantics/*` 受控值；
4. OpenAlex Work 的受控 `type`；
5. Crossref Work 的受控 `type`。

这里的顺序不是一般性的“数据库可信度排名”。第一、二项只对标签明确表达的局部事实有效；第三至
五项用于结构化书目类型。程序不得因某来源整体声誉而覆盖一次具体冲突。

PhilPapers 公开记录页在 2026-09-07 的程序化请求和正常内置浏览器检查中均触发 Cloudflare 安全
验证。当前实现不绕过验证、不复用 Cookie，也不把逐篇 HTML 抓取作为运行依赖。PhilArchive OAI
现在提供开放记录的受控 `dc:type`，但它不代表完整 PhilPapers 索引；非开放 PhilPapers 记录的正式
结构化类型适配仍需官方允许的 article feed 或稳定机器接口。

## 3. 统一类型词表

### 默认支持

- `article`：期刊论文；
- `review-article`：综述论文，不等于书评；
- `preprint`、`manuscript`、`working-paper`、`forthcoming-article`：尚未完全落定的前沿论文形态；
- `conference-paper`：完整会议论文；
- `data-paper`、`software-paper`：描述数据或软件的论文，而不是数据集或软件本身。

### 默认不支持

- `book`、`book-chapter`、`book-review`；
- `conference-abstract`、`dataset`、`software`、`dissertation`；
- `editorial`、`letter`、`peer-review`、`reference-entry`；
- `erratum`、`retraction`、`paratext`、`supplementary-materials`；
- `standard`、`libguides`、容器记录和不能确定为论文的 `other`。

OpenAlex 当前明确区分：`book-review` 是对单本书的评价，`review` 是总结和评价某一研究领域的综述
论文。本项目把前者保留为 `book-review` 并排除，把后者规范为 `review-article` 并按论文处理。

Crossref 的 `journal-article` 规范为 `article`，`posted-content` 规范为 `preprint`，
`proceedings-article` 规范为 `conference-paper`。OpenAlex／Crossref 的 `report` 当前实验性规范为
`working-paper`，因为 OpenAlex 的正式定义明确把 working paper 包含在 report 中；如果真实运行
表明该映射引入大量非论文报告，应改为需要复核，而不是使用模型过滤。

PhilArchive OAI 当前使用 OpenAIRE `info:eu-repo` publication type URI。article、book、bookPart、
三类 thesis、workingPaper 和 preprint 可保守映射到现有内部词表。`review` 只说明对他人已发表
作品的评论，不能可靠区分综述论文与书评，因此保持 `unknown`，不得自动映射成
`review-article`。词表字段和允许值参见
[OpenAIRE Publication Type](https://guidelines.openaire.eu/en/latest/literature/field_publicationtype.html)；
review 的范围说明参见
[COAR Resource Types: review](https://vocabularies.coar-repositories.org/resource_types/c_efa0/)。

上述 `preprint`、`manuscript`、`working-paper`、`accepted-manuscript` 或 `forthcoming` 明确信号也
是日期证据不足集合的解除条件：即使记录没有年份、DOI 或 feed 时间，也必须继续进行新近来源与
旧作核验，不能仅因缺少正式发表日期而进入集合。

## 4. 一致、冲突、未知与缺失

- 多个来源映射到同一规范类型：`confirmed`；
- 多个来源给出不同类型，但全部都属于支持形式或全部都属于不支持形式：`compatible`，保留全部
  证据并选取更具体的规范类型；
- 一个来源判为支持论文形式、另一个判为书籍／书章／书评等不支持形式：`conflict`，本次不推送，
  形成 `structured_work_type_conflict` 待核验记录；
- 来源明确返回当前词表未知的安全类型值：`unknown`，本次不推送，形成
  `unknown_structured_work_type`；
- 所有来源都没有结构化类型，但记录满足 PhilPapers 当前提醒流和旧作检查：保持原有
  `confirmed_source_arrival` 路径，以 `article` 作为候选默认值，状态为 `defaulted`。报告必须说明
  默认值不是来源断言。

未知和冲突不能终局冒充“非论文”，也不能静默改成 article。它们应留在未完成核验统计中，以便词表
更新或来源元数据变化后重新处理。

同一作品从多个分类 feed 重复出现时，去重不得直接比较最终类型字符串。程序必须先合并来源证据，
再用上述规则重新解析；因此一个缺少类型的默认记录与一个有 `preprint` 证据的记录会规范为
`preprint`，而不会仅因 `article`／`preprint` 两个字符串不同而制造伪冲突。

## 5. 安全与资源约束

- 不为类型识别新增逐篇 PhilPapers 页面请求；
- 复用既有 PhilArchive OAI、OpenAlex／Crossref 响应和私人短期缓存；
- 只接受最多 64 字符、由小写字母、数字和连字符组成的受控类型值；其他值在遥测和报告中统一写成
  `unrecognized`，防止远程文本注入 Markdown 或日志；
- 类型证据可以进入私人报告，但不得包含查询 URL、API key、响应正文或论文全文；
- 类型冲突不得通过额外无限重试解决，仍受即时拉取远程预算、缓存和来源熔断控制。

## 6. 2026-09-07 离线缓存审计

对本机既有私人书目缓存只读统计，不输出题名、作者或兴趣画像：271 条记录带有 Crossref／OpenAlex
结构化类型，当前词表全部识别。规范化后包括 129 条 article、15 条 preprint、1 条 review-article、
61 条 book、53 条 book-chapter、4 条 book-review，以及少量 dataset、dissertation、editorial 和
other。唯一一对同时存在的 Crossref／OpenAlex 类型是 `posted-content`／`preprint`，一致规范为
`preprint`。

该审计证明统一词表可以利用已经取得的元数据减少非论文误入，但不证明未来来源不会增加新类型；
未知类型必须继续按第 4 节失败关闭。

## 7. 官方依据

- [OpenAlex Work types](https://help.openalex.org/data/work-types/)：完整受控词表、类型定义和
  `book-review`／`review` 区分；
- [OpenAlex Works attributes](https://help.openalex.org/data/works/attributes/)：每条 Work 的
  `type` 字段及其属性契约；
- [Crossref REST API `/types`](https://api.crossref.org/types)：Crossref 当前公开作品类型集合。
