# 第三方软件与数据来源声明

最后核验：2026-09-08

本文件说明 `philosophy-frontier-monitor` 直接依赖的软件，以及运行时访问的学术元数据来源。它不把
这些软件或数据纳入本项目自身的代码许可证，也不声称取得第三方论文全文、摘要或数据库的所有权。

## 1. Python 运行时依赖

项目源码不复制或修改下列依赖的源代码。安装工具根据 `pyproject.toml` 和 `uv.lock` 分别下载，
使用和再分发仍受各依赖自身许可证约束。

| 依赖 | 当前锁定版本 | 用途 | 上游许可证 |
|---|---:|---|---|
| [HTTPX](https://github.com/encode/httpx) | 0.28.1 | HTTPS 客户端 | [BSD 3-Clause](https://github.com/encode/httpx/blob/master/LICENSE.md) |
| [PyYAML](https://github.com/yaml/pyyaml) | 6.0.3 | 私人配置解析 | [MIT](https://github.com/yaml/pyyaml/blob/main/LICENSE) |
| [tzdata](https://github.com/python/tzdata) | 2026.3 | Windows 上的 IANA 时区数据 | [Apache-2.0](https://github.com/python/tzdata/blob/master/LICENSE) |

当前 HTTPX 依赖链还解析出 `anyio`、`certifi`、`httpcore`、`h11`、`idna` 和
`typing-extensions`。这些包没有被复制进本仓库；准确版本记录在 `uv.lock`，安装分发物所附的
license metadata 和 license files 是其许可证的权威随包副本。特别是，当前 `certifi` 元数据声明
MPL-2.0；本项目只依赖其已安装的 CA bundle，不把 certifi 代码重新许可为本项目许可证。

## 2. 构建与开发工具

这些工具用于构建、检查或测试，不属于本项目运行时 API：

| 工具 | 项目中的版本约束或锁定版本 | 上游许可证 |
|---|---:|---|
| [Hatchling](https://github.com/pypa/hatch) | `>=1.27` | [MIT](https://github.com/pypa/hatch/blob/master/LICENSE.txt) |
| [pytest](https://github.com/pytest-dev/pytest) | 8.4.2 | [MIT](https://github.com/pytest-dev/pytest/blob/main/LICENSE) |
| [pytest-cov](https://github.com/pytest-dev/pytest-cov) | 6.3.0 | [MIT](https://github.com/pytest-dev/pytest-cov/blob/master/LICENSE) |
| [Ruff](https://github.com/astral-sh/ruff) | 0.13.3 | [MIT](https://github.com/astral-sh/ruff/blob/main/LICENSE) |

它们的传递依赖及精确解析版本见 `uv.lock`。依赖升级时必须重新核验本文件；不能因为新版本沿用
同一包名就假定许可证和依赖关系没有变化。

## 3. 学术元数据与远程服务

### PhilPapers 与 PhilArchive

项目使用 PhilPapers 分类页面／RSS、认证 taxonomy JSON，以及 PhilArchive OAI-PMH。项目不会把
完整 PhilPapers taxonomy、批量引文数据、用户私人书目或论文全文放入公开仓库。

PhilPapers 的 [API 说明](https://philpapers.org/help/api)明确提醒 API 数据再分发受到严格限制；
[服务条款](https://philpapers.org/help/terms.html)进一步规定，未经书面同意不得下载或再分发不属于
PhilArchive OAI-PMH 范围的实质性 PhilPapers 引文或关联数据。运行者必须遵守当时有效的官方条款，
本项目代码许可证不提供对 PhilPapers 数据的额外权利。

公开仓库中的 PhilPapers 测试材料仅包括：

- 九项、明确标记为 `complete: false` 和 `fixture: true` 的最小分类结构夹具；
- 使用 `EXAMPLE` 标识和虚构标题的 RSS／HTML 协议夹具；
- 用于验证解析器、父子图、来源 ID 和安全边界的少量字段。

这些夹具不能替代、重建或批量分发 PhilPapers 数据库。正式 6153 分类快照只保存在用户本机、
Git 忽略的 `var/taxonomy/`。

### Crossref

项目使用 [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
核验 DOI 和出版日期。Crossref 官方的
[Metadata Retrieval](https://www.crossref.org/services/metadata-retrieval/)页面说明其元数据可公开获取和
再利用；客户端仍应标识自身、适当缓存、处理限速并遵守服务稳定性要求。

本仓库不批量附带 Crossref 数据、出版商全文或 PDF。单篇记录中的全文链接或作品许可字段也不表示
相应论文正文采用与 Crossref 元数据相同的开放条件。

### OpenAlex

项目使用 OpenAlex API 作为书目身份与日期核验来源。OpenAlex 的
[官方数据许可证说明](https://github.com/ourresearch/openalex-docs/blob/main/license.md)将 OpenAlex 数据置于
CC0；MAG-format snapshot 是其说明中单列的 ODC-BY 例外，本项目不使用或分发该快照格式。

OpenAlex 提供的论文位置、PDF URL 或开放获取状态不向本项目转让论文全文版权。项目第一版不下载
或再分发论文全文。

### OAI-PMH 协议

项目实现 OAI-PMH `ListRecords`／`resumptionToken` 客户端。协议本身只规定元数据交换方式，不为
具体仓库记录自动授予统一的内容再分发许可证。每个仓库的记录和全文仍服从其来源权利声明。

## 4. GitHub 设计参考

本项目对“近期记录”实现做过公开源码对照：

- [sea9401/philosophy-mcp](https://github.com/sea9401/philosophy-mcp/tree/96a39290ac7095db0fe8895b08d3e288c088c73f)，
  MIT License；其 OAI `from`／`until` 思路是本项目增量入口的直接设计启发；
- [tamnd/philpapers-cli](https://github.com/tamnd/philpapers-cli/tree/6c8a3b18e0ae41b337d27180b2e741b9d9a32af3)，
  Apache-2.0 License；用于对照检查无窗口和未消费分页令牌的 `Recent` 实现；
- [Sfgangloff/co-philosopher](https://github.com/Sfgangloff/co-philosopher/tree/fc9ba00e220419e45c30af47371b5368cfbcd038)，
  用于对照 RSS 年份与稿本状态提取；核验时 GitHub API 没有返回可确认的 SPDX 仓库许可证。

本仓库没有复制、翻译、修改或捆绑上述项目的源代码；OAI 适配器是独立 Python 实现。引用这些
项目不使其代码成为本项目 MIT License 的组成部分。固定版本、具体源文件、采用／未采用的设计和
差异见[设计来源、实现差异与独立贡献](references/design-lineage.md)。

## 5. 不属于本项目许可证的内容

无论维护者最终为项目代码选择何种许可证，它都不自动覆盖：

- PhilPapers taxonomy、引文、分类说明和用户资料；
- PhilArchive、Crossref 或 OpenAlex 返回的第三方论文内容；
- 论文标题以外可能受保护的摘要或正文；
- 出版商页面、PDF 和图像；
- 第三方 Python 包本身。

周报只保存完成识别、日期核验和标签交集所需的最小事实性书目信息。对外分享周报时，分享者仍需
自行检查其中第三方内容的适用权利。

## 6. 更新责任

以下变化发生时必须重新审查本文件和 `references/security-and-privacy.md`：

- `pyproject.toml` 或 `uv.lock` 的依赖发生变化；
- 增加新的学术数据库、全文来源或商业 API；
- 开始在发行包中捆绑第三方代码或数据；
- 公开夹具不再是最小虚构数据；
- 任一数据源的 API、访问条件、缓存范围或再分发条款发生变化。

本文件是当前项目边界的工程记录，不替代第三方许可证全文或适用法律下的专业意见。
