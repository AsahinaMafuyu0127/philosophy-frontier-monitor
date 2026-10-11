# 结构化作品类型政策

状态：稳定
最后修订：2026-10-10

## 1. 目的与边界

本政策只判断一条记录属于论文、预印本、手稿、书籍、书章、书评或其他书目形式，不评价内容质量、
论证水平、新颖性或研究价值。作品类型门槛先于分类交集；只有默认支持的论文形式才可能进入周报或
即时报告。

批量结构化判断不得读取论文正文，也不得根据题名主题或作者声誉猜测；它只使用当前书目请求
已经返回的受控 `type` 字段，以及 PhilPapers feed 题名／描述中明确出现的有限书目形式标签。
冲突记录随后进入 Codex 的逐篇复核：打开本篇记录、原始稿件或出版方页面，独立判断作品形式。

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
验证。批量程序不绕过验证、不复用 Cookie，也不把逐篇 HTML 抓取作为运行依赖；Codex 在正常浏览器
可访问时可以逐篇查看。页面不可读时转向原始存放平台或出版方，并记录未取得正文的限制。PhilArchive OAI
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
- 一个来源判为支持论文形式、另一个判为书籍／书章／书评等不支持形式：`conflict`，未核实前
  暂不推送，形成 `structured_work_type_conflict` 逐篇复核记录；
- OpenAlex／Crossref 的 `other` 只是兜底类型。若同一作品还有明确的受支持类型，`other` 保留在
  证据中，但不单独构成“非论文”的反证；解析状态为 `compatible`。若仅有 `other`，仍不作为论文
  推送。`dataset`、`book`、`conference-abstract` 等明确的不支持类型继续与论文类型形成冲突；
- 来源明确返回当前词表未知的安全类型值：`unknown`，本次不推送，形成
  `unknown_structured_work_type`；
- 所有来源都没有结构化类型，但记录满足 PhilPapers 当前提醒流和旧作检查：保持原有
  `confirmed_source_arrival` 路径，以 `article` 作为候选默认值，状态为 `defaulted`。报告必须说明
  默认值不是来源断言。

未知和冲突不能终局冒充“非论文”，也不能静默改成 article。它们应留在未完成核验统计中，以便词表
更新或来源元数据变化后重新处理。

人工复核条目在报告和私人 JSON 队列中完整列出逐来源类型标签、来源日期及证据指纹。它显示
冲突来自哪里，不代替原始存放平台的核验，也不把 `dc:type` 或 OpenAlex `type` 自动当成不可更正
的事实。

同一作品从多个分类 feed 重复出现时，去重不得直接比较最终类型字符串。程序必须先合并来源证据，
再用上述规则重新解析；因此一个缺少类型的默认记录与一个有 `preprint` 证据的记录会规范为
`preprint`，而不会仅因 `article`／`preprint` 两个字符串不同而制造伪冲突。

## 5. Codex 逐篇复核与回填

初步报告出现作品类型冲突时，先检查有证据显示日期可能落在窗口内的候选，再逐篇处理其余冲突。
打开**本篇** PhilPapers／PhilArchive 记录、实际稿件或 DOI 目标页，核对题名、作者和版本；
查看标题页、主体论证、篇幅与结构、参考文献，以及原始平台的明确类型说明。区分完整论文、
手稿、预印本、书章、书评、会议摘要和数据集。“摘要自称 paper”、DOCX／PDF 扩展名、
相似作品列表的标签均不足以单独定型。正文不可得时，仅在原始平台或出版方书目证据足够明确
时裁定；否则留在复核队列并说明缺口。

每条已裁定记录存入 Git 忽略的私人 JSON，包含 PhilPapers 记录 URL、准确题名、规范类型、
当次证据指纹、实际核查过的 HTTPS 来源、带时区的复核时间及简短依据。`pfm pull-now
--review-queue-file <私人路径>` 输出完整队列，并在同一私人 JSON 保存首次读取的分类 feed 清单；
复核后使用相同窗口终点的 `--as-of`、`--review-queue-input <首次队列>` 与
`--type-reviews <私人 JSON>` 得到最终即时报告。第二次不重新读取 RSS，也不把后来读取的
来源反记为初次窗口内的到达。首次运行若已指定过去的 `--as-of`，但没有当时保存的 feed 清单，
其来源到达统计只能称作追溯窗口投影，不能重建历史到达事实。周报先用 `weekly-run --dry-run
--review-queue-file` 生成队列，再按相同窗口及 `--type-reviews` 提交正式运行。题名或来源类型
证据指纹变化时，旧裁定不会套用；明确裁定为非论文也会结束类型冲突，但不会进入论文列表。

逐篇复核只裁定**作品形式**。是否属于七日窗口仍按日期和事件政策独立判断；存档上传日不自动
等同于期刊首次发表日。若两次拉取之间来源集合发生变化，须比较队列并核对新增或变化的记录。

私人复核文件的最小结构如下。`evidence_fingerprint` 从队列对应条目原样复制；证据 URL 应指向
实际打开并核查的本篇稿件或原始平台页面，`note` 写明可复核的形式判断依据。

```json
{
  "schema_version": 1,
  "reviews": [
    {
      "record_url": "https://philpapers.org/rec/EXAMPLE",
      "title": "Exact source title",
      "work_type": "manuscript",
      "evidence_fingerprint": "64-character lowercase SHA-256 value from the queue",
      "evidence_urls": ["https://philarchive.org/rec/EXAMPLE"],
      "reviewed_at": "2026-10-10T12:00:00+08:00",
      "note": "Title page and body show a complete, unpublished philosophical manuscript."
    }
  ]
}
```

示例中的指纹仅是格式说明，不可直接运行。程序核对记录 URL、题名和指纹后才应用裁定；
来源证据或身份变化时保持待复核。复核内容留在私人文件中，不写入公开介绍页面。
如果同一篇已读作品在固定窗口复跑时因书目查询路径不同，呈现两个实际核查过的来源标签组合，
可在该条裁定中另加 `"additional_evidence_fingerprints": ["另一组合的 64 位指纹"]`。
只允许列出已经逐项比较过的组合；新出现的第三种标签组合不会自动套用裁定。

## 6. 安全与资源约束

- 批量程序不为类型识别新增逐篇 PhilPapers 页面请求；Codex 仅对冲突条目逐篇访问；
- 复用既有 PhilArchive OAI、OpenAlex／Crossref 响应和私人短期缓存；
- 只接受最多 64 字符、由小写字母、数字和连字符组成的受控类型值；其他值在遥测和报告中统一写成
  `unrecognized`，防止远程文本注入 Markdown 或日志；
- 类型证据可以进入私人报告，但不得包含查询 URL、API key、响应正文或论文全文；
- 类型冲突不得通过额外无限重试解决，仍受即时拉取远程预算、缓存和来源熔断控制。

## 7. 2026-09-07 离线缓存审计

对本机既有私人书目缓存只读统计，不输出题名、作者或兴趣画像：271 条记录带有 Crossref／OpenAlex
结构化类型，当前词表全部识别。规范化后包括 129 条 article、15 条 preprint、1 条 review-article、
61 条 book、53 条 book-chapter、4 条 book-review，以及少量 dataset、dissertation、editorial 和
other。唯一一对同时存在的 Crossref／OpenAlex 类型是 `posted-content`／`preprint`，一致规范为
`preprint`。

该审计证明统一词表可以利用已经取得的元数据减少非论文误入，但不证明未来来源不会增加新类型；
未知类型必须继续按第 4 节失败关闭。

## 8. 官方依据

- [OpenAlex Work types](https://help.openalex.org/data/work-types/)：完整受控词表、类型定义和
  `book-review`／`review` 区分；
- [OpenAlex Works attributes](https://help.openalex.org/data/works/attributes/)：每条 Work 的
  `type` 字段及其属性契约；
- [Crossref REST API `/types`](https://api.crossref.org/types)：Crossref 当前公开作品类型集合。
