"""Completion-detection analysis: how long after a command exits does the agent move on?

Per RUN step i (variant v):
  run_ms      probe start -> probe exit (the command's own duration)
  dispatch_ms marker i written -> probe start (tool start; should not depend on v)
  detect_ms   probe exit -> marker i+1 written (harness notices exit + constant model turn)
  excess_ms   median detect_ms(v) - median detect_ms(baseline): the harness's extra
              completion-detection cost for that kind of command
  early       marker i+1 written before the probe exited: the harness returned
              (or backgrounded the command) before it finished
  killed      a background child never recorded its exit: the harness killed it
"""

from pathlib import Path

from harnessbench import analysis, runs, stats, timing

BASELINE = "line"
BACKGROUND_VARIANTS = {"bg-holds-output", "bg-detached"}


def step_rows(meta: dict, records: list[dict], markers: dict[int, int]) -> list[dict]:
    completes: dict[int, dict] = {}
    for record in records:
        if record["kind"] == "complete":
            completes.setdefault(record["seq"], record)
    child_exits = {r["seq"]: r["exit_ns"] for r in records if r["kind"] == "child_exit"}
    rows = []
    for step in meta.get("steps", []):
        seq, variant = step["seq"], step["variant"]
        record = completes.get(seq)
        if record is None:
            continue
        next_marker = markers.get(seq + 1)
        detect = timing.diff_ms(next_marker, record["exit_ns"])
        child_exit = child_exits.get(seq)
        rows.append(
            {
                "seq": seq,
                "variant": variant,
                "recorded_variant": record["variant"],
                "run_ms": timing.diff_ms(record["exit_ns"], record["start_ns"]),
                "dispatch_ms": timing.diff_ms(record["start_ns"], markers.get(seq)),
                "detect_ms": detect,
                "after_child_ms": timing.diff_ms(next_marker, child_exit),
                "early": detect is not None and detect < 0,
                "killed": variant in BACKGROUND_VARIANTS and child_exit is None,
            }
        )
    return rows


def validate(
    meta: dict, records: list[dict], markers: dict[int, int], rows: list[dict]
) -> list[str]:
    seqs = [r["seq"] for r in records if r["kind"] == "complete"]
    if not seqs:
        return ["no probes recorded"]
    planned = {step["seq"] for step in meta.get("steps", [])}
    issues = []
    if missing := sorted(planned - set(seqs)):
        issues.append(f"missing probes {missing}")
    if repeated := sorted({s for s in seqs if seqs.count(s) > 1}):
        issues.append(f"repeated probes {repeated}")
    if missing_markers := sorted(set(range(1, len(planned) + 2)) - set(markers)):
        issues.append(f"missing markers {missing_markers}")
    for row in rows:
        if row["variant"] != row["recorded_variant"]:
            issues.append(
                f"probe {row['seq']} ran variant {row['recorded_variant']}, not {row['variant']}"
            )
        if (row["dispatch_ms"] or 0) < 0:
            issues.append(f"probe {row['seq']} ran before marker {row['seq']}")
    return issues


def variant_summary(rows: list[dict]) -> list[dict]:
    """One entry per variant; excess is relative to the baseline variant's median."""
    by_variant = stats.group_by(rows, lambda r: (r["variant"],))
    baseline = stats.median(r["detect_ms"] for r in by_variant.get((BASELINE,), []))
    summary = []
    for (variant,), group in sorted(by_variant.items()):
        detect = stats.summarize(r["detect_ms"] for r in group)
        summary.append(
            {
                "variant": variant,
                "n": detect["n"],
                "run_ms": stats.median(r["run_ms"] for r in group),
                "dispatch_ms": stats.median(r["dispatch_ms"] for r in group),
                "detect_ms": detect["median"],
                "detect_p90": detect["p90"],
                "excess_ms": detect["median"] - baseline
                if detect["median"] is not None and baseline is not None
                else None,
                "early": sum(r["early"] for r in group),
                "killed": sum(r["killed"] for r in group),
            }
        )
    return summary


def summarize_run(run_path: Path) -> dict | None:
    meta = runs.read_json(run_path / "meta.json")
    if meta is None or meta.get("benchmark") != "completion":
        return None
    records = runs.read_jsonl(run_path / "complete.jsonl")
    markers = analysis.marker_times(run_path)
    rows = step_rows(meta, records, markers)
    return {
        "meta": meta,
        "rows": rows,
        "n": len(rows),
        "markers": len(markers),
        "issues": validate(meta, records, markers, rows),
        "variants": variant_summary(rows),
    }
