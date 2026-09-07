# 设计来源、实现差异与独立贡献

最后核验：2026-09-08

本文说明本项目在“无 RSS 时间戳／无作品年份”的近期论文发现问题上参考过哪些公开 GitHub 项目，
哪些思想被采用，哪些实现没有照搬，以及本项目新增了什么。这里的“新增”表示相对于所列项目的
工程组合与证据语义差异，不声称某个 OAI-PMH 基础操作本身是本项目发明，也不构成专利新颖性判断。

## 1. 直接设计启发：sea9401/philosophy-mcp

核验版本：commit
[`96a3929`](https://github.com/sea9401/philosophy-mcp/tree/96a39290ac7095db0fe8895b08d3e288c088c73f)，
MIT License。

其 [`src/index.ts`](https://github.com/sea9401/philosophy-mcp/blob/96a39290ac7095db0fe8895b08d3e288c088c73f/src/index.ts)
中的 `list_recent` 提供了本次修改的直接启发：不要试图从缺失日期的 PhilPapers 分类 RSS 条目中
猜测发表时间，而应使用 PhilArchive OAI-PMH `ListRecords` 的 `from`／`until` 参数取得近期创建或
修改的元数据记录。这样，即使 `dc:date` 缺失，OAI header 的 `datestamp` 仍能构成“来源记录在
窗口内发生变化”的证据。

本项目没有复制该仓库的 TypeScript 代码。其当前实现只解析第一响应页，并在结果上执行 `slice`；
没有继续消费 `resumptionToken`。解析结果读取 `dc:date`，但没有把 OAI header `datestamp` 保存为
独立字段；`list_recent` 也不与用户确认的 PhilPapers 分类集合求交。因此该实现适合交互式查询示例，
不能直接满足本项目的完整周监测、兴趣分类交集和旧作补录排除要求。

## 2. 对照实现：tamnd/philpapers-cli

核验版本：commit
[`6c8a3b1`](https://github.com/tamnd/philpapers-cli/tree/6c8a3b18e0ae41b337d27180b2e741b9d9a32af3)，
Apache-2.0 License。

其 [`philpapers/philpapers.go`](https://github.com/tamnd/philpapers-cli/blob/6c8a3b18e0ae41b337d27180b2e741b9d9a32af3/philpapers/philpapers.go)
中的 `Recent` 请求 OAI `ListRecords`，但没有传 `from`／`until`，也没有继续分页；
[`philpapers/types.go`](https://github.com/tamnd/philpapers-cli/blob/6c8a3b18e0ae41b337d27180b2e741b9d9a32af3/philpapers/types.go)
虽声明 `Datestamp` 和 `ResumptionToken`，`Recent` 路径却没有用它们完成窗口筛选。OAI-PMH 不保证
第一批不完整列表按最近时间排序，所以本项目把这种实现当作反例检查，而不是所采用的算法。

## 3. 对照实现：Sfgangloff/co-philosopher

核验版本：commit
[`fc9ba00`](https://github.com/Sfgangloff/co-philosopher/tree/fc9ba00e220419e45c30af47371b5368cfbcd038)。
核验时 GitHub API 没有返回可确认的 SPDX 仓库许可证，因此本项目不复制其代码。

其 [`philarchive.py`](https://github.com/Sfgangloff/co-philosopher/blob/fc9ba00e220419e45c30af47371b5368cfbcd038/src/cophilo/biblio/philarchive.py)
从 PhilArchive RSS description 中提取四位年份，并识别 forthcoming、manuscript、preprint 等状态。
这可以规范化已有书目信息，但在年份和状态都缺失时不能证明记录何时到达，也没有持久基线或 OAI
窗口。因此本项目继续把 RSS 年份／早期稿本词作为弱候选提示，不用它们替代来源时间。

## 4. 本项目相对于上述实现的新增组合

### 4.1 完整、可审计的 OAI 增量窗口

- 查询窗口向前重叠一个 datestamp 单位，减少边界和时钟差造成的遗漏；
- 逐页消费全部 `resumptionToken`，续页请求不重复携带首请求参数；
- 再在本地按准确 UTC 半开区间 `[window_start, window_end)` 过滤；
- 对重复 `/rec/` 键保留窗口内最新记录，删除记录不进入论文候选；
- `noRecordsMatch` 作为成功的空窗口处理，而不是来源故障。

这些行为遵循 [OAI-PMH 2.0 协议](https://www.openarchives.org/OAI/2.0/openarchivesprotocol.htm)和
[OAI 收割器实施指南](https://www.openarchives.org/OAI/2.0/guidelines-harvester.htm)。

### 4.2 两种时间语义不混写

- OAI header `datestamp` 保存为 `source_datestamp`／`availability_date` 证据；
- OAI `dc:date` 只作为作品日期或年份线索；
- 两者都不会被无条件写成 `publication_date`；
- OAI 近期变化只能生成待核验候选，完成旧作检查后至多支持
  `confirmed_source_arrival`，不能单独生成 `confirmed_new`。

### 4.3 分类集合与增量集合的确定性交集

OAI 普通 `dc:subject` 在实测中通常只有笼统的 `Philosophy`，不足以表达用户的细粒度兴趣。本项目
不让模型从 OAI 标题临时生成标签，而是通过共享的 `/rec/` 键计算：

```text
用户确认的 PhilPapers 分类当前记录
∩ PhilArchive OAI 窗口内非删除变化记录
```

分类证据仍来自 PhilPapers taxonomy 和分类 feed；OAI 只提供近期来源变化证据。这保持了项目的
核心规则：集合交集，而不是质量评分或模型相关性排序。

### 4.4 成功时缩减、失败时不伪造排除

即时模式只对既没有 feed 时间戳、也没有书目年份的冷启动库存应用 OAI 缩减。完整 OAI 收割成功
时，未命中记录不进入本次滚动候选，但不会被永久标为旧作；报告必须披露缩减数，并说明 OAI 只
覆盖开放记录。OAI 请求失败、分页不完整或无法解析时，未命中不能成为排除证据，程序退回原有宽
候选并披露来源失败。

周报不依赖这种排除：它仍以持久基线发现新来源记录，只复用 OAI datestamp、`dc:date` 和
`dc:type` 作为补充证据。因此即时模式的性能优化没有创造一套更宽松的论文推送标准。

### 4.5 结构化类型与旧作检查继续生效

OAI 的 `info:eu-repo/semantics/*` `dc:type` 进入与 Crossref／OpenAlex 共用的作品类型门槛。
article、book、bookPart、thesis、workingPaper 和 preprint 使用保守映射；语义不足以区分综述论文
和书评的笼统 `review` 保持未知。OAI 命中之后仍执行 DOI、OpenAlex、Crossref、题名／作者作品
同一性和旧年份检查，不因 OAI 近期更新就直接推送。

## 5. 首次真实结果与尚未解决的问题

2026-09-08 的首次七日实跑完整读取 9 个 OAI 逻辑页。2039 条兴趣分类当前记录按旧规则形成 347
条宽候选；OAI 求交后为 47 条，完全无 feed 时间与年份的候选只剩 1 条，逐篇预算延期为 0，真正
需要用户判断的作品同一性候选为 2 条。

这证明该组合显著降低了当次冷启动机器工作量和人工负担，但不能证明对所有画像、日期或来源都能
缩到相同比例。当前限制包括：

- PhilArchive OAI 只覆盖开放记录，非开放且完全无日期的新 PhilPapers 记录可能不在即时结果中；
- OAI datestamp 不区分元数据首次创建与后续修改，仍需旧作检查；
- 一周 OAI 窗口可能包含数千条记录和多页响应，完整收割本身有固定等待时间；
- OpenAlex／Crossref 仍可能限流、延迟收录或返回冲突证据；
- 真实效果应继续用候选漏斗、人工复核数和抽查误报／漏报记录评价，不能只追求更小的数字。

## 6. 代码与许可证边界

上述 GitHub 项目用于设计审查和对照。本项目本次 OAI 适配器由维护者独立以 Python 实现，没有把
`philosophy-mcp` 的 TypeScript 或 `philpapers-cli` 的 Go 源码复制、翻译或捆绑进仓库。上游项目的
名称、固定 commit、相关源文件和许可证只用于可复核的来源说明；本项目 MIT License 不改变上游
代码、PhilPapers／PhilArchive 元数据或论文内容的权利状态。
