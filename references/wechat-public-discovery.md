# 官方公众号公开入口发现、候选队列与原帖核验

`pfm journal-watch wechat-discovery-plan` 列出有来源依据的账号线索和刊方入口。账号目录是发现计划，不是每一期的证据。当前纳入《哲学研究》《哲学动态》《世界哲学》《中国哲学史》《现代哲学》《周易研究》《孔子研究》《中国社会科学》八刊；其中《世界哲学》的专门账号和《中国哲学史》借用的编辑部账号与本刊的对应关系仍列为 `candidate`。其余账号的身份来源见目录里的 `wechat_identity_url`。一些刊方只提供二维码、没有可核验的账号名称，因此《哲学分析》《自然辩证法通讯》等暂不填猜测的账号。

## 公开发现与候选状态

在通过本 Skill 发起 `search`、`pull-now` 或周报的会话中，先执行：

```powershell
pfm journal-watch wechat-discovery-plan
pfm journal-watch wechat-discover
pfm journal-watch wechat-candidates
```

`wechat-discover` 对目录内每刊**已登记的刊方 HTTPS 页面**，按 Asia/Shanghai 日期每天至多读取一次。发起网络请求前，程序先在 Git 忽略的本地 SQLite 库中为该刊当天登记 `attempted`；并发调用、进程中断或当天刊方入口 URL 变化都不能再次消耗该刊当天额度。正常完成后登记 `ok` 或 `failed`；重复调用返回 `already_scanned`，其中 `prior_status` 保留原状态，`attempted` 也可能意味着前一次请求中断，下一当地日期仍可重新扫描。每个响应限 20 秒、750 KB、HTML、20 条公众号文章链接，不跨站跳转。它只从刊方页面抽取直达 `mp.weixin.qq.com/s/...` 的链接，不请求微信原帖，不把候选自动写成官方期次。`wechat-candidates --journal ID` 可筛选队列。页面没有外链或只用二维码时，零候选只表示这个页面未给出可抽取链接。

对于未覆盖的刊物，Codex 可在同一会话中用公开网页搜索或交互式浏览器，按账号名称、刊名、年份、期号和“目录”寻找确切原帖。检索时每刊保持有界，记下 URL、来源页面和失败状态。搜索结果不能直接标为 reviewed；只有登记刊方页面抽到的链接自动进入队列。会话检索获得的确切原帖可用 `pfm journal-watch wechat-add-candidate --journal ID --evidence-url URL --source-url HTTPS_SOURCE` 入队；队列明确保留发现来源，不能把搜索摘要伪装为刊方外链。

## 会话内核验

逐一打开候选原帖，核实 HTTPS 主机、显示账号、原帖日期、刊名、年份与期号、正文目录的完整可读性，并与刊方身份来源交叉核对。候选账号还须额外核实账号与该刊编辑部的关系。遇到验证页、登录、正文残缺或错刊，保留状态与理由：

```powershell
pfm journal-watch wechat-block --journal ID --evidence-url URL --reason "正文验证页"
pfm journal-watch wechat-dismiss --journal ID --evidence-url URL --reason "错刊"
```

只有实际读过完整原帖并核实期次、账号和明确日期后，才能执行：

```powershell
pfm journal-watch wechat-review --journal ID --year 2026 --issue 5 `
  --evidence-url URL --announcement-on 2026-10-09 --confirm-evidence
```

`wechat-review` 必须引用队列内候选；对 `candidate` 身份，还要显式提供 `--confirm-account-identity`。确认时，已复核期次与候选的 `reviewed` 状态在同一数据库事务中写入；任一步失败都不会留下只更新一侧的记录。`--confirm-evidence` 是会话核验的人工声明，命令本身不能证明原帖已读。原帖日期只记作公告日。只有原帖明确标出月份才传 `--label-month`；不可按期号推算。逐篇题名须逐项阅读，未读目录不能形成逐篇分母。已复核期次可按首次本地核实时间进入周报与即时拉取的刊方期次栏，历史检索按年份读取；公告日和刊期月份照原证据显示，不据此认定逐篇论文于本窗口发表。

## 无人值守边界

本地定时任务可运行刊方公开页面发现并保留候选，但它没有这些期刊公众号的文章列表授权，不能自动取得所有新帖或完成原帖正文核验。`pfm search`、`pull-now`、`weekly-run` 在无人值守时只读取本地已复核证据；本 Skill 在可浏览的会话中处理候选。需要登录或验证码时记为 `blocked`，不绕过。普通 HTTP 200、转载、账号同名、搜索摘要和图片缩略图不足以确证。微信和搜狗微信搜索公开 `robots.txt` 对相应路径有限制，本项目不把它们当作后台爬取入口。若后续获得获授权的列表服务，应另行核验授权、身份、正文范围、限额与故障状态。
