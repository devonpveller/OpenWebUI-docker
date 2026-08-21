"""Eval harness over the llm-queue analytics event log (design §10.4 / P4).

Reads llm-queue's OWN SQLite store (NOT LiteLLM's Postgres, §4.4) and reports,
per model: admit/reject/finish counts, wait percentiles, mean upstream duration,
and the estimate-vs-actual accuracy (drives the §7.2 tuning loop on T's window /
outlier trimming). Run inside the container:

  docker exec llm-queue python /app/scripts/eval_events.py        # if mounted
  docker exec -i llm-queue python - < eval_events.py              # piped
"""

import os
import sqlite3
import statistics
import sys

DB = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
    "LLM_QUEUE_EVENTS_DB_PATH", "/data/events.db")


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return round(s[k], 2)


def main():
    if not os.path.exists(DB):
        print(f"no event store at {DB}")
        return
    c = sqlite3.connect(DB)
    models = [r[0] for r in
              c.execute("SELECT DISTINCT model FROM queue_events WHERE model IS NOT NULL")]
    total = c.execute("SELECT COUNT(*) FROM queue_events").fetchone()[0]
    print(f"event store: {DB}  ·  {total} events  ·  models: {models}\n")

    for m in models:
        counts = dict(
            c.execute("SELECT event, COUNT(*) FROM queue_events WHERE model=? GROUP BY event",
                      (m,)).fetchall()
        )
        fins = c.execute(
            "SELECT wait_s, duration_s, est_wait_s FROM queue_events "
            "WHERE model=? AND event='finish'", (m,)
        ).fetchall()
        waits = [r[0] for r in fins if r[0] is not None]
        durs = [r[1] for r in fins if r[1] is not None]
        # estimate-vs-actual: |est_at_enqueue - actual_wait|
        errs = [abs(r[2] - r[0]) for r in fins if r[2] is not None and r[0] is not None]
        admits = counts.get("admit", 0)
        rejects = counts.get("reject", 0)
        denom = admits + rejects
        print(f"── {m}")
        print(f"   admit={admits} reject={rejects} finish={counts.get('finish', 0)} "
              f"reject_rate={ (rejects/denom*100 if denom else 0):.1f}%")
        if waits:
            print(f"   wait  p50={pct(waits,50)}s p95={pct(waits,95)}s max={round(max(waits),2)}s")
        if durs:
            print(f"   upstream duration  mean={round(statistics.mean(durs),2)}s "
                  f"p95={pct(durs,95)}s")
        if errs:
            print(f"   estimate error |est-actual|  mean={round(statistics.mean(errs),2)}s "
                  f"p95={pct(errs,95)}s  (n={len(errs)})")
        print()


if __name__ == "__main__":
    main()
