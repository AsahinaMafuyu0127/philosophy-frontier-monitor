from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

import philosophy_frontier_monitor.journal_collection as journal_collection
from philosophy_frontier_monitor.cli import build_parser
from philosophy_frontier_monitor.journal_collection import (
    MAX_HTML_BYTES,
    MAX_PDF_BYTES,
    _cbpt_page,
    _cbpt_toc,
    _connect,
    _cycle_allocations,
    _fetch,
    _fetch_pdf,
    _identity,
    _jdn_toc,
    _kongzi_index,
    _kongzi_toc,
    _sdu_index,
    _sdu_toc,
    _sysu_article_page,
    _sysu_index,
    _sysu_toc,
    check_publisher_articles,
    collect_publisher,
    issue_coverage_summary,
    measurement_summary,
    observation_summary,
    review_publisher_article,
    run_publisher_cycle,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_jdn_toc_keeps_article_and_author() -> None:
    html = (
        "<h1>2026年9期</h1>"
        '<a href="/home/journal/view/id/5358">饮食与存在研究</a>'
        '<a href="/home/retrieval/lists/id/3344">王甲</a>'
    )
    issue, articles = _jdn_toc(
        html, "https://jdn.ucas.ac.cn/home/journal/cataloglist/cid/349", "2026-09-27T10:00:00+08:00"
    )
    assert issue == "9"
    assert articles[0].author == "王甲"
    assert articles[0].url == "https://jdn.ucas.ac.cn/home/journal/view/id/5358"


def test_sdu_toc_uses_announcement_day_only() -> None:
    index = '<a href="../info/1033/2558.htm">《周易研究》2026年第4期</a>'
    year, issue, url = _sdu_index(index, "https://zhouyi.sdu.edu.cn/zyyjxk/qkml.htm")
    assert (year, issue, url) == (2026, "4", "https://zhouyi.sdu.edu.cn/info/1033/2558.htm")
    page = (
        "<h2>《周易研究》2026年第4期</h2><span>日期：2026-08-21</span>"
        '<div id="vsb_content">'
        '<section style="text-align: center; line-height: 2; font-size: 12px;">'
        "<p>易学中的形而上问题</p><p>李乙</p></section></div>"
    )
    announced, articles = _sdu_toc(page, year, issue, url, "2026-09-27T10:00:00+08:00")
    assert announced == "2026-08-21"
    assert len(articles) == 1
    assert articles[0].title == "易学中的形而上问题"


def test_kongzi_site_toc_checks_every_title_against_abstract_section(tmp_path) -> None:
    home = "https://www.chinakongzi.org/category/kongziyanjiu_qikan/"
    issue_url = "https://www.chinakongzi.org/content/6147_589032.html"
    index = (
        '<a href="/content/6147_589032.html">'
        "《孔子研究》2026年第4期（总第216期）目录</a>"
    )
    year, issue, selected = _kongzi_index(index, home)
    assert (year, issue, selected) == (2026, "4", issue_url)
    rows = [f"作者{i}丨儒学问题研究之{i}" for i in range(1, 6)]
    issue_html = (
        "<h3>《孔子研究》2026年第4期（总第216期）目录</h3></div>"
        "<h6>2026-08-13 11:07:53</h6>"
        '<div id="page-content"><p>●中国哲学●</p>'
        + "".join(f"<p>{row}</p>" for row in rows)
        + "".join(f"<p><strong>儒学问题研究之{i}</strong></p>" for i in range(1, 6))
        + "</div>"
    )
    announced, articles = _kongzi_toc(
        issue_html, year, issue, issue_url, "2026-09-28T10:00:00+08:00"
    )
    assert announced == "2026-08-13"
    assert len(articles) == 5
    assert articles[0].author == "作者1"
    assert all(article.url == issue_url for article in articles)
    with pytest.raises(ValueError, match="abstract_crosscheck"):
        _kongzi_toc(
            issue_html.replace("<strong>儒学问题研究之5</strong>", "<strong>另一篇</strong>"),
            year, issue, issue_url, "2026-09-28T10:00:00+08:00",
        )

    def handler(request: httpx.Request) -> httpx.Response:
        body = index if request.url.path.startswith("/category/") else issue_html
        return httpx.Response(200, headers={"content-type": "text/html"}, text=body)

    db = tmp_path / "publisher.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = collect_publisher("kongzi-yanjiu", db, client=client)
    assert result["articles"] == 5
    assert result["issue_status"] == "pending"
    with _connect(db) as saved:
        label_month, announcement = saved.execute(
            "SELECT label_month, announcement_on FROM official_issues"
        ).fetchone()
    assert (label_month, announcement) == (None, "2026-08-13")
    assert issue_coverage_summary(db)[0]["reference_denominator"] == 5


def test_cbpt_full_toc_extracts_link_and_author() -> None:
    url = "https://zxyj.cbpt.cnki.net/portal/journal/portal/client/paper_list/type_benqi"
    page = (
        "<h3>2026年 08期</h3>"
        '<div class="paperBox"><h3><a href="https://zxyj.cbpt.cnki.net/portal/journal/'
        'portal/client/paper/1c28b1c9ea6321925812306afd5787d6">仁爱感通研究'
        "</a></h3><span>王乙;</span></div>"
    )
    year, issue, articles = _cbpt_toc(
        page, url, "2026-09-27T10:00:00+08:00", "zhexue-yanjiu", "zxyj.cbpt.cnki.net"
    )
    assert (year, issue, len(articles)) == (2026, "8", 1)
    assert articles[0].author == "王乙"


def test_cbpt_page_rejects_other_issue_or_host() -> None:
    page = (
        '<a href="https://other.example/portal/journal/portal/client/paperPage_list?'
        'pageNum=2&amp;year=2026&amp;issue=08">wrong host</a>'
        '<a href="https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paperPage_list?'
        'pageNum=2&amp;year=2025&amp;issue=08">wrong year</a>'
        '<a href="https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paperPage_list?'
        'pageNum=2&amp;year=2026&amp;issue=08">next</a>'
    )
    assert _cbpt_page(page, "zxdt.cbpt.cnki.net", 2026, "8", 2) == (
        "https://zxdt.cbpt.cnki.net/portal/journal/portal/client/paperPage_list?"
        "pageNum=2&year=2026&issue=08"
    )


def test_sysu_pdf_toc_keeps_explicit_issue_month_separate_from_post_date() -> None:
    index = '<a href="/article/25746">2026-06-29 现代哲学2026年第2期</a>'
    year, issue, article_url = _sysu_index(index, "https://mphilosophy.sysu.edu.cn/cat/124")
    assert (year, issue, article_url) == (
        2026, "2", "https://mphilosophy.sysu.edu.cn/article/25746"
    )
    page = (
        "<span>发布日期：2026-06-29</span>"
        '<a href="/sites/default/files/2026-06/test.pdf">'
        "03_现代哲学2026年第2期中文目录.pdf</a>"
    )
    announced, pdf_url = _sysu_article_page(page, article_url, "mphilosophy.sysu.edu.cn")
    assert (announced, pdf_url) == (
        "2026-06-29", "https://mphilosophy.sysu.edu.cn/sites/default/files/2026-06/test.pdf"
    )
    text = (FIXTURES / "xiandai_toc_extracted.txt").read_text(encoding="utf-8")
    month, articles = _sysu_toc(text, year, issue, pdf_url,
                                "2026-09-27T10:00:00+08:00")
    assert month == "2026-03"
    assert len(articles) == 5
    assert articles[1].title == "测试哲学中的表达——论语言与理解"
    assert articles[2].author == "作者丙 作者丁"
    with pytest.raises(ValueError, match="heading_mismatch"):
        _sysu_toc(text, 2026, "3", pdf_url, "2026-09-27T10:00:00+08:00")
    with pytest.raises(ValueError, match="article_count_or_month_mismatch"):
        _sysu_toc(text.replace("*5*2026-3", "*6*2026-3"), 2026, "2", pdf_url,
                  "2026-09-27T10:00:00+08:00")


def test_sysu_pdf_fetch_rejects_error_pages_redirects_and_large_files() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("redirect.pdf"):
            return httpx.Response(302, headers={"location": "https://other.example/file.pdf"})
        if path.endswith("html.pdf"):
            return httpx.Response(200, headers={"content-type": "text/html"}, text="blocked")
        if path.endswith("large.pdf"):
            return httpx.Response(200, headers={"content-type": "application/pdf"},
                                  content=b"%PDF" + b"x" * MAX_PDF_BYTES)
        return httpx.Response(200, headers={"content-type": "application/pdf"},
                              content=b"not a PDF")

    host = "mphilosophy.sysu.edu.cn"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        for suffix, reason in (
            ("redirect", "publisher_pdf_redirect"),
            ("html", "publisher_not_pdf"),
            ("large", "publisher_pdf_too_large"),
            ("invalid", "publisher_invalid_pdf_header"),
        ):
            with pytest.raises(ValueError, match=reason):
                _fetch_pdf(client, f"https://{host}/{suffix}.pdf", host)


def test_sysu_collection_keeps_pdf_evidence_pending(tmp_path, monkeypatch) -> None:
    host = "mphilosophy.sysu.edu.cn"
    pdf_url = f"https://{host}/sites/default/files/2026-06/test.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cat/124":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                '<a href="/article/25746">2026-06-29 现代哲学2026年第2期</a>'
            ))
        if request.url.path == "/article/25746":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                '<span>发布日期：2026-06-29</span><a href="/sites/default/files/2026-06/'
                'test.pdf">03_现代哲学2026年第2期中文目录.pdf</a>'
            ))
        return httpx.Response(200, headers={"content-type": "application/pdf"},
                              content=b"%PDF-synthetic")

    text = (FIXTURES / "xiandai_toc_extracted.txt").read_text(encoding="utf-8")
    monkeypatch.setattr(journal_collection, "_extract_pdf_text", lambda _content: (text, 2))
    db = tmp_path / "publisher.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = collect_publisher("xiandai-zhexue", db, client=client)
    assert result == {"journal": "xiandai-zhexue", "year": 2026, "issue": "2",
                      "articles": 5, "pages": 3, "issue_status": "pending",
                      "issue_url": pdf_url, "pdf_pages": 2}
    with sqlite3.connect(db) as saved:
        evidence = saved.execute(
            "SELECT label_month, announcement_on, status FROM official_issues ORDER BY evidence_url"
        ).fetchall()
        assert evidence == [(None, "2026-06-29", "pending"),
                            ("2026-03", None, "pending")]
    assert issue_coverage_summary(db)[0]["reference_denominator"] == 5

    fresh = tmp_path / "too-bounded.sqlite3"
    with (
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client,
        pytest.raises(ValueError, match="sysu_requires_three_request_pages"),
    ):
        collect_publisher("xiandai-zhexue", fresh, client=client, max_pages=2)
    assert not fresh.exists()


def test_multi_author_toc_compares_first_author() -> None:
    official = {"title": "反对奠基—因果统一命题", "author": "马迅;李乙", "year": 2026, "issue": "8"}
    assert (
        _identity(
            official,
            official["title"],
            ("马迅", "李乙"),
            "自然辩证法研究",
            2026,
            "8",
            "自然辩证法研究",
        )
        == "hit"
    )


def test_cycle_budget_rotates_across_all_sites() -> None:
    journals = ("a", "b", "c", "d")
    assert _cycle_allocations(journals, 0, 2, 0) == dict.fromkeys(journals, 0)
    assert _cycle_allocations(journals, 6, 2, 0) == {"a": 2, "b": 2, "c": 1, "d": 1}
    assert _cycle_allocations(journals, 2, 2, 1) == {"a": 0, "b": 1, "c": 1, "d": 0}
    with pytest.raises(ValueError, match="invalid cycle bounds"):
        _cycle_allocations(journals, 31, 2, 0)


def test_wanfang_article_check_requires_explicit_opt_in(tmp_path, monkeypatch) -> None:
    path = tmp_path / "issues.sqlite3"
    with _connect(path) as db:
        db.execute(
            "INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "zhouyi-yanjiu", 2026, "4", "易学新论", "李乙",
                "https://zhouyi.sdu.edu.cn/a", "https://zhouyi.sdu.edu.cn/issue",
                "2026-08-21T10:00:00+08:00", "2026-08-21T10:00:00+08:00",
            ),
        )
    called: list[str] = []

    def read_key(*_args):
        called.append("read_key")
        return "test-key", None

    def cnki_check(*_args):
        called.append("cnki")
        return "hit", "https://example.org/cnki", None

    def wanfang_check(*_args):
        called.append("wanfang")
        return "miss", None, None

    monkeypatch.setattr(journal_collection, "_read_key", read_key)
    monkeypatch.setattr(journal_collection, "_cnki_check", cnki_check)
    monkeypatch.setattr(journal_collection, "_wanfang_check", wanfang_check)
    default = check_publisher_articles(
        "zhouyi-yanjiu", path, checked_at="2026-09-27T10:00:00+08:00"
    )
    assert [item["source"] for item in default["checks"]] == ["cnki"]
    assert called == ["cnki"]

    called.clear()
    opted_in = check_publisher_articles(
        "zhouyi-yanjiu", path, with_wanfang=True, min_interval_hours=0,
        checked_at="2026-09-28T10:00:00+08:00",
    )
    assert [item["source"] for item in opted_in["checks"]] == ["cnki", "wanfang"]
    assert called == ["read_key", "cnki", "wanfang"]


def test_journal_watch_cli_defaults_to_no_wanfang() -> None:
    parser = build_parser()
    default = parser.parse_args(["journal-watch", "run"])
    assert default.with_wanfang is False
    assert default.max_total_checks == 0
    assert parser.parse_args(["journal-watch", "run", "--with-wanfang"]).with_wanfang is True


def test_default_cycle_scans_all_sites_without_article_queries(tmp_path, monkeypatch) -> None:
    scanned: list[str] = []

    def collect(journal_id, *_args, **_kwargs):
        scanned.append(journal_id)
        return {"journal": journal_id}

    def unexpected_check(*_args, **_kwargs):
        raise AssertionError("default cycle must not query article databases")

    monkeypatch.setattr(journal_collection, "collect_publisher", collect)
    monkeypatch.setattr(journal_collection, "check_publisher_articles", unexpected_check)
    result = run_publisher_cycle(tmp_path / "unused.sqlite3")
    assert len(scanned) == result["sites_attempted"] == 9
    assert result["article_check_budget"] == result["articles_checked"] == 0
    assert result["sites_failed"] == 0


def test_publisher_fetch_rejects_redirect_and_large_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "https://elsewhere.example/"})
        return httpx.Response(
            200, headers={"content-type": "text/html"}, content=b"x" * (MAX_HTML_BYTES + 1)
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="publisher_redirect"):
            _fetch(client, "https://jdn.ucas.ac.cn/redirect", "jdn.ucas.ac.cn")
        with pytest.raises(ValueError, match="publisher_page_too_large"):
            _fetch(client, "https://jdn.ucas.ac.cn/large", "jdn.ucas.ac.cn")


def test_publisher_fetch_distinguishes_rate_limit_and_non_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/limited":
            return httpx.Response(429, headers={"content-type": "text/html"})
        return httpx.Response(200, headers={"content-type": "application/json"}, json={})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(ValueError, match="publisher_http_429"):
            _fetch(client, "https://jdn.ucas.ac.cn/limited", "jdn.ucas.ac.cn")
        with pytest.raises(ValueError, match="publisher_not_html"):
            _fetch(client, "https://jdn.ucas.ac.cn/not-html", "jdn.ucas.ac.cn")


def test_publisher_fetch_reports_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("publisher did not respond", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client,
        pytest.raises(ValueError, match="publisher_timeout"),
    ):
        _fetch(client, "https://jdn.ucas.ac.cn/slow", "jdn.ucas.ac.cn")


def test_publisher_page_bound_does_not_commit_partial_catalog(tmp_path) -> None:
    host = "zxyj.cbpt.cnki.net"
    paper = f"https://{host}/portal/journal/portal/client/paper/" + "a" * 32

    def page(next_page: int | None) -> str:
        next_link = (
            f'<a href="https://{host}/portal/journal/portal/client/paperPage_list?'
            f'pageNum={next_page}&amp;year=2026&amp;issue=08">下一页</a>'
            if next_page else ""
        )
        return (
            "<h3>2026年 08期</h3>"
            f'<div class="paperBox"><h3><a href="{paper}">伦理学研究论文'
            f'{next_page or 1}</a></h3><span>王乙;</span></div>{next_link}'
        )

    def handler(request: httpx.Request) -> httpx.Response:
        next_page = 3 if request.url.params.get("pageNum") == "2" else 2
        return httpx.Response(200, headers={"content-type": "text/html"}, text=page(next_page))

    db = tmp_path / "publisher.sqlite3"
    with (
        httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client,
        pytest.raises(ValueError, match="cbpt_toc_truncated_by_page_bound"),
    ):
        collect_publisher("zhexue-yanjiu", db, client=client, max_pages=2)
    assert not db.exists()


def test_first_publisher_scan_creates_issue_store_after_article_catalog(tmp_path) -> None:
    host = "zxyj.cbpt.cnki.net"
    article_url = f"https://{host}/portal/journal/portal/client/paper/" + "a" * 32

    def handler(_request: httpx.Request) -> httpx.Response:
        html = (
            "<h3>2026年 08期</h3>"
            f'<div class="paperBox"><h3><a href="{article_url}">伦理学研究论文'
            "</a></h3><span>王乙;</span></div>"
        )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    db = tmp_path / "publisher.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = collect_publisher("zhexue-yanjiu", db, client=client)
    assert result["articles"] == 1
    assert result["issue_status"] == "pending"
    with sqlite3.connect(db) as saved:
        assert saved.execute("SELECT COUNT(*) FROM publisher_articles").fetchone()[0] == 1
        assert saved.execute("SELECT COUNT(*) FROM official_issues").fetchone()[0] == 1
        assert saved.execute("SELECT COUNT(*) FROM publisher_issue_scans").fetchone()[0] == 1


def test_issue_audit_distinguishes_toc_and_manual_research_denominators(tmp_path) -> None:
    host = "zxyj.cbpt.cnki.net"
    urls = [f"https://{host}/portal/journal/portal/client/paper/{ch * 32}"
            for ch in ("a", "b")]

    def handler(_request: httpx.Request) -> httpx.Response:
        boxes = "".join(
            f'<div class="paperBox"><h3><a href="{url}">'
            f'测试目录论文{number}</a></h3><span>作者甲;</span></div>'
            for number, url in enumerate(urls, 1)
        )
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text=f"<h3>2026年 08期</h3>{boxes}")

    db = tmp_path / "publisher.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        collect_publisher("zhexue-yanjiu", db, client=client)
    before = issue_coverage_summary(db, journal_id="zhexue-yanjiu")[0]
    assert before["toc_complete"] is True
    assert before["reference_denominator"] == 2
    assert before["research_denominator"] is None
    assert before["interest_denominator"] is None
    assert before["research_source_checks"]["cnki"]["visibility_denominator"] is None
    assert before["work_types"]["unreviewed"] == 2
    assert before["research_interest"]["relevant"] == 0
    assert before["source_checks"]["cnki"]["unchecked"] == 2

    review_publisher_article(
        db, "zhexue-yanjiu", 2026, "8", "测试目录论文1", urls[0],
        work_type="research", interest="relevant",
    )
    partial = issue_coverage_summary(db, journal_id="zhexue-yanjiu")[0]
    assert partial["research_denominator"] is None
    assert partial["interest_denominator"] is None
    review_publisher_article(
        db, "zhexue-yanjiu", 2026, "8", "测试目录论文2", urls[1],
        work_type="other", interest="unreviewed",
    )
    after = issue_coverage_summary(
        db, journal_id="zhexue-yanjiu", year=2026, issue="8", include_articles=True
    )[0]
    assert after["reference_denominator"] == 2
    assert after["research_denominator"] == 1
    assert after["interest_denominator"] == 1
    assert after["research_source_checks"]["cnki"] == {
        "complete_hits": 0, "complete_misses": 0, "unresolved": 1,
        "visibility_denominator": None,
    }
    assert after["work_types"] == {
        "research": 1, "other": 1, "uncertain": 0, "unreviewed": 0,
    }
    assert after["research_interest"] == {
        "relevant": 1, "irrelevant": 0, "uncertain": 0, "unreviewed": 0,
    }
    assert len(after["articles"]) == 2
    with _connect(db) as saved:
        saved.execute("INSERT INTO publisher_article_checks VALUES (?,?,?,?,?,?,?,?,?)", (
            "zhexue-yanjiu", 2026, "8", "测试目录论文1", "cnki",
            "2026-09-28T10:00:00+08:00", "miss", None, None,
        ))
        saved.execute("INSERT INTO publisher_query_scopes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            "zhexue-yanjiu", 2026, "8", "测试目录论文1", "cnki",
            "2026-09-28T10:00:00+08:00", "title_year", 2026, 1, 1, 1,
        ))
    checked = issue_coverage_summary(db, journal_id="zhexue-yanjiu")[0]
    assert checked["research_source_checks"]["cnki"] == {
        "complete_hits": 0, "complete_misses": 1, "unresolved": 0,
        "visibility_denominator": 1,
    }
    assert checked["research_source_checks"]["wanfang"]["visibility_denominator"] is None
    with pytest.raises(ValueError, match="exact collected article"):
        review_publisher_article(
            db, "zhexue-yanjiu", 2026, "8", "测试目录论文1", urls[1],
            work_type="research", interest="relevant",
        )
    with pytest.raises(ValueError, match="requires a reviewed research article"):
        review_publisher_article(
            db, "zhexue-yanjiu", 2026, "8", "测试目录论文2", urls[1],
            work_type="other", interest="relevant",
        )
    with _connect(db) as saved:
        saved.execute("UPDATE publisher_articles SET title = ? WHERE title = ?", (
            "更改后的目录题名", "测试目录论文2",
        ))
    changed = issue_coverage_summary(db, journal_id="zhexue-yanjiu")[0]
    assert changed["publisher_records"] == 2
    assert changed["toc_complete"] is False
    assert changed["reference_denominator"] is None
    assert changed["research_denominator"] is None
    assert changed["interest_denominator"] is None


def test_publisher_author_spacing_preserves_chinese_name_identity() -> None:
    article = {
        "title": "“天命流行”的开放创生——朱熹论“天地之心”思想再探",
        "author": "刘 沁", "year": 2026, "issue": "4",
    }
    assert journal_collection._identity(
        article, article["title"], ("刘沁",), "孔子研究", 2026, "4", "孔子研究"
    ) == "hit"
    article["author"] = "刘沁；王乙"
    assert journal_collection._identity(
        article, article["title"], ("刘沁",), "孔子研究", 2026, "4", "孔子研究"
    ) == "hit"
    article["author"] = "张三 李四"
    assert journal_collection._identity(
        article, article["title"], ("张三",), "孔子研究", 2026, "4", "孔子研究"
    ) == "hit"


def test_old_article_rows_without_complete_scan_have_no_reference_denominator(tmp_path) -> None:
    db = tmp_path / "publisher.sqlite3"
    with _connect(db) as saved:
        saved.execute("INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)", (
            "zhouyi-yanjiu", 2026, "4", "测试论文", "作者乙",
            "https://zhouyi.sdu.edu.cn/article", "https://zhouyi.sdu.edu.cn/issue",
            "2026-08-21T10:00:00+08:00", "2026-08-21T10:00:00+08:00",
        ))
    row = issue_coverage_summary(db)[0]
    assert row["publisher_records"] == 1
    assert row["toc_complete"] is False
    assert row["reference_denominator"] is None


def test_cnki_query_scope_records_complete_miss_and_page_bound(tmp_path, monkeypatch) -> None:
    db = tmp_path / "publisher.sqlite3"
    with _connect(db) as saved:
        saved.execute("INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)", (
            "zhouyi-yanjiu", 2026, "4", "测试论文", "作者乙",
            "https://zhouyi.sdu.edu.cn/article", "https://zhouyi.sdu.edu.cn/issue",
            "2026-08-21T10:00:00+08:00", "2026-08-21T10:00:00+08:00",
        ))
    monkeypatch.setattr(journal_collection, "fetch_result_page",
                        lambda *_args, **_kwargs: ((), 1))
    first = check_publisher_articles(
        "zhouyi-yanjiu", db, checked_at="2026-09-27T10:00:00+08:00"
    )
    assert first["checks"][0]["status"] == "miss"
    row = issue_coverage_summary(db, include_articles=True)[0]
    cnki = row["articles"][0]["checks"]["cnki"]
    assert (cnki["query_kind"], cnki["year_filter"], cnki["fetched_pages"],
            cnki["reported_pages"], cnki["complete"]) == ("title_year", 2026, 1, 1, True)
    assert row["source_checks"]["cnki"]["complete_queries"] == 1
    assert row["source_checks"]["cnki"]["complete_misses"] == 1

    monkeypatch.setattr(journal_collection, "fetch_result_page",
                        lambda *_args, **_kwargs: ((), 3))
    second = check_publisher_articles(
        "zhouyi-yanjiu", db, min_interval_hours=0,
        checked_at="2026-09-28T10:00:00+08:00",
    )
    assert second["checks"][0]["status"] == "incomplete"
    row = issue_coverage_summary(db, include_articles=True)[0]
    cnki = row["articles"][0]["checks"]["cnki"]
    assert (cnki["fetched_pages"], cnki["reported_pages"], cnki["complete"]) == (
        1, 3, False,
    )
    assert row["source_checks"]["cnki"]["incomplete"] == 1
    assert row["source_checks"]["cnki"]["complete_misses"] == 0


def test_journal_watch_audit_and_classify_cli_require_exact_source(tmp_path, capsys) -> None:
    db = tmp_path / "publisher.sqlite3"
    url = "https://zhouyi.sdu.edu.cn/article"
    with _connect(db) as saved:
        saved.execute("INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)", (
            "zhouyi-yanjiu", 2026, "4", "测试论文", "作者乙", url,
            "https://zhouyi.sdu.edu.cn/issue", "2026-08-21T10:00:00+08:00",
            "2026-08-21T10:00:00+08:00",
        ))
    parser = build_parser()
    base = ["journal-watch", "classify", "--db", str(db), "--journal", "zhouyi-yanjiu",
            "--year", "2026", "--issue", "4", "--title", "测试论文",
            "--article-url", url, "--work-type", "research", "--interest", "uncertain"]
    with pytest.raises(ValueError, match="confirm-evidence"):
        parser.parse_args(base).handler(parser.parse_args(base))
    assert parser.parse_args([*base, "--confirm-evidence"]).handler(
        parser.parse_args([*base, "--confirm-evidence"])
    ) == 0
    assert '"work_type": "research"' in capsys.readouterr().out

    audit = parser.parse_args(["journal-watch", "audit", "--db", str(db),
                               "--journal", "zhouyi-yanjiu", "--year", "2026",
                               "--issue", "4", "--articles"])
    assert audit.handler(audit) == 0
    assert '"reference_denominator": null' in capsys.readouterr().out
    with pytest.raises(ValueError, match="article-level audit"):
        args = parser.parse_args(["journal-watch", "audit", "--db", str(db), "--articles"])
        args.handler(args)


def test_parsed_publisher_announcement_waits_for_human_review(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("qkml.htm"):
            html = '<a href="../info/1033/2558.htm">《周易研究》2026年第4期</a>'
        else:
            html = (
                "<h2>《周易研究》2026年第4期</h2><span>日期：2026-08-21</span>"
                '<div id="vsb_content"><section style="text-align: center; '
                'line-height: 2; font-size: 12px;">'
                "<p>易学中的形而上问题</p><p>李乙</p></section></div>"
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    db = tmp_path / "publisher.sqlite3"
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        result = collect_publisher("zhouyi-yanjiu", db, client=client)
    assert result["issue_status"] == "pending"
    with sqlite3.connect(db) as saved:
        status, announcement = saved.execute(
            "SELECT status, announcement_on FROM official_issues"
        ).fetchone()
    assert (status, announcement) == ("pending", "2026-08-21")


def test_publisher_cycle_isolates_one_failed_site(tmp_path, monkeypatch) -> None:
    visited: list[str] = []

    def collect(journal_id, *_args, **_kwargs):
        visited.append(journal_id)
        if journal_id == "zhexue-yanjiu":
            raise ValueError("publisher_http_429")
        return {"journal": journal_id}

    monkeypatch.setattr(journal_collection, "collect_publisher", collect)
    result = run_publisher_cycle(tmp_path / "publisher.sqlite3")
    assert len(visited) == result["sites_attempted"] == 9
    assert result["sites_failed"] == 1
    failed = next(item for item in result["sites"] if item["journal"] == "zhexue-yanjiu")
    assert failed["scan"] is None
    assert failed["failure"] == "publisher_http_429"
    assert all(
        item["scan"] is not None for item in result["sites"]
        if item["journal"] != "zhexue-yanjiu"
    )


def test_first_hit_interval_needs_prior_complete_miss(tmp_path) -> None:
    path = tmp_path / "issues.sqlite3"
    with _connect(path) as db:
        db.execute(
            "INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "zhouyi-yanjiu",
                2026,
                "4",
                "易学新论",
                "李乙",
                "https://zhouyi.sdu.edu.cn/a",
                "https://zhouyi.sdu.edu.cn/issue",
                "2026-08-21T10:00:00+08:00",
                "2026-08-21T10:00:00+08:00",
            ),
        )
        for checked, status in (
            ("2026-08-20T10:00:00+08:00", "miss"),
            ("2026-08-22T10:00:00+08:00", "miss"),
            ("2026-08-25T10:00:00+08:00", "hit"),
        ):
            db.execute(
                "INSERT INTO publisher_article_checks VALUES (?,?,?,?,?,?,?,?,?)",
                ("zhouyi-yanjiu", 2026, "4", "易学新论", "wanfang", checked, status, None, None),
            )
        db.execute(
            "INSERT INTO publisher_query_scopes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("zhouyi-yanjiu", 2026, "4", "易学新论", "wanfang",
             "2026-08-22T10:00:00+08:00", "title", None, 1, 1, 1),
        )
    row = observation_summary(path)[0]
    assert row["arrival_evidence"] == "interval_censored"
    assert row["last_complete_miss"] == "2026-08-22T10:00:00+08:00"
    assert row["first_hit"] == "2026-08-25T10:00:00+08:00"
    assert measurement_summary(path)[1]["arrival_intervals"] == 1
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO publisher_article_checks VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "zhouyi-yanjiu",
                2026,
                "4",
                "易学新论",
                "cnki",
                "2026-08-22T10:00:00+08:00",
                "hit",
                None,
                None,
            ),
        )
    cnki = next(row for row in observation_summary(path) if row["source"] == "cnki")
    assert cnki["arrival_evidence"] == "left_censored"


def test_legacy_or_incomplete_miss_cannot_prove_arrival_interval(tmp_path) -> None:
    path = tmp_path / "issues.sqlite3"
    with _connect(path) as db:
        db.execute("INSERT INTO publisher_articles VALUES (?,?,?,?,?,?,?,?,?)", (
            "zhouyi-yanjiu", 2026, "4", "测试论文", "作者乙",
            "https://zhouyi.sdu.edu.cn/a", "https://zhouyi.sdu.edu.cn/issue",
            "2026-08-21T10:00:00+08:00", "2026-08-21T10:00:00+08:00",
        ))
        for stamp, status in (
            ("2026-08-22T10:00:00+08:00", "miss"),
            ("2026-08-23T10:00:00+08:00", "miss"),
            ("2026-08-25T10:00:00+08:00", "hit"),
        ):
            db.execute("INSERT INTO publisher_article_checks VALUES (?,?,?,?,?,?,?,?,?)", (
                "zhouyi-yanjiu", 2026, "4", "测试论文", "cnki", stamp, status, None, None,
            ))
        db.execute("INSERT INTO publisher_query_scopes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            "zhouyi-yanjiu", 2026, "4", "测试论文", "cnki",
            "2026-08-23T10:00:00+08:00", "title_year", 2026, 1, 3, 0,
        ))
    row = observation_summary(path)[0]
    assert row["last_complete_miss"] is None
    assert row["arrival_evidence"] == "left_censored"
    assert measurement_summary(path)[0]["arrival_intervals"] == 0
