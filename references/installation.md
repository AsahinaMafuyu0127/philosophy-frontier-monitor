# 安装与首次部署

## 1. 支持范围

`philosophy-frontier-monitor` 是一个带本地 Python 程序的 Codex Skill。安装 Skill 只负责把仓库文件
放入 Codex 可发现目录；执行论文采集还需要在同一目录建立 Python 虚拟环境和私人配置。

当前要求：

- Codex 桌面应用、Codex CLI 或 IDE 扩展；
- Git；
- `uv`；
- Python `>=3.12,<3.14`；
- 可以访问所选学术数据源的网络环境。

Windows 已完成真实定时运行验收。macOS／Linux 的 Python 路径和文件语义已兼容，但 `v0.3.0`
尚未声明完成同等平台的无人值守运行验收。

## 2. 用 Skill Installer 安装

Codex 的内置 `$skill-installer` 支持从其他 GitHub 仓库安装。由于本仓库根目录就是 Skill 目录，
安装时必须明确使用路径 `.`，并指定目标名称 `philosophy-frontier-monitor`：

```text
$skill-installer 请从 https://github.com/AsahinaMafuyu0127/philosophy-frontier-monitor
仓库根目录安装这个 Skill；path 使用 .，安装名称使用 philosophy-frontier-monitor。
```

对应的安装器参数语义是：

```text
--repo AsahinaMafuyu0127/philosophy-frontier-monitor
--path .
--name philosophy-frontier-monitor
```

安装器默认把 Skill 放入 Codex 用户技能目录，并在目标已经存在时停止，避免无提示覆盖本地私人
配置。安装后在下一轮对话调用；如果没有出现，重启 Codex。

## 3. 建立 Python 环境

在安装器返回的 Skill 目录中运行：

```powershell
uv sync --python 3.12
```

此命令按照 `pyproject.toml` 与 `uv.lock` 建立项目内 `.venv`。运行 CLI 时必须以包含 `SKILL.md`
的目录为工作目录：

```text
Windows:      .venv\Scripts\pfm.exe
macOS/Linux:  .venv/bin/pfm
```

若 `.venv` 不存在，不要直接尝试在线周运行；先完成依赖同步和 `doctor` 检查。

## 4. 创建私人配置

Windows：

```powershell
Copy-Item .\config\watchlist.example.yaml .\config\watchlist.yaml
```

macOS／Linux：

```bash
cp ./config/watchlist.example.yaml ./config/watchlist.yaml
```

`config/watchlist.yaml`、`var/` 和 `reports/` 已被 Git 忽略。不要把私人配置复制回公开示例，也不要
为了求助而上传整个运行目录。

Windows 用户还应检查 `storage.state_database` 的实际位置。OAI 与书目缓存默认建立在该数据库
同级目录，上游批量元数据更新时可能显著增长；如果有容量充足的其他本地磁盘，应尽量避开空间紧张
的 `C:` 系统盘，例如把整套私人运行目录配置到 `F:` 或其他非系统盘。Skill 和 CLI 只提出建议，
不会未经授权移动现有配置、数据库、缓存或报告。

## 5. Taxonomy 与凭据

正式画像必须使用经过验证的完整 PhilPapers taxonomy。Skill 会指导用户取得 PhilPapers API ID
和 API key；凭据只应保存到安装目录下 Git 忽略的 `var/secrets/`，或者临时注入当前进程环境。
不要在聊天、Issue、截图、README、命令行参数或 Git 提交中粘贴凭据。

如果 PhilPapers 的命令行请求被 Cloudflare challenge 拦截，允许用户在正常私人浏览器会话中保存
同一官方 JSON，然后执行 `taxonomy-import` 验证；不得伪装浏览器、复用 Cookie、关闭 TLS 或绕过
访问控制。

OpenAlex key 不是建立兴趣画像所必需，但宽范围书目核验可能需要较多 credits。可选 key 同样只能
从私人文件或当前进程环境读取。

## 6. 首次运行顺序

首次部署必须按下列顺序完成：

```text
说明研究方向
  → 核验并确认 PhilPapers 分类
  → 导入完整 taxonomy
  → 建立历史基线
  → dry run
  → 正式周运行
  → 验证报告和检查点
  → 创建 Codex 每周计划任务
```

首次基线不会发送当前 feed 中的历史论文。只有报告文件写入成功并且 SQLite 总事务成功后，正式
周运行才推进检查点。创建计划任务前应完成失败恢复测试；默认计划是用户当地星期一 08:00，其他
时区、星期和当地时间可以在私人配置与计划任务中同步修改。

## 7. 从任意工作目录调用

Skill 被调用后，应把其自身 `SKILL.md` 所在目录解析为运行根目录，而不是假设用户当前正在该目录。
所有 `pfm` 命令、私人配置和报告路径都相对于这一根目录解析。若用户复制出单独的数据目录，必须
明确传入配置路径，不得从其他项目目录猜测或搜索私人配置。

## 8. 更新

`v0.3.0` 不提供覆盖式自动更新。安装器发现同名目标目录已经存在时会停止，以免覆盖私人画像、
状态库和报告。更新前应先审查新版本变更，保留私人目录和数据库备份，再由 Codex 在明确路径中
执行 Git 更新与 schema 检查；不得通过删除整个 Skill 目录来更新。

升级到 `v0.3.0` 后不需要重建 `state.sqlite3`、历史基线或通知历史。第一次使用 `pull-now`、
`weekly-run` 或 `catch-up` 时会在状态库同级私人目录自动创建 `oai-cache.sqlite3`；可用
`--no-oai-cache` 临时绕过。该文件是可再生成的 OAI 元数据缓存，不应提交、备份到公开仓库或与
私人画像混为同一个数据库。已有 `v0.2.0` OAI 缓存会就地增加中断会话与分页暂存表，无需重建；
可用 `pfm oai-cache status --config config/watchlist.yaml` 查看计数与恢复状态。只有明确需要释放旧
缓存空间时才运行 `pfm oai-cache prune --config config/watchlist.yaml --before <ISO-8601> --confirm`；
该操作不会修改 `state.sqlite3`、基线、检查点或通知历史。
