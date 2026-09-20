# Philosophy Frontier Monitor

[English](README.en.md) | 简体中文

**按你确认的研究方向，持续生成事实性的哲学新论文周报。**

研究者用中文或英文说明研究方向，Skill 将其映射到经过核验的 PhilPapers 受控分类；只有用户
明确确认的分类才会进入监测。报告保留论文题目、作者、日期证据、命中分类、来源链接与覆盖缺口，
不评价论文质量，也不按模型评分删减结果。

现在也可按已确认的兴趣配置检索**已有论文**：`pfm search --config config/watchlist.yaml`。
它不限制为近期新作；只纳入有论文类型证据的记录，排除书籍、书章和书评。引用量有数据就降序
排列，缺失就标注并置后。支持临时选择已配置标签、年份范围、论文类型和结果分页，不修改订阅
或周报状态。当前覆盖分类 feed 本次返回的记录，不保证完整历史索引。
不限制年份时默认返回全部已确认匹配论文，缺少 feed 日期或年份提示也不排除；只有设置年份
范围时，才提前排除没有 feed 年份提示的记录。用户明确指定数量时仍可分页。
[检索用法、范围与缺失说明](references/paper-search.md)。

![从研究方向到每周哲学论文报告的四步流程](assets/demo/philosophy-frontier-monitor-demo.png)

[查看完整公开示例周报](examples/public-demo-weekly-report.md) ·
[查看周报预览原图](assets/demo/public-demo-weekly-report-2026-09-09.png) ·
[完整安装说明](references/installation.md)

### 公开示例：用三个真实标签实际拉取

下面的报告不是合成稿。它于 2026-09-09 使用完整 PhilPapers taxonomy 中的
`Moral Responsibility`（4590）、`Free Will`（347）和 `Action Theory`（5992）三个分类实际运行
七日 `pull-now` 生成；三个分类均按精确范围读取，不展开子类。为避免改动正式周报状态，示例采用
与每周监测共享证据规则的只读即时模式，而不是伪造一次已经提交的定时运行。

这次真实运行的首要结果是：**确认新出 1 篇**，另有 **PhilPapers 新近来源 91 篇**。后者表示
论文新近进入所选分类对应的 PhilPapers／PhilArchive 来源变化集合，而且当次旧作检查没有发现更早
作品证据；它不等于已经取得正式发表日期。两项合计 92 篇，并在同一报告内按作品去重。匹配论文
现在依次分为 **recently published**（1 篇）、**recently arrived**（2 篇）和
**recently changed**（89 篇）：每组按与其语义相符的证据时间从新到旧排列，组间有清楚的分割线；
`recently changed` 默认折叠，但报告中始终保留可见的显示／隐藏开关。

另有 **18 条记录需要人工复核**。

下图呈现报告第一页。[完整 Markdown](examples/public-demo-weekly-report.md)收录全部 92 篇的
中英文记录；需要了解更多时，可自行展开其中的复核记录与技术说明。

![三个真实 PhilPapers 标签生成的古典象牙白周报第一页](assets/demo/public-demo-weekly-report-2026-09-09.png)

CLI 原生报告格式是 Markdown。若当前 Codex 环境具备文档或演示文稿生成能力，用户还可以要求把
同一份报告转排为 **Word（.docx）** 或 **PowerPoint（.pptx）**；这是报告生成后的呈现转换，不会
重新筛选论文，也不是 `pfm` CLI 的原生导出格式。交互式 Markdown 中可以直接点击展开；若准备
转排 Word 或 PowerPoint，并希望其中包含来源记录变化组，可在生成时加入
`--show-recently-changed`，使该组默认展开。

### 为什么它不同

- **研究范围由你决定**：自然语言兴趣先映射为真实分类，再由你逐项确认；
- **完整报告而非质量排名**：不按作者声望、期刊等级、引用量或模型评价过滤；
- **证据与缺口同时披露**：区分出版、新近可得、来源重录和元数据更新，来源失败不会伪装成零结果；
- **私人配置留在本机**：研究方向、凭据、SQLite 状态、缓存与周报默认不进入公开仓库。

### 开始安装

需要 Codex、Git、[`uv`](https://docs.astral.sh/uv/) 和 Python 3.12 或 3.13。在 Codex 中调用：

```text
$skill-installer 请从 https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor
仓库根目录安装这个 Skill；path 使用 .，安装名称使用 philosophy-frontier-monitor。
```

安装后，在新一轮对话中说：

```text
请使用 $philosophy-frontier-monitor，帮我把研究方向映射到经过核验的 PhilPapers 分类，
由我确认后完成本地配置、历史基线和第一次周报。不要让我把凭据粘贴到聊天中。
```

当前应用版本为 **v0.3.0**；内部证据管线版本为 **0.6.0**。正式实机验收在 Windows 上完成；
macOS／Linux 路径已经兼容，但尚未取得同等的真实定时运行验证。

<a id="technical-update-2026-09-20"></a>

## 技术更新：公开示例的人工复核提示（2026-09-20）

公开示例的覆盖说明现在只显示“有 18 条记录需要人工复核”，中英文介绍页与预览图同步调整。
完整示例的复核明细和技术说明默认折叠，读者可自行展开；原有论文记录及核验事实保留。
本次为文档与展示更新，已核对论文内容、折叠结构和链接，并重新渲染检查预览图。

<a id="technical-update-2026-09-19"></a>

## 技术更新：兴趣论文检索与当地时间调度（2026-09-19）

本次 `main` 分支更新新增独立的 `pfm search`，按已确认的兴趣配置与 PhilPapers feed 标签查找
已有论文，支持任一／全部标签匹配、年份与论文类型筛选。检索不要求历史基线，也不推进周报
通知历史或改写长期订阅。此功能已随[代码提交 `9249da6`](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/commit/9249da600c79a63c2f55a973bcd98c60cd2361dd)
推送，尚未另建版本标签或 GitHub Release。

日期处理按用户的检索要求区分：**不限制年份时**，缺少 feed 日期、年份提示或正式发表日期的
论文仍可纳入，默认返回全部已确认匹配结果；只有用户明确限量时才分页。**指定年份范围时**，
没有 feed 年份提示的记录在书目查询前直接排除，最终年份筛选仍使用正式书目日期证据。

引用量采用 OpenAlex 的可用计数，已知值降序排列，缺失标注并置后，零次引用与缺失分开处理。
书籍、书章、书评等非论文形式不进入结果；类型或书目身份未确认的记录只提示存在、不报告
数量，并可提供已有来源供用户自愿核对，skill 不再为这些记录追加调查。冲突 DOI 不会因仅有
一个外部匹配就被视为身份已确认。

定时任务流程现在优先读取会话提供的用户时区，缺少时检测本机时区；“早上八点”等表达按当地
时间理解，在最终创建任务时统一确认星期、时间和时区，不在前期单独追问。既有订阅仍沿用其
已确认的时区。

功能提交通过 **319 项测试**、代码与格式检查、skill 校验及发布检查。Git 历史备份包也已纳入
私人文件保护。检索覆盖本次分类 feed 能够提供并确认的论文，不代表 PhilPapers 全站完整书目。
完整规则见[论文检索说明](references/paper-search.md)与[变更记录](CHANGELOG.md)。

今后的项目更新都会同步写入本页及英文页面的技术更新，并保留已有记录；这一要求已写入
[维护规则](CONTRIBUTING.md#网页技术更新与变更记录)。

## 技术更新：OAI 跨运行缓存与即时拉取收尾（2026-09-08）

证据管线 `0.6.0` 为 PhilArchive OAI 增量收割增加了可中断恢复的跨运行 SQLite 缓存。`pull-now`、
`weekly-run` 和 `catch-up` 默认复用已经完整收割的 OAI 窗口，只从网络补齐尚未覆盖的区间；可用
`--no-oai-cache` 临时关闭。缓存位于 Git 忽略的私人 `var/oai-cache.sqlite3`，与周报通知、基线和
检查点状态分离。

OAI 缓存只保留记录标识符、来源 `datestamp`、删除标记，以及程序实际使用的 `dc:identifier`、
`dc:date` 和 `dc:type`；不保存题名、作者、摘要或全文。缓存命中仍会重新执行当前窗口过滤、作品
类型处理和确认分类交集，不会因为此前见过某条记录而抑制本次即时报告或后续周报。

PhilArchive 当前提供秒级 OAI `datestamp`。收割器现在向前重叠一个秒级单位，并把 OAI 的包含式
`until` 精确转换为本地半开窗口 `[start, end)`，避免额外下载窗口结束日的无关记录。长冷启动会在
stderr 报告已完成分页数和累计记录数。每个成功页的最小记录和下一枚不透明 `resumptionToken`
在同一事务中落盘；中断后续跑从最近检查点继续。只有全部分页成功且暂存记录原子并入正式缓存后，
程序才把区间登记为已覆盖；过期或被上游拒绝的令牌会安全清空该缺口的暂存并从原窗口重新收割。

即时拉取的范围保持克制：成功 OAI 窗口未命中的完全无日期记录从本次滚动候选集移除；已经命中
OAI、但同时缺少 feed 时间、书目年份、DOI 和明确手稿／预印本类型的库存记录，进入私人、只含
来源 ID 散列的“日期证据不足集合”。普通拉取只提示集合数量，不逐条消耗书目查询，也不把它们
计入机器积压或人工复核；若后来出现任一上述证据，记录自动返回核验。这不是旧作或不相关判断，
也意味着完全缺少这些信号的新手稿可能不会出现在普通即时报告中。项目不为这部分记录增加付费墙
页面或逐篇 PhilPapers 抓取。`pull-now` 自 `v0.3.0` 起属于稳定
功能；它与每周监测共享证据标准，但仍是用户主动调用、只读且不推进通知历史的独立模式。完整规则见 [CHANGELOG.md](CHANGELOG.md)、
[监测政策](references/monitoring-policy.md)与[结构化作品类型政策](references/work-type-policy.md)。

## 一、旨在解决的问题

哲学研究者往往需要反复打开 PhilPapers、期刊页面和书目数据库，检查自己的研究领域是否出现了
新论文。本项目把这项机械劳动转化为持续监测：研究者用中文或英文说明研究方向，Skill 将其映射
到经过核验的 PhilPapers 受控分类，并在新论文命中已确认分类时生成周报。

它不评价论文质量，不替研究者判断论证是否成功，也不按作者声望、期刊等级、引用量或模型评分
删减结果。它要解决的是“不要漏掉可能相关的新论文”，而不是“替我决定哪些论文值得读”。

## 二、功能

公开 `v0.3.0` 的稳定功能包括：

- 把自然语言研究方向转换为待确认的 PhilPapers 分类集合；
- 只启用用户确认过的真实分类，不把模型生成的关键词冒充官方分类；
- 首次运行先建立历史基线，避免把既有论文误报为本周新作；
- 按用户时区执行每周增量监测，并在错过计划时间后按窗口顺序补跑；
- 区分正式发表、预印本／手稿新近可得、来源重新收录和元数据更新；
- 合并多个分类来源中的同一作品，并抑制正式周报中的重复通知；
- 输出题目、作者、载体、日期证据、命中分类、来源和稳定链接；
- 没有匹配结果时如实报告零结果，来源失败时明确说明覆盖缺口；
- 在 1—31 个当地日的滚动窗口内即时拉取，并在中断后从 OAI 分页检查点恢复。

核心规则是：

```text
(确认在窗口内新出，或确认新近进入 PhilPapers 提醒流)
AND
(论文分类集合 ∩ 用户已确认兴趣分类集合 ≠ ∅)
```

`pull-now` 可以在用户明确提出时立即检查最近 1—31 个当地日，属于稳定但非定时、非通知模式。程序综合使用
PhilPapers 分类 feed、PhilArchive OAI 来源记录变化、Crossref／OpenAlex 书目证据、结构化作品类型
和用户已经确认的分类交集；OAI `datestamp` 始终只表示元数据记录创建、更新或删除，不等于论文
出版日期。

默认的私人 `oai-cache.sqlite3` 跨次保存已经完整收割的 OAI 窗口；相同窗口可以完全从缓存读取，
滚动窗口通常只需刷新新增尾部。缓存不属于周报状态，也不会抑制以后报告。首次遇到上游批量元数据
更新时，冷启动仍可能需要较长时间和较大的本地缓存；程序会显示分页进度、逐页保存恢复点，且只在
完整分页成功后登记覆盖。可用 `pfm oai-cache status` 检查记录数、覆盖与中断会话，用带明确
`--confirm` 的 `pfm oai-cache prune --before <ISO-8601>` 清理旧缓存；两者都不推进周报状态。

即时拉取开始时会显示磁盘建议。由于 OAI 和书目缓存可能在上游批量更新期间显著增长，Windows 用户
如有其他可用磁盘，应尽量不要把 `state_database` 及其同目录缓存放在空间紧张的 `C:` 系统盘；优先
配置到容量充足的非系统盘。该建议不改变跨平台路径支持，也不会擅自移动既有私人文件。

对完全没有 feed 时间、没有年份且未命中成功 OAI 窗口的记录，本项目不再继续扩展到付费墙或
非开放页面。若一条 OAI 库存记录连 feed 时间、书目年份、DOI 和明确早期稿本类型也都没有，默认
私人书目缓存只保存其来源 ID 散列，形成最长保留 365 天并随再次观察刷新的日期证据不足集合。
第一次拉取会在联网前提示运行可能较长，并以默认 300 条逐篇远程预算完成集合外候选的旧作核验；
集合内记录只报告数量。出现新证据时自动解除集合状态。OpenAlex／Crossref 的剩余延期、人工复核
和来源自动重试仍分别披露，不把未核验冒充已覆盖。稳定承诺指状态隔离、可恢复性、有界资源与诚实
报告，不表示固定运行时长，也不改变正式周报的通知历史、基线和检查点。

## 三、如何安装与首次使用

### 推荐：让 Codex 完成安装

需要 Codex、Git、[`uv`](https://docs.astral.sh/uv/) 和 Python 3.12 或 3.13。当前正式实机验收在
Windows 上完成；其他系统的路径已作兼容设计，但尚未获得同等的真实定时运行验证。

在 Codex 中调用内置安装器：

```text
$skill-installer 请从 https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor
仓库根目录安装这个 Skill；path 使用 .，安装名称使用 philosophy-frontier-monitor。
```

安装完成后的下一轮对话中，请 Codex 执行：

```text
请进入已安装的 philosophy-frontier-monitor 目录，运行 uv sync --python 3.12，
复制公开示例为私人 watchlist，并按照 Skill 引导我完成首次配置。
不要让我把任何 API key、密码或 Cookie 粘贴到聊天中。
```

Codex 官方文档说明，`$skill-installer` 可以从其他 GitHub 仓库安装 Skill；新安装的 Skill 通常在
下一轮对话可用，如果没有出现则重启 Codex。参见 [OpenAI 官方 Skill 文档](https://learn.chatgpt.com/zh-Hant/docs/build-skills)。

### 首次配置流程

1. 用普通语言告诉 Skill 你的研究方向；你不需要预先知道 PhilPapers 分类名称。
2. 审查 Skill 提出的真实分类、范围和推理理由，明确接受、拒绝或暂缓每个候选。
3. 按引导取得 PhilPapers taxonomy 所需的 API ID 和 API key，并只保存到本机 Git 忽略目录；
   不要把凭据发到聊天、Issue、截图或命令行参数中。
4. 建立历史基线。基线只把当前 feed 条目标为既有历史，不生成历史论文洪水。
5. 先完成一次 dry run 和正式周运行，再让 Codex建立每周计划任务。默认是用户当地星期一 08:00，
   可以改为其他时区、星期和时间。

熟悉终端的用户可以在安装目录中运行：

```powershell
uv sync --python 3.12
Copy-Item .\config\watchlist.example.yaml .\config\watchlist.yaml
.\.venv\Scripts\pfm.exe doctor
```

macOS／Linux 对应的程序路径是 `.venv/bin/pfm`。完整的安装、运行目录、凭据和首次基线说明见
[安装与首次部署](references/installation.md)；英文版见
[Installation and first deployment](references/installation.en.md) 与
[English user guide](references/usage.en.md)。

## 四、当前版本

当前版本：`v0.3.0`。

| 能力 | 状态 |
|---|---|
| 自然语言研究方向到受控分类 | 稳定 |
| 分类范围预估与用户确认 | 稳定 |
| 历史基线 | 稳定 |
| 每周增量监测与事实性周报 | 稳定 |
| 错过窗口后的顺序补跑 | 稳定 |
| SQLite 状态、去重与事务性检查点 | 稳定 |
| 数据源有界重试、遥测与运行级熔断 | 稳定 |
| 用户主动即时拉取 `pull-now` | 稳定（主动、只读） |
| OAI 跨运行缓存、分页中断恢复与增量刷新 | 稳定 |
| 周报与即时报告的中英文双语输出 | 稳定 |

`v0.3.0` 将用户主动即时拉取纳入稳定能力。本版本完成分页暂存与 `resumptionToken` 检查点、进程
互斥、失效令牌安全重启、缓存状态和显式清理接口，并验证冷启动、中断续跑、热命中和滚动增量刷新。
本次未轮到远程核验／人工复核／自动重试继续分栏报告；稳定并不消除上游批量元数据更新造成的
冷启动成本。

OAI 缓存是性能与可恢复覆盖机制，不是论文新近性的替代证据。缓存命中不会把 `datestamp` 转换成
出版日期，也不会跳过当前兴趣分类、作品身份、作品类型或窗口判断。

### 设计来源、差异与本项目新增

本次 OAI 增量候选思路直接受到
[`sea9401/philosophy-mcp`](https://github.com/sea9401/philosophy-mcp) 的 `list_recent` 启发：当
PhilPapers RSS 没有条目时间时，改用 PhilArchive OAI 的记录变化窗口。不过，本项目没有复制其
代码，也没有沿用“只处理第一响应页”的实现，而是增加完整 `resumptionToken` 分页、重叠窗口与
精确本地过滤、删除记录处理、OAI 时间／出版时间分离、与确认分类 `/rec/` 集合求交、旧作检查和
失败回退。首次真实七日测试把 347 条宽候选缩为 47 条，真正需要用户判断的候选为 2 条。

我们也把 [`tamnd/philpapers-cli`](https://github.com/tamnd/philpapers-cli) 的无窗口、首响应页
`Recent` 实现，以及 [`Sfgangloff/co-philosopher`](https://github.com/Sfgangloff/co-philosopher)
的 RSS 年份／稿本状态提取作为对照，说明哪些办法不能单独解决“完全无时间信息”的问题。完整的
固定版本链接、许可证状态、没有复制上游代码的边界、逐项实现差异和仍然存在的覆盖限制见
[设计来源、实现差异与独立贡献](references/design-lineage.md)。

版本变化见 [CHANGELOG.md](CHANGELOG.md)，贡献和验证要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 五、安全与来源边界

- 私人研究方向、确认分类、凭据、SQLite 状态和周报默认只保存在本机，不进入公开仓库；
- `config/watchlist.yaml`、`var/`、`reports/`、`.env` 和虚拟环境均受 Git 忽略规则保护；
- `oai-cache.sqlite3` 与 `bibliography-cache.sqlite3` 都位于私人运行目录；OAI 缓存只保存标识符、
  记录时间、删除标记、最小类型／年份字段和中断续跑所需的不透明分页令牌，不保存题名、摘要、
  作者、全文或完整响应；书目缓存中的日期证据不足集合也只保存来源 ID 散列与首次／最近观察时间，
  不保存题名或作者；状态命令不回显令牌；
- API key 只允许从本机私人文件或进程环境读取，不写入代码、报告、日志、测试夹具或 URL 输出；
- 远程题目、摘要、XML、JSON 和错误页都按不可信数据处理，不能成为执行本地命令的指令；
- 默认不下载论文全文，不绕过付费墙、访问控制、TLS 验证或站点防护；
- 项目只监测用户确认的分类和运行时成功返回的来源，不承诺穷尽全球哲学新作或消除来源延迟；
- PhilPapers feed 到达时间不自动等于论文正式发表时间，报告会保留事件类型、日期精度和证据来源；
- OpenAlex 与 Crossref 用于书目身份、旧作和日期核验；它们的未收录或暂时失败不能被伪装成确定结论；
- MIT License 只适用于本项目代码，不自动适用于 PhilPapers taxonomy、第三方元数据、摘要或论文全文。

详细规则见 [安全与隐私政策](references/security-and-privacy.md)、
[监测政策](references/monitoring-policy.md)、[时间与版本语义](references/date-and-version-semantics.md)、
[数据源目录](references/source-catalog.md)与[第三方声明](THIRD_PARTY_NOTICES.md)。

## 六、问题反馈

- 维护者：**Asahina Mafuyu**
- 联系邮箱：[junxuanxie@stu.xjtu.edu.cn](mailto:junxuanxie@stu.xjtu.edu.cn)

普通安装问题、功能错误、来源兼容问题和改进建议，可以在
[GitHub Issues](https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor/issues) 中提交，也可以发送邮件。
反馈时请提供版本、操作系统、执行的命令以及已经删除私人信息的错误摘要；不要提交 API key、密码、
Cookie、私人研究方向、完整 `watchlist.yaml`、数据库、周报或带认证参数的 URL。

即时拉取问题还应说明窗口天数、`oai-cache status` 中的计数与布尔状态、是否发生中断，以及报告中
的本次远程核验未处理量／人工复核／自动重试数量；不要复制分页令牌或上传缓存数据库。维护者会
先把反馈归类为
可复现缺陷、来源语义变化、性能／资源问题或证据政策提案，再决定代码、测试或政策文档的修改。

可能导致凭据或私人研究数据泄露的安全问题，不要发到公开 Issue。请使用 GitHub Private
Vulnerability Reporting；具体步骤见 [SECURITY.md](SECURITY.md)。
