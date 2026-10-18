# 作品同一性逐对复核

## 复核对象与证据

当 Crossref 或 OpenAlex 的具体书目记录与 PhilPapers 记录作者相容，但题名可能是译名、
大幅改题或存在作者冲突时，程序不能把搜索排名当成同一性证据。周报、即时拉取与历史检索
把这对记录列入私人 `--review-queue-file`。每个 `identity_review_pairs` 条目保存来源名称、
外部记录 ID、稳定链接、题名、作者、DOI、来源发表日期、触发原因、比较器使用的证据项，
以及约束这两条记录的 `evidence_fingerprint`。队列中的外部日期仍须按日期政策解释；
OpenAlex 的 `YYYY-01-01` 可能只是年精度占位值。

逐篇打开 PhilPapers 原记录、外部记录和可访问的出版方或作品页面。核对 DOI、题名、
作者、载体、页码、版本关系，以及出版方是否明确给出译名或改题关系。相似题名、同名作者、
搜索排名和模型解释可以形成线索，不能单独成为 `equivalent` 裁定。若两篇文本在书目上
确为同一作品或有明确的版本关系，可裁定 `equivalent`；有确切证据表明两条记录属于
不同作品，裁定 `distinct`。证据不足时保留待审。一个来源返回多条可能同一的记录时，
每一对都要单独裁定；未裁定的比较仍阻止自动收录。

## 私人裁定文件

在配置的私人状态目录中保存 JSON，例如 `var/identity-reviews.json`：

```json
{
  "schema_version": 1,
  "reviews": [
    {
      "record_url": "https://philpapers.org/rec/EXAMPLE",
      "source": "Crossref",
      "external_record_id": "10.1234/example",
      "evidence_fingerprint": "从本轮 identity_review_pairs 逐字复制的 64 位十六进制值",
      "decision": "equivalent",
      "evidence_urls": ["https://doi.org/10.1234/example"],
      "reviewed_at": "2026-10-18T12:00:00+08:00",
      "note": "写明核对过的出版方字段和同一作品或版本关系的根据"
    }
  ]
}
```

例中的指纹文字只是格式占位符，运行时必须使用队列中的实际 64 位值。
`record_url`、`source`、`external_record_id` 和指纹共同限定裁定。题名、作者、
外部 DOI、日期、链接或比较理由变化会使旧指纹失效，并重新进入复核队列。
文件只保存在 Git 忽略的私人状态目录，不写入公开仓库、缓存或项目介绍页。

复跑使用同一个 `--identity-reviews` 参数：

```powershell
pfm weekly-run --config config/watchlist.yaml --dry-run --review-queue-file var/weekly-identity-queue.json
pfm weekly-run --config config/watchlist.yaml --dry-run --identity-reviews var/identity-reviews.json --review-queue-file var/weekly-identity-reviewed.json

pfm pull-now --config config/watchlist.yaml --days 7 --review-queue-file var/pull-identity-queue.json
pfm pull-now --config config/watchlist.yaml --days 7 --as-of <首次窗口结束时间> --review-queue-input var/pull-identity-queue.json --identity-reviews var/identity-reviews.json --review-queue-file var/pull-identity-reviewed.json

pfm search --config config/watchlist.yaml --year-from 2026 --year-to 2026 --review-queue-file var/search-identity-queue.json
pfm search --config config/watchlist.yaml --year-from 2026 --year-to 2026 --identity-reviews var/identity-reviews.json --review-queue-file var/search-identity-reviewed.json
```

即时拉取若要比较同一个七日窗口，应把首次结果 `window_end` 原样用于 `--as-of`，
并用首次队列的 feed 快照作为 `--review-queue-input`。周报正式运行前先用 `--dry-run`
核对队列；正式周报仍按状态库、已执行窗口及通知规则处理。历史检索有年份范围时，
无 feed 年份提示的记录仍须先有适用的 `--bibliographic-reviews` 出版方年份补证，
同一性裁定不能替代它。

`equivalent` 只允许外部书目记录进入原有旧作、日期、类型、分类、去重与窗口检查；
`distinct` 只排除这一条具体外部比较对象。两种裁定都不直接宣布“新发表”，
不能把未知作品类型改成论文，也不能改变 PhilPapers 分类成员关系。
