"""Conservative work-level deduplication decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .identity import IdentityMatchLevel, authors_equivalent, compare_bibliographic_identity
from .models import WorkRecord
from .normalize import normalize_doi


class DeduplicationKind(StrEnum):
    AUTOMATIC_MERGE = "automatic_merge"
    CANDIDATE_DUPLICATE = "candidate_duplicate"
    DISTINCT = "distinct"


@dataclass(frozen=True, slots=True)
class DeduplicationDecision:
    kind: DeduplicationKind
    reason: str
    evidence: tuple[str, ...]


def compare_works(left: WorkRecord, right: WorkRecord) -> DeduplicationDecision:
    """Compare two works without letting weak title evidence trigger a merge."""

    shared_source_ids = set(left.source_ids).intersection(right.source_ids)
    if shared_source_ids:
        return DeduplicationDecision(
            DeduplicationKind.AUTOMATIC_MERGE,
            "相同数据源明确使用了相同来源 ID。",
            tuple(f"{source}:{source_id}" for source, source_id in sorted(shared_source_ids)),
        )

    left_doi = normalize_doi(left.doi)
    right_doi = normalize_doi(right.doi)
    if left_doi is not None and left_doi == right_doi:
        identity = compare_bibliographic_identity(
            left.title,
            left.authors[0] if left.authors else None,
            right.title,
            right.authors,
            shared_identifier=True,
        )
        type_conflict = bool(
            left.work_type and right.work_type and left.work_type != right.work_type
        )
        if identity.level is IdentityMatchLevel.EQUIVALENT and not type_conflict:
            return DeduplicationDecision(
                DeduplicationKind.AUTOMATIC_MERGE,
                "规范化 DOI 相同，且作者首位与作品类型没有明显冲突。",
                (f"doi:{left_doi}",),
            )
        return DeduplicationDecision(
            DeduplicationKind.CANDIDATE_DUPLICATE,
            "DOI 相同但关键书目信息冲突，必须复核后才能合并。",
            (f"doi:{left_doi}",),
        )

    identity = compare_bibliographic_identity(
        left.title,
        left.authors[0] if left.authors else None,
        right.title,
        right.authors,
    )
    same_first_author = (
        authors_equivalent(left.authors[0], right.authors) is True if left.authors else False
    )
    if identity.level is not IdentityMatchLevel.DISTINCT or same_first_author:
        return DeduplicationDecision(
            DeduplicationKind.CANDIDATE_DUPLICATE,
            "作品同一性证据值得复核，但缺少共同稳定标识符，不自动合并。",
            identity.evidence if identity.evidence else ("compatible_first_author",),
        )

    return DeduplicationDecision(
        DeduplicationKind.DISTINCT,
        "没有发现足以合并或进入候选复核的标识证据。",
        (),
    )
