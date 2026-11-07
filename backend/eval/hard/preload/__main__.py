"""Hard-set answers with context given before the first turn.

    python -m eval.hard.preload terms answers --name terms --variant graph --records \\
      --chat-model claude-haiku-5-5 --judge claude-sonnet-5-5 --no-grounding \\
      --concurrency 4 --repeats 3

`terms` adds facts from the knowledge graph about names in the question; `plain` adds the
top 20 passages of a search for the question. Both go at the end of the first user message
with the same instruction. Everything after the mode is `eval.harness`'s own arguments.

Run it on a copy of strata_eval_hard with the graph built (`createdb -T strata_eval_hard`,
then `build-graph`), with EVAL_CORPUS=hard and EVAL_DATABASE_URL naming the copy. The copy
keeps its active record schemas and none are suggested: `suggest()` reads documents in no
fixed order, and a graph build rewrites every document row, so a fresh suggestion can add
a schema the baseline never had. A run without `--records` archives every schema, and with
suggestion held nothing turns them back on: set them active again before the next run.

The harness writes each variant's settings into the org, so a later `--variant default`
on the copy still has the graph's tools on. Check `knowledge_graph` in the org settings
before treating a run as graph-off.
"""

import os
import sys

MODES = ("terms", "plain")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.exit(f"usage: python -m eval.hard.preload {{{'|'.join(MODES)}}} <harness arguments>")
    mode = sys.argv.pop(1)
    database = os.environ.get("EVAL_DATABASE_URL", "").rsplit("/", 1)[-1]
    if not database.startswith("strata_eval"):
        sys.exit("set EVAL_DATABASE_URL to a strata_eval* copy with the graph built")

    # The harness must come first: it points the app at EVAL_DATABASE_URL. Importing `app`
    # before it would build the engine for the default (live) database.
    from eval import harness  # noqa: I001

    from app.records import suggest
    from eval.hard.preload import plain, terms

    async def nothing_new(db, collection, user_id=None):
        return []

    suggest.suggest = nothing_new
    {"terms": terms, "plain": plain}[mode].apply()
    harness.main()


if __name__ == "__main__":
    main()
