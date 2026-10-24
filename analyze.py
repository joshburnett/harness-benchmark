"""Summarize everything under runs/ (copy runs/ from every machine into one place first).

python analyze.py                 # one row per agent run + spawn/net/llm comparisons
python analyze.py --run <id>      # per-tool-call breakdown for one agent run
python analyze.py --csv out.csv   # agent-run summary as CSV
"""

import argparse
import csv
from pathlib import Path

from harnessbench import analysis, runs, stats

DETAIL_COLUMNS = [
    "pre_create_ms",
    "shell_init_ms",
    "exec_launch_ms",
    "py_init_ms",
    "probe_ms",
    "turn_to_shell_ms",
    "turn_to_native_ms",
]
SUMMARY_COLUMNS = [
    ("shell_init", "shell_init_ms"),
    ("exec", "exec_launch_ms"),
    ("py_init", "py_init_ms"),
    ("pre_create", "pre_create_ms"),
    ("turn_native", "turn_to_native_ms"),
    ("turn_shell", "turn_to_shell_ms"),
]


def agent_runs() -> list[dict]:
    summaries = []
    for path in sorted(runs.RUNS_DIR.glob("*/meta.json")):
        summary = analysis.summarize_run(path.parent)
        if summary and summary["n"]:
            summaries.append(summary)
    return summaries


def print_agent_runs(summaries: list[dict]) -> None:
    headers = ["run", "env", "harness", "model", "shell", "n", "reused"]
    headers += [label for label, _ in SUMMARY_COLUMNS] + [
        "shell_overhead",
        "turn_shell_p90",
        "wall_s",
    ]
    rows = []
    for s in summaries:
        meta = s["meta"]
        row = [
            meta["run_id"][:15],
            meta["env"],
            meta["harness"],
            meta["model"],
            meta["shell"],
        ]
        row += [str(s["n"]), str(s["reused"])]
        row += [stats.fmt(s[key]["median"]) for _, key in SUMMARY_COLUMNS]
        row += [
            stats.fmt(s["shell_overhead_ms"]),
            stats.fmt(s["turn_to_shell_ms"]["p90"]),
            stats.fmt(s["wall_s"], 1),
        ]
        rows.append(row)
    print("## Agent runs (medians in ms, first call excluded)")
    print(stats.render_table(headers, rows, text_cols=5))


def print_run_detail(run_id: str) -> None:
    summary = analysis.summarize_run(runs.RUNS_DIR / run_id, include_first=True)
    if summary is None:
        raise SystemExit(f"no run {run_id}")
    print(f"## {run_id}  ({summary['n']} probes, {summary['markers']} markers)")
    headers = [
        "seq",
        "shell",
        "reused",
        *(c.removesuffix("_ms") for c in DETAIL_COLUMNS),
    ]
    rows = [
        [
            str(r["seq"]),
            str(r["shell"]),
            "yes" if r["reused_shell"] else "",
            *(stats.fmt(r[c]) for c in DETAIL_COLUMNS),
        ]
        for r in summary["rows"]
    ]
    print(stats.render_table(headers, rows, text_cols=3))
    print(
        f"shell_overhead_ms (median turn_to_shell - median turn_to_native): {stats.fmt(summary['shell_overhead_ms'])}"
    )
    if summary["first_tool_ms"] is not None:
        print(f"launch -> first tool call: {stats.fmt(summary['first_tool_ms'])} ms")


def standalone_records(kind: str) -> list[dict]:
    paths = list((runs.RUNS_DIR / kind).glob("*.jsonl")) + list(
        runs.RUNS_DIR.glob(f"*/{kind}.jsonl")
    )
    return [
        record
        for path in paths
        for record in runs.read_jsonl(path)
        if record.get("kind") == kind
    ]


def print_spawn() -> None:
    records = [r for r in standalone_records("spawn") if r["idle_s"] == 0]
    if not records:
        return
    groups = stats.group_by(records, lambda r: (r["name"], r["env"], r["tag"]))
    rows = []
    for (name, env, tag), group in sorted(groups.items()):
        first = [r for r in group if r["position"] == "first"]
        repeat = [r for r in group if r["position"] == "repeat"]
        rows.append(
            [
                name,
                env,
                tag,
                str(len(group)),
                stats.fmt(stats.median(r.get("startup_ms") for r in group)),
                stats.fmt(stats.median(r.get("total_ms") for r in first)),
                stats.fmt(stats.median(r.get("total_ms") for r in repeat)),
                stats.fmt(stats.summarize(r.get("total_ms") for r in group)["p90"]),
            ]
        )
    print("\n## Process spawn, idle 0 (ms)")
    headers = [
        "command",
        "env",
        "tag",
        "n",
        "startup",
        "total_first",
        "total_repeat",
        "total_p90",
    ]
    print(stats.render_table(headers, rows, text_cols=3))


def print_idle_effect() -> None:
    records = [r for r in standalone_records("spawn") if r["position"] == "first"]
    idles = sorted({r["idle_s"] for r in records})
    if len(idles) < 2:
        return
    groups = stats.group_by(records, lambda r: (r["name"], r["env"]))
    rows = []
    for (name, env), group in sorted(groups.items()):
        by_idle = stats.group_by(group, lambda r: (r["idle_s"],))
        rows.append(
            [
                name,
                env,
                *(
                    stats.fmt(
                        stats.median(r.get("total_ms") for r in by_idle.get((i,), []))
                    )
                    for i in idles
                ),
            ]
        )
    print(
        "\n## First spawn after idle (median total ms) - does a per-exe check cache expire?"
    )
    print(
        stats.render_table(
            ["command", "env", *(f"idle {i}s" for i in idles)], rows, text_cols=2
        )
    )


def print_net() -> None:
    records = [r for r in standalone_records("net") if not r.get("error")]
    if not records:
        return
    rows = []
    for (host, env), group in sorted(
        stats.group_by(records, lambda r: (r["host"], r["env"])).items()
    ):
        rows.append(
            [
                host,
                env,
                str(len(group)),
                *(
                    stats.fmt(stats.median(r.get(k) for r in group), 1)
                    for k in ("dns_ms", "tcp_ms", "tls_ms", "ttfb_ms")
                ),
                (group[-1].get("issuer") or "")[:40],
            ]
        )
    print("\n## Network (median ms)")
    print(
        stats.render_table(
            ["host", "env", "n", "dns", "tcp", "tls", "ttfb", "issuer"],
            rows,
            text_cols=2,
        )
    )


def print_llm() -> None:
    records = [r for r in standalone_records("llm") if not r.get("error")]
    if not records:
        return
    rows = []
    for (model, env), group in sorted(
        stats.group_by(records, lambda r: (r["model"], r["env"])).items()
    ):
        rows.append(
            [
                model,
                env,
                str(len(group)),
                *(
                    stats.fmt(stats.median(r.get(k) for r in group))
                    for k in ("headers_ms", "ttft_ms", "stream_span_ms", "total_ms")
                ),
                stats.fmt(stats.median(r.get("tok_per_s") for r in group), 1),
            ]
        )
    print("\n## Direct LLM API (median)")
    headers = [
        "model",
        "env",
        "n",
        "headers_ms",
        "ttft_ms",
        "stream_ms",
        "total_ms",
        "tok/s",
    ]
    print(stats.render_table(headers, rows, text_cols=2))


def write_csv(summaries: list[dict], path: Path) -> None:
    fields = [
        "run_id",
        "env",
        "harness",
        "model",
        "shell",
        "n",
        "reused",
        "shell_overhead_ms",
        "wall_s",
    ]
    metric_fields = [
        f"{m}_{s}"
        for m in analysis.TOOL_METRICS + analysis.TURN_METRICS
        for s in ("median", "p90")
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields + metric_fields)
        writer.writeheader()
        for s in summaries:
            row = {
                key: s["meta"].get(key)
                for key in ("run_id", "env", "harness", "model", "shell")
            }
            row |= {
                key: s[key] for key in ("n", "reused", "shell_overhead_ms", "wall_s")
            }
            for metric in analysis.TOOL_METRICS + analysis.TURN_METRICS:
                row[f"{metric}_median"] = s[metric]["median"]
                row[f"{metric}_p90"] = s[metric]["p90"]
            writer.writerow(row)
    print(f"\nwrote {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", help="per-call detail for one agent run")
    parser.add_argument("--csv", type=Path, help="write agent-run summary CSV")
    args = parser.parse_args()
    if args.run:
        print_run_detail(args.run)
        return 0
    summaries = agent_runs()
    if summaries:
        print_agent_runs(summaries)
    print_spawn()
    print_idle_effect()
    print_net()
    print_llm()
    if args.csv:
        write_csv(summaries, args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
