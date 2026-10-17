# WorkBuddy 本地 Skill 试点

本试点把项目现有 CLI 与证据政策接到 WorkBuddy 的本地技能导入入口。ZIP 包含 WorkBuddy 入口、选定项目根目录的公开 `SKILL.md`、该 Skill 引用的 16 份公开政策以及公开期刊目录，并附文件摘要清单。程序本身仍需在本机安装；私人 watchlist、状态、报告和凭据不进入包中。

## 选择来源并构建

在本试点分支的项目根目录运行：

```powershell
.\.venv\Scripts\python.exe scripts\build_workbuddy_skill.py --source-root '<项目根目录>'
```

省略 `--source-root` 时使用当前试点工作树。若要打包另一份正在修改的项目，明确指定其根目录；构建器只读取该目录的公开 Skill、政策、公开目录和项目元数据，不修改来源项目。新增政策链接若尚未进入打包白名单，构建会停止，避免遗漏新规则。输出放在 Git 忽略的 `var/workbuddy-pilot/`，文件名含 WorkBuddy Skill 版本和整套公开快照的摘要前缀；只改政策文件也会产生新的文件名。

在 WorkBuddy 客户端进入“技能市场 → 添加技能 → 创建／上传本地技能包”，选择新生成的 ZIP。不要上传整个项目目录或私人配置。让 WorkBuddy 能读取与快照对应的本机项目，并允许它执行该项目虚拟环境中的 `pfm`。若尚未建立虚拟环境，先按[安装说明](installation.md)安装项目。

包内的 `references/package-manifest.json` 保存来源 Skill 及各公开文件的 SHA-256。运行前核对本机项目根目录的 `SKILL.md` 与 `source_skill_sha256` 一致，并核对清单中其他政策文件与目录的摘要；若不一致，应重新打包当前项目或改用匹配的项目。 `pfm doctor` 报告的根目录也须与选定项目相同。如虚拟环境仍指向旧工作树，在当前项目目录运行 `uv sync --locked` 重新绑定。

试点包不会自动下载项目，也不配置 WorkBuddy 的定时任务或通知。更新项目 Skill 或政策后，重新构建并导入新 ZIP。

## 客户端验收

1. 导入并选中本地 Skill，确认 WorkBuddy 能读取包内的项目 Skill 快照和所需政策。
2. 在匹配的项目根目录运行 `pfm doctor` 与 `pfm onboarding-status`，核对 CLI 和根目录。
3. 在用户已确认的私人配置上，分别试指定年份的历史检索、七日即时拉取，以及周报 `--dry-run`。对比直接使用 CLI 的范围、来源状态与待复核队列。
4. 如遇作品类型冲突，核对 WorkBuddy 是否逐篇读取原件或出版方记录，并按证据指纹保存私人裁定；无法取得原件时保持未定。
5. 核对私人配置、报告和凭据没有进入 ZIP、公开仓库或外部对话。

没有 WorkBuddy 客户端时，只能完成包结构、文件摘要和本机 CLI 的离线验证，不能声称客户端导入或实际运行通过。
