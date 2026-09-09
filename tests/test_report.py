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


def test_report_groups_sorts_and_collapses_recent_changes(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)
    assignment = CategoryAssignment("74924", "Plato: Theaetetus", "philpapers-category-feed", END)

    def paper(
        work_id: str,
        title: str,
        *,
        freshness_status: FreshnessStatus,
        event: str,
        publication: date | None = None,
        available: datetime | None = None,
    ) -> WorkRecord:
        return WorkRecord(
            work_id=work_id,
            title=title,
            authors=("Example Author",),
            observed_at=END,
            freshness_status=freshness_status,
            freshness_event=event,
            publication_date=(
                DateValue(publication, DatePrecision.DAY, "crossref")
                if publication is not None
                else None
            ),
            availability_date=(
                DateValue(available, DatePrecision.SECOND, "philarchive-oai")
                if available is not None
                else None
            ),
            category_status=CategoryStatus.AVAILABLE,
            category_assignments=(assignment,),
        )

    papers = (
        paper(
            "published-old",
            "Published September 2",
            freshness_status=FreshnessStatus.CONFIRMED_NEW,
            event="recently_published_online",
            publication=date(2026, 9, 2),
        ),
        paper(
            "published-new",
            "Published September 5",
            freshness_status=FreshnessStatus.CONFIRMED_NEW,
            event="recently_published_online",
            publication=date(2026, 9, 5),
        ),
        paper(
            "arrived-old",
            "Arrived September 3",
            freshness_status=FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
            event="recently_arrived_in_philpapers_alert",
            available=datetime(2026, 9, 3, tzinfo=UTC),
        ),
        paper(
            "arrived-new",
            "Arrived September 6",
            freshness_status=FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
            event="recently_arrived_in_philpapers_alert",
            available=datetime(2026, 9, 6, tzinfo=UTC),
        ),
        paper(
            "changed",
            "Changed September 7",
            freshness_status=FreshnessStatus.CONFIRMED_SOURCE_ARRIVAL,
            event="recently_changed_in_philarchive_oai",
            available=datetime(2026, 9, 7, tzinfo=UTC),
        ),
    )
    works = {item.work_id: item for item in papers}
    matches = tuple(match_work(item, profile, now=END) for item in papers)
    coverage = (SourceCoverage("fixture", "success", END, "complete"),)

    report = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works=works,
        matches=matches,
        window_start=START,
        window_end=END,
        coverage=coverage,
        unresolved_count=1,
        unresolved_reason_counts={"old_work_check_incomplete": 1},
    )
    chinese = report.split("\n\n---\n\n# Philosophy Frontier Weekly Report", maxsplit=1)[0]

    published_heading = chinese.index("### Recently published｜确认新出（2 篇）")
    arrived_heading = chinese.index("### Recently arrived｜新近进入来源（2 篇）")
    changed_heading = chinese.index("### Recently changed｜来源记录近期变化（1 篇）")
    coverage_heading = chinese.index("## 数据源覆盖")
    verification_heading = chinese.index("## 核验状态")
    assert published_heading < arrived_heading < changed_heading < coverage_heading
    assert coverage_heading < verification_heading
    assert chinese.index("Published September 5") < chinese.index("Published September 2")
    assert chinese.index("Arrived September 6") < chinese.index("Arrived September 3")
    assert "<details>" in chinese
    assert "<details open>" not in chinese
    assert "显示／隐藏 recently changed（1 篇；默认隐藏）" in chinese
    assert chinese.count("\n---\n") == 2

    expanded = render_weekly_report(
        profile=profile,
        snapshot=taxonomy,
        works=works,
        matches=matches,
        window_start=START,
        window_end=END,
        coverage=coverage,
        show_recently_changed=True,
    )
    assert "<details open>" in expanded
    assert "显示／隐藏 recently changed（1 篇；本报告默认展开）" in expanded


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


def test_on_demand_report_separates_per_run_remote_deferral_from_human_review(taxonomy):
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
        remote_verification_not_reached_count=242,
        human_review_items=(
            HumanReviewItem(
                title="A disputed paper",
                author_text="Ada Scholar",
                stable_url="https://philpapers.org/rec/TEST",
                reason_code="structured_work_type_conflict",
            ),
        ),
    )

    assert "本次未轮到远程核验：242 条" in report
    assert "不表示跨次积压" in report
    assert "不要求用户逐篇判断" in report
    assert "需要人工复核：2 条" in report
    assert "尚未分类的内部核验状态" not in report
    assert "需要人工复核的候选" in report
    assert "A disputed paper" in report
    assert "https://philpapers.org/rec/TEST" in report


def test_on_demand_report_leads_with_confirmed_new_and_source_arrivals(taxonomy):
    profile = build_interest_profile("我研究《泰阿泰德》。", taxonomy, now=START)

    report = render_on_demand_report(
        profile=profile,
        snapshot=taxonomy,
        works={},
        matches=(),
        window_start=START,
        window_end=END,
        coverage=(SourceCoverage("fixture", "success", END, "complete"),),
        unresolved_count=266,
        unresolved_reason_counts={},
        remote_verification_not_reached_count=266,
        undated_quarantine_count=182,
        undated_quarantine_new=182,
        matched_confirmed_new=1,
        matched_confirmed_source_arrivals=91,
    )

    assert "### 确认新出：1 篇" in report
    assert "### PhilPapers 新近来源：91 篇" in report
    assert "## 无日期记录集合" in report
    assert "182 条是本次首次进入私人散列隔离集合" in report
    assert report.index("### 确认新出：1 篇") < report.index("本次未轮到远程核验：266 条")
    assert "### Confirmed new: 1" in report
    assert "### Recent PhilPapers source arrivals: 91" in report
