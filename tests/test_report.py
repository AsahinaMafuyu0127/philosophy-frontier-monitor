from datetime import UTC, date, datetime

from philosophy_frontier_monitor.interest import build_interest_profile
from philosophy_frontier_monitor.matching import match_work
from philosophy_frontier_monitor.models import (
    CategoryAssignment,
    CategoryStatus,
    DatePrecision,
    DateValue,
    FreshnessStatus,
    WorkRecord,
    WorkTypeEvidence,
    WorkTypeStatus,
)
from philosophy_frontier_monitor.report import (
    HumanReviewItem,
    SourceCoverage,
    render_on_demand_report,
    render_weekly_report,
)

START = datetime(2026, 8, 31, tzinfo=UTC)
END = datetime(2026, 9, 7, tzinfo=UTC)


def test_report_states_factual_match_and_date_evidence(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)
    assignment = CategoryAssignment(
        category_id="74924",
        category_name="Plato: Theaetetus",
        assignment_source="philpapers-category-feed",
        retrieved_at=END,
        mapping_method="category_feed_membership",
    )
    work = WorkRecord(
        work_id="pfm:work:test",
        title="A New Paper on the Theaetetus",
        authors=("Example Author",),
        observed_at=END,
        freshness_status=FreshnessStatus.CONFIRMED_NEW,
        category_status=CategoryStatus.AVAILABLE,
        category_assignments=(assignment,),
        doi="10.1234/example",
        publication_date=DateValue(date(2026, 9, 3), DatePrecision.DAY, "crossref"),
        freshness_event="recently_published_online",
        container_title="Example Journal",
        work_type="article",
        work_type_status=WorkTypeStatus.CONFIRMED,
        work_type_evidence=(
            WorkTypeEvidence(
                source="crossref",
                raw_type="journal-article",
                normalized_type="article",
                source_record_id="10.1234/example",
            ),
        ),
    )
    match = match_work(work, profile, now=END)

    report = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works={work.work_id: work},
        matches=(match,),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("philpapers-rss", "success", END, "分类 feed 已读取"),),
    )

    assert "A New Paper on the Theaetetus" in report
    assert "Plato: Theaetetus" in report
    assert "2026-09-03" in report
    assert "https://doi.org/10.1234/example" in report
    assert "类型证据状态：confirmed" in report
    assert "crossref：journal-article → article" in report
    assert "不评价论文质量" in report
    assert "不承诺对全球哲学新作的穷尽覆盖" in report
    assert "# Philosophy Frontier Weekly Report" in report
    assert "Publication-date evidence: 2026-09-03" in report
    assert "does not evaluate paper quality" in report
    assert report.count("A New Paper on the Theaetetus") == 2
    assert "\n\n---\n\n# Philosophy Frontier Weekly Report" in report


def test_zero_results_does_not_claim_none_exist_when_source_failed(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)

    report = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works={},
        matches=(),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("philpapers-rss", "failed", END, "HTTP 503"),),
    )

    assert "这不等于本周没有相关新作" in report


def test_remote_title_cannot_inject_a_markdown_heading(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)
    work = WorkRecord(
        work_id="pfm:work:inert-title",
        title="A Paper\n## Forged Section",
        authors=("A. Author",),
        observed_at=END,
        freshness_status=FreshnessStatus.CONFIRMED_NEW,
        category_status=CategoryStatus.AVAILABLE,
        category_assignments=(CategoryAssignment("74924", "Plato: Theaetetus", "fixture", END),),
    )
    match = match_work(work, profile, now=END)

    report = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works={work.work_id: work},
        matches=(match,),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("fixture", "success", END, "complete"),),
    )

    assert "\n## Forged Section" not in report
    assert "A Paper \\#\\# Forged Section" in report


def test_report_discloses_unresolved_candidates(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)

    report = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works={},
        matches=(),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("fixture", "success", END, "complete"),),
        unresolved_count=2,
        unresolved_reason_counts={"structured_work_type_conflict": 1},
    )

    assert "## 核验状态" in report
    assert "另有 2 条" in report
    assert "需要人工复核：1 条" in report
    assert "尚未分类的内部核验状态：1 条" in report
    assert "结构化来源在支持论文形式与不支持形式之间冲突：1 条" in report


def test_on_demand_report_discloses_unknown_structured_work_types(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)

    report = render_on_demand_report(
        profile=profile,
        snapshot=taxonomy,
        works={},
        matches=(),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("fixture", "success", END, "complete"),),
        unresolved_count=3,
        unresolved_reason_counts={"unknown_structured_work_type": 2},
    )

    assert "## 本次核验状态" in report
    assert "需要人工复核：2 条" in report
    assert "尚未分类的内部核验状态：1 条" in report
    assert "来源返回当前词表尚未识别的结构化作品类型：2 条" in report
    assert "# Philosophy Frontier On-Demand Report" in report
    assert "Human review required: 2" in report
    assert "not recognized by the current vocabulary: 2" in report
    assert "\n\n---\n\n# Philosophy Frontier On-Demand Report" in report


def test_on_demand_report_separates_machine_backlog_from_human_review(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)

    report = render_on_demand_report(
        profile=profile,
        snapshot=taxonomy,
        works={},
        matches=(),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("fixture", "success", END, "complete"),),
        unresolved_count=244,
        unresolved_reason_counts={"structured_work_type_conflict": 2},
        machine_deferred_count=242,
        human_review_items=(
            HumanReviewItem(
                title="A disputed paper",
                author_text="Ada Scholar",
                stable_url="https://philpapers.org/rec/TEST",
                reason_code="structured_work_type_conflict",
            ),
        ),
    )

    assert "机器核验积压：242 条" in report
    assert "不要求用户逐篇判断" in report
    assert "需要人工复核：2 条" in report
    assert "尚未分类的内部核验状态" not in report
    assert "需要人工复核的候选" in report
    assert "A disputed paper" in report
    assert "https://philpapers.org/rec/TEST" in report
