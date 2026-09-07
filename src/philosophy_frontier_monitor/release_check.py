"""Read-only public-release and privacy readiness checks."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024

REQUIRED_PUBLIC_FILES = (
    ".gitignore",
    "README.md",
    "SKILL.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "agents/openai.yaml",
    "config/watchlist.example.yaml",
)

REQUIRED_IGNORE_RULES = (
    ".venv/",
    "config/watchlist.yaml",
    "var/",
    "reports/",
    ".env",
    ".env.*",
    "!.env.example",
)

SKIPPED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "htmlcov",
    "reports",
    "var",
}

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

THIRD_PARTY_NOTICE_MARKERS = (
    "HTTPX",
    "PyYAML",
    "tzdata",
    "PhilPapers",
    "PhilArchive",
    "Crossref",
    "OpenAlex",
)

HIGH_CONFIDENCE_SECRET_PATTERNS = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "credential_query_parameter",
        re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|password)=([A-Za-z0-9._~-]{16,})"),
    ),
)


@dataclass(frozen=True, slots=True)
class ReleaseCheckItem:
    code: str
    status: str
    summary: str
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "status": self.status,
            "summary": self.summary,
            "details": list(self.details),
        }


@dataclass(frozen=True, slots=True)
class ReleaseAudit:
    project_root: Path
    repository_initialized: bool
    scanned_file_count: int
    checks: tuple[ReleaseCheckItem, ...]

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.checks if item.status == "blocker")

    @property
    def decision_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.checks if item.status == "decision_required")

    @property
    def warning_codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.checks if item.status == "warning")

    @property
    def release_ready(self) -> bool:
        return not self.blocker_codes and not self.decision_codes

    def to_dict(self) -> dict[str, object]:
        return {
            "project_root": str(self.project_root),
            "repository_initialized": self.repository_initialized,
            "scanned_file_count": self.scanned_file_count,
            "release_ready": self.release_ready,
            "blocker_codes": list(self.blocker_codes),
            "decision_codes": list(self.decision_codes),
            "warning_codes": list(self.warning_codes),
            "checks": [item.to_dict() for item in self.checks],
        }


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _git_repository(root: Path) -> tuple[bool, Path | None, tuple[str, ...]]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None, ()
    if result.returncode != 0:
        return False, None, ()
    repository_root = Path(result.stdout.strip()).resolve()
    try:
        tracked = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached"],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return True, repository_root, ()
    if tracked.returncode != 0:
        return True, repository_root, ()
    paths = tuple(
        item.decode("utf-8", errors="replace").replace("\\", "/")
        for item in tracked.stdout.split(b"\0")
        if item
    )
    return True, repository_root, paths


def _ignore_rules(root: Path) -> tuple[set[str], bool]:
    path = root / ".gitignore"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set(), False
    normalized = {
        line.strip().replace("\\", "/")
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }
    return normalized, True


def _is_private_path(relative_path: str) -> bool:
    path = relative_path.replace("\\", "/").lstrip("./")
    name = Path(path).name.casefold()
    suffix = Path(path).suffix.casefold()
    return bool(
        path == "config/watchlist.yaml"
        or path.startswith("var/")
        or path.startswith("reports/")
        or name == ".env"
        or (name.startswith(".env.") and name != ".env.example")
        or suffix in {".db", ".key", ".p12", ".pem", ".sqlite", ".sqlite3"}
    )


def _public_candidates(root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for directory, child_directories, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        child_directories[:] = sorted(
            name
            for name in child_directories
            if name not in SKIPPED_DIRECTORY_NAMES and not name.startswith(".pytest-tmp")
        )
        for filename in sorted(filenames):
            path = current / filename
            relative_path = _relative(path, root)
            if _is_private_path(relative_path):
                continue
            candidates.append(path)
    return tuple(candidates)


def _scan_text_files(root: Path, candidates: tuple[Path, ...]) -> tuple[list[str], list[str]]:
    findings: list[str] = []
    skipped: list[str] = []
    for path in candidates:
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped.append(_relative(path, root))
            continue
        if size > MAX_TEXT_SCAN_BYTES:
            skipped.append(_relative(path, root))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped.append(_relative(path, root))
            continue
        for detector, pattern in HIGH_CONFIDENCE_SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{_relative(path, root)}:{detector}")
    return sorted(set(findings)), sorted(set(skipped))


def _production_taxonomy_files(root: Path, candidates: tuple[Path, ...]) -> tuple[str, ...]:
    findings: list[str] = []
    for path in candidates:
        if path.suffix.casefold() != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("source") != "philpapers":
            continue
        if payload.get("complete") is True or payload.get("fixture") is False:
            findings.append(_relative(path, root))
    return tuple(sorted(findings))


def _load_interest_identity(path: Path) -> tuple[str | None, str | None]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        interest = payload.get("interest", {}) if isinstance(payload, dict) else {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return None, None
    if not isinstance(interest, dict):
        return None, None
    profile_id = interest.get("profile_id")
    original_text = interest.get("original_text")
    return (
        profile_id.strip() if isinstance(profile_id, str) else None,
        original_text.strip() if isinstance(original_text, str) else None,
    )


def audit_release(
    project_root: str | Path,
    *,
    private_config: str | Path | None = None,
) -> ReleaseAudit:
    """Audit release readiness without changing files, Git, or remote state."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")

    checks: list[ReleaseCheckItem] = []
    missing_public = tuple(name for name in REQUIRED_PUBLIC_FILES if not (root / name).is_file())
    checks.append(
        ReleaseCheckItem(
            code="required_public_files",
            status="blocker" if missing_public else "pass",
            summary=(
                "缺少公开发布所需的基础项目文件。" if missing_public else "公开项目的基础文件齐全。"
            ),
            details=missing_public,
        )
    )

    rules, ignore_exists = _ignore_rules(root)
    missing_rules = tuple(rule for rule in REQUIRED_IGNORE_RULES if rule not in rules)
    checks.append(
        ReleaseCheckItem(
            code="private_path_ignore_rules",
            status="blocker" if not ignore_exists or missing_rules else "pass",
            summary=(
                "Git 忽略规则没有覆盖全部私人运行路径。"
                if not ignore_exists or missing_rules
                else "私人配置、状态、报告、凭据和环境文件已有明确忽略规则。"
            ),
            details=missing_rules if ignore_exists else (".gitignore",),
        )
    )

    initialized, repository_root, tracked = _git_repository(root)
    if not initialized:
        checks.append(
            ReleaseCheckItem(
                code="git_repository",
                status="blocker",
                summary="项目尚未初始化为 Git 仓库；当前无法证明实际跟踪文件集合。",
            )
        )
    elif repository_root != root:
        checks.append(
            ReleaseCheckItem(
                code="git_repository",
                status="blocker",
                summary="项目根目录不是 Git 仓库顶层；发布边界需要人工确认。",
                details=(str(repository_root),),
            )
        )
    else:
        checks.append(
            ReleaseCheckItem(
                code="git_repository",
                status="pass",
                summary="项目根目录是可读取的 Git 仓库。",
            )
        )

    tracked_private = tuple(sorted(path for path in tracked if _is_private_path(path)))
    checks.append(
        ReleaseCheckItem(
            code="tracked_private_files",
            status="blocker" if tracked_private else ("pass" if initialized else "not_applicable"),
            summary=(
                "Git 已跟踪私人路径或高风险运行文件。"
                if tracked_private
                else (
                    "Git 跟踪集合中没有发现私人路径。"
                    if initialized
                    else "建立 Git 仓库后才能检查实际跟踪集合。"
                )
            ),
            details=tracked_private,
        )
    )

    candidates = _public_candidates(root)
    tracked_set = set(tracked)
    untracked_public = tuple(
        sorted(
            _relative(path, root) for path in candidates if _relative(path, root) not in tracked_set
        )
    )
    checks.append(
        ReleaseCheckItem(
            code="untracked_public_files",
            status=(
                "blocker"
                if initialized and repository_root == root and untracked_public
                else ("pass" if initialized and repository_root == root else "not_applicable")
            ),
            summary=(
                "拟公开文件中仍有未被 Git 跟踪的文件；实际首次提交面尚未固定。"
                if initialized and repository_root == root and untracked_public
                else (
                    "当前拟公开文件均已进入 Git 跟踪集合。"
                    if initialized and repository_root == root
                    else "建立项目根级 Git 仓库后才能比较拟公开文件与实际跟踪集合。"
                )
            ),
            details=untracked_public if initialized and repository_root == root else (),
        )
    )

    secret_findings, skipped_text = _scan_text_files(root, candidates)
    checks.append(
        ReleaseCheckItem(
            code="high_confidence_secret_scan",
            status="blocker" if secret_findings else "pass",
            summary=(
                "拟公开文本中发现高置信度凭据模式；结果只显示文件和检测器，不显示秘密值。"
                if secret_findings
                else "拟公开文本中未发现高置信度凭据模式。"
            ),
            details=tuple(secret_findings),
        )
    )
    if skipped_text:
        checks.append(
            ReleaseCheckItem(
                code="unscanned_public_files",
                status="warning",
                summary="部分拟公开文件因非 UTF-8、读取失败或超过大小上限而未做文本扫描。",
                details=tuple(skipped_text),
            )
        )

    production_taxonomies = _production_taxonomy_files(root, candidates)
    checks.append(
        ReleaseCheckItem(
            code="production_taxonomy_redistribution",
            status="blocker" if production_taxonomies else "pass",
            summary=(
                "拟公开文件中发现完整 PhilPapers taxonomy；不得随项目代码重新分发。"
                if production_taxonomies
                else "拟公开文件中没有发现完整 PhilPapers taxonomy。"
            ),
            details=production_taxonomies,
        )
    )

    example_config = root / "config" / "watchlist.example.yaml"
    private_path = (
        Path(private_config).resolve()
        if private_config is not None
        else root / "config" / "watchlist.yaml"
    )
    example_profile, example_text = _load_interest_identity(example_config)
    private_profile, private_text = _load_interest_identity(private_path)
    duplicate_fields: list[str] = []
    if private_path.is_file():
        if example_profile and private_profile and example_profile == private_profile:
            duplicate_fields.append("interest.profile_id")
        if example_text and private_text and example_text == private_text:
            duplicate_fields.append("interest.original_text")
    checks.append(
        ReleaseCheckItem(
            code="private_profile_separation",
            status="blocker" if duplicate_fields else "pass",
            summary=(
                "公开示例复制了私人画像字段；应改成明确的虚构示例。"
                if duplicate_fields
                else "公开示例未复制本机私人画像的身份或研究方向原文。"
            ),
            details=tuple(duplicate_fields),
        )
    )

    license_files = tuple(
        path.name
        for path in root.iterdir()
        if path.is_file() and path.name.casefold().startswith(("license", "copying"))
    )
    checks.append(
        ReleaseCheckItem(
            code="project_license",
            status="pass" if license_files else "decision_required",
            summary=(
                "项目许可证文件已经存在。"
                if license_files
                else "公开发布前需要由维护者选择项目代码许可证。"
            ),
            details=license_files,
        )
    )

    notices = root / "THIRD_PARTY_NOTICES.md"
    try:
        notice_text = notices.read_text(encoding="utf-8")
    except OSError:
        notice_text = ""
    missing_notice_markers = tuple(
        marker for marker in THIRD_PARTY_NOTICE_MARKERS if marker not in notice_text
    )
    checks.append(
        ReleaseCheckItem(
            code="third_party_notices",
            status="pass" if not missing_notice_markers else "blocker",
            summary=(
                "第三方声明覆盖直接运行依赖和当前四类学术数据来源。"
                if not missing_notice_markers
                else "公开发布前需要完成第三方依赖、数据来源和再分发边界声明。"
            ),
            details=missing_notice_markers,
        )
    )

    security_path = root / "SECURITY.md"
    try:
        security_text = security_path.read_text(encoding="utf-8")
    except OSError:
        security_text = ""
    contact_pending = "暂未公布" in security_text or "尚未公开发布" in security_text
    checks.append(
        ReleaseCheckItem(
            code="private_security_contact",
            status="decision_required" if contact_pending or not security_text else "pass",
            summary=(
                "公开发布前需要决定私密安全问题的接收渠道。"
                if contact_pending or not security_text
                else "SECURITY.md 已提供公开发布后的私密报告方式。"
            ),
        )
    )

    return ReleaseAudit(
        project_root=root,
        repository_initialized=initialized,
        scanned_file_count=len(candidates),
        checks=tuple(checks),
    )
