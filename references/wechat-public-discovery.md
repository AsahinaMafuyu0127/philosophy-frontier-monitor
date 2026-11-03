# 官方公众号公开入口发现、候选队列与原帖核验

`pfm journal-watch wechat-discovery-plan` 列出有来源依据的账号线索和刊方入口。账号目录是发现计划，不是每一期的证据。当前纳入《哲学研究》《哲学动态》《世界哲学》《中国哲学史》《现代哲学》《周易研究》《孔子研究》《中国社会科学》八刊；其中《世界哲学》的专门账号和《中国哲学史》借用的编辑部账号与本刊的对应关系仍列为 `candidate`。其余账号的身份来源见目录里的 `wechat_identity_url`。一些刊方只提供二维码、没有可核验的账号名称，因此《哲学分析》《自然辩证法通讯》等暂不填猜测的账号。

## 公开发现与候选状态

在通过本 Skill 发起 `search`、`pull-now` 或周报的会话中，先执行：

```powershell
pfm journal-watch wechat-discovery-plan
pfm journal-watch wechat-discover
pfm journal-watch wechat-candidates
```

`wechat-discover` 对目录内每刊**已登记的刊方 HTTPS 页面**，按用户当地日期每天至多读取一次。时区优先取命令中的 `--timezone`（IANA 标识），其次取 `--config` 指向的私人 watchlist 时区；配置文件不存在时取运行用户本机时区。北京用户使用的 IANA 标识是 `Asia/Shanghai`，它不是所有用户的默认时区。若命令运行在远端主机或容器，须显式传入用户时区或提供已设定该时区的 watchlist，不能把远端系统时区当作用户所在地。例如 `pfm journal-watch wechat-discover --timezone America/New_York`。

发起网络请求前，程序先在 Git 忽略的本地 SQLite 库中为该刊当天登记 `attempted`；并发调用、进程中断或当天刊方入口 URL 变化都不能再次消耗该刊当天额度。正常完成后登记 `ok` 或 `failed`；重复调用返回 `already_scanned`，其中 `prior_status` 保留原状态，`attempted` 也可能意味着前一次请求中断。旧版按北京日期保存的记录会按其实际请求时间换算至当前用户时区，避免切换时区后重复扫描或错过新的当地日期。每个响应限 20 秒、750 KB、HTML、20 条公众号文章链接，不跨站跳转。它只从刊方页面抽取直达 `mp.weixin.qq.com/s/...` 的链接，不请求微信原帖，不把候选自动写成官方期次。`wechat-candidates --journal ID` 可筛选队列。页面没有外链或只用二维码时，零候选只表示这个页面未给出可抽取链接。

对于未覆盖的刊物，Codex 可在同一会话中用公开网页搜索或交互式浏览器，按账号名称、刊名、年份、期号和“目录”寻找确切原帖。检索时每刊保持有界，记下 URL、来源页面和失败状态。搜索结果不能直接标为 reviewed；只有登记刊方页面抽到的链接自动进入队列。会话检索获得的确切原帖可用 `pfm journal-watch wechat-add-candidate --journal ID --evidence-url URL --source-url HTTPS_SOURCE` 入队；队列明确保留发现来源，不能把搜索摘要伪装为刊方外链。

## 日常待办与新增候选

`wechat-discover` 输出本次命令实际插入的 `new_candidates`、`new_candidates_count`、
各刊扫描结果和 `candidate_summary` 状态计数，不再随每轮扫描输出整个历史队列。
新增由同一数据库事务内的插入结果判断；重复发现、当天已扫描或同一秒内已有的记录
均不会被重复计为新增。每条候选保留首次发现的 `source_url`、链接文字和首次时间；
重复发现只更新最后观察时间，保留待核、受阻、排除或已复核的原状态与备注。
会话手动入队的输出用 `newly_added` 标出是否首次加入。

日常先看本轮新增，再读取有界待办：

```powershell
pfm journal-watch wechat-candidates
pfm journal-watch wechat-candidates --journal xiandai-zhexue --queue-status pending
pfm journal-watch wechat-candidates --queue-status blocked --limit 20 --offset 20
pfm journal-watch wechat-candidates --queue-status all --limit 20
```

默认 `--queue-status active` 只列待核和受阻项，每页 20 条；`--limit` 允许 1 至 100，
`--offset` 从 0 开始。`matched_count` 是筛选后的总数，`has_more` 提示是否还有下一页；
`summary` 统计所选刊物的全部状态，已复核与已排除项保留但不占默认待办页。
连续受阻满七个完整日（7×24 小时）的候选和受阻起点未知的旧记录优先显示，
其后是新近入队的待核项及其他受阻项。`attention_reason` 分别标为
`long_blocked`、`blocked_age_unknown`、`pending` 或 `blocked`。
受阻时长按已记录的 `blocked_since` 计算，重复标为受阻保留连续起点并更新
`last_checked_at`；旧库没有起点时留空，不用首次发现时间伪造受阻时间。
这只是候选处理顺序，不是期刊等级、论文相关性评分或发表新近性判断。
列队列、筛选和分页均不发起网络请求，也不增加每日扫描额度。

## 会话内核验

逐一打开候选原帖，核实 HTTPS 主机、显示账号、原帖日期、刊名、年份与期号、正文目录的完整可读性，并与刊方身份来源交叉核对。候选账号还须额外核实账号与该刊编辑部的关系。遇到验证页、登录、正文残缺或错刊，保留状态与理由：

```powershell
pfm journal-watch wechat-block --journal ID --evidence-url URL --reason "正文验证页"
pfm journal-watch wechat-dismiss --journal ID --evidence-url URL --reason "错刊"
```

只有实际读过完整原帖并核实期次、账号和明确日期后，才能执行：

```powershell
pfm journal-watch wechat-review --journal ID --year 2026 --issue 5 `
  --evidence-url URL --announcement-on 2026-10-09 `
  --account-name "原帖显示的账号名" --toc-complete --confirm-evidence
```

新执行的 `wechat-review` 必须引用待核或受阻候选，并明确提供原帖账号显示名、公告日和
完整目录声明；对 `candidate` 身份，还要显式提供 `--confirm-account-identity`。
账号名应按原帖显示填写并与刊方身份来源核对，命令不会根据名称相同自动证明账号身份。
账号名、公告日、目录完整性、UTC 复核时间和备注，与已复核期次及候选 `reviewed` 状态
在同一事务内保存；任一步失败全部撤回。`--confirm-evidence` 和 `--toc-complete` 是
人工阅读声明，命令本身不能证明正文已读，也不会自动导入逐篇题名。
旧库已复核记录保留原状态，缺少的结构化复核细节保持空值；不要补写未经复读的信息。
受阻或排除操作必须提供非空理由；队列清理不能把已复核候选降级，也不能直接把候选
标成已复核而绕过期次的事务写入。分享链接中的跟踪参数会规范化后再匹配队列原帖。

原帖日期只记作公告日。只有原帖明确标出月份才传 `--label-month`；不可按期号推算。
逐篇题名须逐项阅读，未读目录不能形成逐篇分母。周报、即时拉取和历史检索继续只读取
已复核的刊方期次证据，不把新增候选数、处理优先顺序或目录完整声明当成逐篇发表证明。
已复核期次可按首次本地核实时间进入周报与即时拉取的刊方期次栏，历史检索按年份读取；
公告日和刊期月份照原证据显示，不据此认定逐篇论文于本窗口发表。

## 无人值守边界

本地定时任务可运行刊方公开页面发现并保留候选，但它没有这些期刊公众号的文章列表授权，不能自动取得所有新帖或完成原帖正文核验。`pfm search`、`pull-now`、`weekly-run` 在无人值守时只读取本地已复核证据；本 Skill 在可浏览的会话中处理候选。需要登录或验证码时记为 `blocked`，不绕过。普通 HTTP 200、转载、账号同名、搜索摘要和图片缩略图不足以确证。微信和搜狗微信搜索公开 `robots.txt` 对相应路径有限制，本项目不把它们当作后台爬取入口。若后续获得获授权的列表服务，应另行核验授权、身份、正文范围、限额与故障状态。
