# WorkBuddy 本地 Skill 试点

本试点把仓库的现有 CLI 与证据政策接到 WorkBuddy 的“导入本地技能包”入口。ZIP 只包含 WorkBuddy 入口 `SKILL.md`；实际运行仍需本机的项目、Python 环境和可访问的 `pfm` 命令。它不会把私人 watchlist、状态、报告或凭据装进包中。

## 构建和导入

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\build_workbuddy_skill.py
```

输出为 Git 忽略的 `var/workbuddy-pilot/philosophy-frontier-monitor-workbuddy.zip`。在 WorkBuddy 客户端进入“技能市场 → 添加技能 → 创建／上传本地技能包”，选择该 ZIP。不要上传整个项目目录或私人配置。导入后，把本地项目根目录作为 WorkBuddy 可访问的工作区；客户端需具备读取本机文件和执行命令的能力。若尚未建立虚拟环境，先按[安装说明](installation.md)安装项目。这里不需要把任何 API key 粘贴到对话里。

试点包不会自动下载项目，也不配置 WorkBuddy 的定时任务或通知。它借用项目根目录的 `SKILL.md` 和现有政策文件，因此后续证据规则更新仍以本地项目为准。

## 客户端验收

1. 确认本地 Skill 能导入并被选中，且能读取项目根目录的 `SKILL.md`。
2. 要求 WorkBuddy 在项目根目录运行 `pfm doctor` 与 `pfm onboarding-status`，核对 CLI 可用性及 `doctor` 返回的项目根目录。如虚拟环境仍指向旧工作树，先在当前项目目录运行 `uv sync --locked` 重新绑定，再做私人检索。
3. 在用户已经确认的私人配置上，分别试一个指定年份的历史检索、一个七日即时拉取，以及周报的 `--dry-run`。对比直接使用 CLI 的范围、来源状态与待复核队列。
4. 如遇作品类型冲突，核对 WorkBuddy 是否逐篇读取原件或出版方记录，并按证据指纹保存私人裁定；无法取得原件时保持未定。
5. 核对私人配置、报告和凭据没有进入 ZIP、公开仓库或外部对话。

本次只完成了离线构建和本机检查；未安装 WorkBuddy 客户端，因此上述导入与实际运行验收仍待客户端可用时执行。
