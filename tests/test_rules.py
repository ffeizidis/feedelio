"""The rule engine: multi-line patterns, literal mode, and an exact dry run."""

import pathlib
import sqlite3

import pytest

from feedelio.core import Core, compile_rule, replacement_error, rule_lines


def library(core, site, titles, body="<p>An ordinary body.</p>"):
    feed = core.subscribe(site[0] + "/rss")["id"]
    return feed, [core.ingest(feed, str(i), title, f"{site[0]}/a{i}", body) for i, title in enumerate(titles)]


def test_rule_lines_and_compilation():
    assert rule_lines("alpha\n\n  beta  \ngamma") == ["alpha", "beta", "gamma"]
    assert rule_lines("   \n\n") == []
    assert [p.pattern for p in compile_rule("a.b", is_regex=False)] == ["a\\.b"]
    with pytest.raises(ValueError, match="Line 2"):
        compile_rule("fine\n[")
    with pytest.raises(ValueError, match="at least one pattern"):
        compile_rule("   ")
    assert replacement_error(compile_rule("x")[0], r"\1")
    assert replacement_error(compile_rule("(x)")[0], r"\1") is None


def test_any_line_matches_and_blank_lines_are_ignored(core, site):
    core.save_rule("Many", "title", "alpha\n\n  beta  \ngamma", "read")
    _, ids = library(core, site, ["alpha one", "beta two", "gamma three", "delta four"])
    assert [core.article(id)["read"] for id in ids] == [1, 1, 1, 0]


def test_literal_mode_takes_patterns_at_face_value(core, site):
    core.save_rule("Literal", "title", "a.b", "star", is_regex=False)
    core.save_rule("Regex", "title", "c.d", "tag", value="matched")
    _, ids = library(core, site, ["xa.bx", "xaZbx", "xcZdx"])
    assert core.article(ids[0])["starred"] == 1
    assert core.article(ids[1])["starred"] == 0  # '.' is a full stop, not a wildcard.
    assert core.article(ids[2])["tags"] == ["matched"]


def test_a_rewrite_applies_every_line_and_sanitizes_its_output(core, site):
    core.save_rule("Tidy", "body", "alpha\nbeta", "rewrite", value="gamma")
    _, ids = library(core, site, ["Post"], body="<p>alpha and beta</p>")
    core.reapply_rules()
    assert "gamma and gamma" in core.article(ids[0])["body"]
    core.save_rule("Inject", "body", "gamma", "rewrite", value="<script>alert(1)</script>ok")
    core.reapply_rules()
    assert "<script>" not in core.article(ids[0])["body"]


def test_save_rule_rejects_what_would_only_fail_later(core, site):
    with pytest.raises(ValueError, match="Line 1"):
        core.save_rule("Bad", "title", "[", "read")
    with pytest.raises(ValueError, match="at least one pattern"):
        core.save_rule("Empty", "title", "  \n ", "read")
    with pytest.raises(ValueError, match="no group 1"):
        core.save_rule("Template", "body", "plain", "rewrite", value=r"\1")
    with pytest.raises(ValueError, match="needs a tag"):
        core.save_rule("Tagless", "title", "x", "tag", value=" ")


def test_validate_rule_reports_the_offending_line(core):
    report = core.validate_rule("good\n[\nalso good")
    assert report["lines"] == 3
    assert [e["line"] for e in report["errors"]] == [2]
    assert core.validate_rule("(a+)+$")["errors"][0]["slow"]
    assert not core.validate_rule("[", is_regex=False)["errors"]  # Literal, so harmless.
    assert core.validate_rule("x", action="rewrite", value=r"\1")["value_error"]


def test_editing_a_rule_keeps_its_place_in_the_order(core, site):
    first = core.save_rule("Tag", "title", "match", "tag", value="flag")["id"]
    second = core.save_rule("Read tagged", "tags", "flag", "read")["id"]
    core.save_rule("Tag", "title", "changed\nmatch", "tag", value="flag", id=first)
    assert [r["id"] for r in core.rules()] == [first, second]
    _, ids = library(core, site, ["match"])
    assert core.article(ids[0])["read"] == 1


def test_preview_predicts_the_apply_exactly_and_changes_nothing(core, site):
    _, ids = library(core, site, ["an advert", "a keeper", "plain", "another advert"])
    core.save_rule("Ads", "title", "advert", "read")  # Written after the articles,
    core.save_rule("Keep", "title", "keeper", "star")  # so they are what needs the pass.
    core.save_rule("Label", "title", "keeper", "tag", value="saved")
    snapshot = {r["id"]: dict(r) for r in core.rows("SELECT * FROM articles")}
    changes = core.db.total_changes

    preview = core.preview_rules()
    assert core.db.total_changes == changes  # A dry run writes nothing at all.
    assert {r["id"]: dict(r) for r in core.rows("SELECT * FROM articles")} == snapshot

    applied = core.reapply_rules()
    assert preview["changed"] == applied["changed"] == 3
    assert preview["actions"] == applied["actions"]
    assert preview["actions"]["read"] == 2 and preview["actions"]["star"] == 1
    after = {r["id"]: dict(r) for r in core.rows("SELECT * FROM articles")}
    assert sum(1 for id in snapshot if snapshot[id] != after[id]) == preview["changed"]
    assert core.reapply_rules()["changed"] == 0  # Nothing left to do.


def test_applying_touches_only_the_articles_that_change(core, site):
    _, ids = library(core, site, ["an advert"] + [f"clean {i}" for i in range(9)])
    core.save_rule("Ads", "title", "advert", "read")
    written = []
    original = core._write_article
    core._write_article = lambda a, columns: written.append((a["id"], tuple(columns))) or original(a, columns)
    core.reapply_rules()
    core._write_article = original
    # One row, one column: the article_search trigger never has to re-index.
    assert written == [(ids[0], ("read",))]
    assert core.articles(q="ordinary")["total"] == 10
    core.db.execute("INSERT INTO article_search(article_search) VALUES('integrity-check')")


def test_one_pathological_rule_costs_its_article_not_the_pass(core, site):
    feed = core.subscribe(site[0] + "/rss")["id"]
    core.ingest(feed, "slow", "clean one", site[0] + "/slow", "<p>" + "a" * 20000 + "!</p>")
    good = core.ingest(feed, "fast", "clean two", site[0] + "/fast", "<p>short</p>")
    evil = core.save_rule("Evil", "body", "(a+)+$", "star")["id"]
    core.save_rule("Benign", "title", "clean", "read")
    result = core.reapply_rules()
    assert result["timeouts"] == 1
    assert result["slow"] == [evil]  # Named, so it can be fixed.
    assert core.article(good)["read"] == 1  # The other rule still ran.
    # The preview reports the same thing without applying anything.
    assert core.preview_rules()["slow"] == [evil]


def test_a_slow_rule_does_not_stop_an_article_being_ingested(core, site):
    core.save_rule("Evil", "body", "(a+)+$", "star")
    core.save_rule("Benign", "title", "clean", "read")
    feed = core.subscribe(site[0] + "/rss")["id"]
    article = core.ingest(feed, "slow", "clean", site[0] + "/slow", "<p>" + "a" * 20000 + "!</p>")
    assert core.article(article)["read"] == 1  # Ingested, and the other rule applied.


def test_a_rule_that_no_longer_compiles_is_skipped_not_raised(core, site):
    core.save_rule("Fine", "title", "keep", "read")
    with core.db:  # Only a direct write can leave an uncompilable pattern behind.
        core.db.execute("UPDATE rules SET pattern='[' WHERE name='Fine'")
    _, ids = library(core, site, ["keep me"])
    assert core.article(ids[0])["read"] == 0
    assert core.preview_rules()["processed"] == 1


def test_an_older_database_gains_the_column_and_keeps_its_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", "1")
    schema = (
        pathlib.Path("feedelio/core/schema.sql")
        .read_text()
        .replace(
            " enabled INTEGER NOT NULL DEFAULT 1,\n is_regex INTEGER NOT NULL DEFAULT 1);",
            " enabled INTEGER NOT NULL DEFAULT 1);",
        )
    )
    connection = sqlite3.connect(tmp_path / "app.sqlite")
    connection.executescript(schema)
    connection.execute(
        "INSERT INTO rules (id,name,field,pattern,action,value,feed_id,enabled)"
        " VALUES ('old','Legacy','title','adv.rt','read','',NULL,1)"
    )
    connection.commit()
    connection.close()

    Core.initialised.discard(tmp_path.resolve())  # The guard is class-level.
    with Core(tmp_path) as core:
        assert "is_regex" in {r[1] for r in core.db.execute("PRAGMA table_info(rules)")}
        assert core.rules()[0]["is_regex"] == 1  # What it always was.


def test_a_backup_without_the_column_still_restores(core, site, tmp_path, monkeypatch):
    monkeypatch.setenv("FEEDELIO_ALLOW_PRIVATE_NETWORK", "1")
    core.save_rule("Literal", "title", "a.b", "read", is_regex=False)
    backup = core.backup()
    assert "is_regex" in backup["tables"]["rules"][0]
    for row in backup["tables"]["rules"]:
        row.pop("is_regex")

    root = tmp_path / "restored"
    Core.initialised.discard(root.resolve())
    with Core(root) as restored:
        restored.restore(backup)
        assert restored.rules()[0]["is_regex"] == 1
