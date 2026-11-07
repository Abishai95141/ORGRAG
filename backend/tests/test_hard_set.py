"""The hard set's generator and its question handling (no database, no model)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from eval.hard import render
from eval.hard.questions import generate
from eval.hard.world import END, build, monday_of

BACKEND = Path(__file__).resolve().parent.parent


def test_the_world_is_the_same_every_time():
    one, two = render.render(build()), render.render(build())
    assert [(d.title, d.text) for d in one] == [(d.title, d.text) for d in two]


def test_titles_are_unique_and_nothing_is_dated_after_the_world_ends():
    w = build()
    docs = render.render(w)
    assert len({d.title for d in docs}) == len(docs)
    assert all(i.day <= END for i in w.incidents)
    assert all(d.decided <= END for d in w.decisions.values())


def test_a_swap_changes_who_was_on_call_but_not_the_published_rota():
    w = build()
    week, slot, new, _, old = w.swaps[0]
    index = 0 if slot == "primary" else 1
    assert w.rota[week][index] == old
    assert w.on_call(week, slot) == new
    assert monday_of(week) == week


def test_every_quote_behind_a_generated_question_is_in_its_document():
    _, docs, rows = generate()
    # A CSV is stored as rows, so its quotes are in the row form: check.py proves those.
    text = {d.title: d.text for d in docs if not d.filename.endswith(".csv")}
    for q in rows:
        for hop in q["hops"]:
            if hop["doc"] in text:  # a few hops point at the real documents; check.py has those
                assert hop["quote"] in text[hop["doc"]], (q["id"], hop)


def test_each_family_has_a_dev_question_and_questions_never_share_an_id():
    _, _, rows = generate()
    ids = [q["id"] for q in rows]
    assert len(set(ids)) == len(ids)
    families = {q["id"].rsplit("-", 1)[0] for q in rows}
    dev = {q["id"].rsplit("-", 1)[0] for q in rows if q["split"] == "dev"}
    assert families == dev


def test_restricted_answers_stay_in_restricted_collections():
    docs = render.render(build())
    assert {d.collection for d in docs if d.title == "contract values 2026"} <= set(
        render.RESTRICTED
    )
    assert all(d.collection in render.RESTRICTED for d in docs if "budget forecast" in d.title)


def _harness(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    """In its own process: importing the harness repoints the app at the eval database."""
    env = {**os.environ, "DATABASE_URL": "postgresql+asyncpg://x:x@localhost:1/x"}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True
    )


def test_rejudge_reads_the_question_file_it_was_given_or_the_runs_own(tmp_path):
    custom = tmp_path / "custom.jsonl"
    row = {"id": "custom-1", "category": "chain", "scope": ["strata"], "question": "?",
           "gold_docs": [], "facts": [], "notes": ""}  # fmt: skip
    custom.write_text(json.dumps(row) + "\n")
    code = f"""
from eval import harness
run = {{"rows": [{{"id": "custom-1"}}]}}
print(list(harness.questions_for(run, {str(custom)!r})))
print(list(harness.questions_for({{**run, "questions": {str(custom)!r}}}, None)))
"""
    out = _harness(tmp_path, code)
    assert out.stdout.splitlines() == ["['custom-1']", "['custom-1']"], out.stderr


def test_rejudge_stops_rather_than_skip_rows_with_no_question(tmp_path):
    code = """
from eval import harness
harness.questions_for({"rows": [{"id": "not-there"}]}, None)
"""
    out = _harness(tmp_path, code)
    assert out.returncode != 0
    assert "not-there" in out.stderr


def test_a_database_that_isnt_an_eval_one_is_never_truncated(tmp_path):
    code = """
from eval import harness
harness.EVAL_DB = "postgresql+asyncpg://postgres:postgres@localhost:55432/strata"
harness.assert_eval_database()
"""
    out = _harness(tmp_path, code)
    assert out.returncode != 0
    assert "Refusing" in out.stderr
