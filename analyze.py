"""Summarize everything under runs/ (copy runs/ from every machine into one place first).

python analyze.py                 # dispatch + completion runs, spawn/net/llm comparisons
python analyze.py --run <id>      # per-tool-call breakdown for one agent run (either benchmark)
python analyze.py --csv out.csv   # agent-run summary as CSV
"""

import argparse
import csv
from pathlib import Path

from harnessbench import analysis, completion, runs, stats

DETAIL_COLUMNS = [
    "pre_create_ms",
    "shell_to_exec_ms",
    "shell_init_ms",
    "exec_launch_ms",
    "py_init_ms",
    "probe_ms",
    "turn_to_shell_ms",
    "turn_to_native_ms",
]
SUMMARY_COLUMNS = [
    ("shell_to_exec", "shell_to_exec_ms"),
    ("shell_init", "shell_init_ms"),
    ("exec", "exec_launch_ms"),
    ("py_init", "py_init_ms"),
    ("pre_create", "pre_create_ms"),
    ("turn_native", "turn_to_native_ms"),
    ("turn_shell", "turn_to_shell_ms"),
]


VARIANT_COLUMNS = [
    ("run", "run_ms"),
    ("dispatch", "dispatch_ms"),
    ("detect", "detect_ms"),
    ("detect_p90", "detect_p90"),
    ("excess", "excess_ms"),
]


def agent_runs() -> list[dict]:
    summaries = []
    for path in sorted(runs.RUNS_DIR.glob("*/meta.json")):
        if summary := analysis.summarize_run(path.parent):
            summaries.append(summary)
    return summaries


def completion_runs() -> list[dict]:
    summaries = []
    for path in sorted(runs.RUNS_DIR.glob("*/meta.json")):
        if summary := completion.summarize_run(path.parent):
            summaries.append(summary)
    return summaries


def print_invalid(invalid: list[dict], included: bool, kind: str) -> None:
    action = "included above" if included else "excluded; use --include-invalid to show"
    print(f"\n## Invalid {kind} runs ({action})")
    for s in invalid:
        print(f"{s['meta']['run_id']}: {'; '.join(s['issues'])}")


def variant_table(variants: list[dict]) -> str:
    headers = [
        "variant",
        "n",
        *(label for label, _ in VARIANT_COLUMNS),
        "early",
        "killed",
    ]
    rows = [
        [
            v["variant"],
            str(v["n"]),
            *(stats.fmt(v[key]) for _, key in VARIANT_COLUMNS),
            str(v["early"]),
            str(v["killed"]),
        ]
        for v in variants
    ]
    return stats.render_table(headers, rows)


def print_completion(summaries: list[dict], include_invalid: bool) -> None:
    shown = [s for s in summaries if s["n"] and (include_invalid or not s["issues"])]
    groups = stats.group_by(
        shown, lambda s: (s["meta"]["env"], s["meta"]["harness"], s["meta"]["model"])
    )
    for (env, harness, model), group in sorted(groups.items()):
        rows = [row for s in group for row in s["rows"]]
        print(
            f"\n## Completion detection: {env} / {harness} / {model} "
            f"({len(group)} runs, medians in ms, excess vs '{completion.BASELINE}')"
        )
        print(variant_table(completion.variant_summary(rows)))
    if invalid := [s for s in summaries if s["issues"]]:
        print_invalid(invalid, include_invalid, "completion")


def print_completion_detail(summary: dict) -> None:
    meta = summary["meta"]
    print(f"## {meta['run_id']}  ({summary['n']} probes, {summary['markers']} markers)")
    for issue in summary["issues"]:
        print(f"INVALID: {issue}")
    headers = ["seq", "variant", "run", "dispatch", "detect", "after_child", "flags"]
    rows = [
        [
            str(r["seq"]),
            r["variant"],
            stats.fmt(r["run_ms"]),
            stats.fmt(r["dispatch_ms"]),
            stats.fmt(r["detect_ms"]),
            stats.fmt(r["after_child_ms"]),
            " ".join(flag for flag in ("early", "killed") if r[flag]),
        ]
        for r in summary["rows"]
    ]
    print(stats.render_table(headers, rows, text_cols=2))
    print(f"\n## Per variant (medians in ms, excess vs '{completion.BASELINE}')")
    print(variant_table(summary["variants"]))


def print_agent_runs(summaries: list[dict]) -> None:
    headers = ["run", "env", "harness", "model", "shell", "valid", "n", "reused"]
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
            s["shell"],
            "no" if s["issues"] else "yes",
        ]
        row += [str(s["n"]), str(s["reused"])]
        row += [stats.fmt(s[key]["median"]) for _, key in SUMMARY_COLUMNS]
        row += [
            stats.fmt(s["shell_overhead_ms"]),
            stats.fmt(s["turn_to_shell_ms"]["p90"]),
            stats.fmt(s["wall_s"], 1),
        ]
        rows.append(row)
    print("## Dispatch runs (medians in ms, first call excluded)")
    print(stats.render_table(headers, rows, text_cols=6))


def print_run_detail(run_id: str) -> None:
    summary = analysis.summarize_run(runs.RUNS_DIR / run_id, include_first=True)
    if summary is None:
        raise SystemExit(f"no run {run_id}")
    print(f"## {run_id}  ({summary['n']} probes, {summary['markers']} markers)")
    for issue in summary["issues"]:
        print(f"INVALID: {issue}")
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
    if summary["ready_missing"]:
        print(
            f"note: {summary['ready_missing']} probes have no shell-ready timestamp "
            "(e.g. macOS /bin/bash 3.2 lacks $EPOCHREALTIME); shell_init and exec_launch "
            "are blank, use shell_to_exec instead"
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
        "issues",
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
                key: s["meta"].get(key) for key in ("run_id", "env", "harness", "model")
            }
            row |= {"shell": s["shell"], "issues": "; ".join(s["issues"])}
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
    parser.add_argument(
        "--include-invalid",
        action="store_true",
        help="include runs with failed/skipped/reordered steps (except empty runs)",
    )
    args = parser.parse_args()
    if args.run:
        if summary := completion.summarize_run(runs.RUNS_DIR / args.run):
            print_completion_detail(summary)
        else:
            print_run_detail(args.run)
        return 0
    all_runs = agent_runs()
    invalid = [s for s in all_runs if s["issues"]]
    summaries = [
        s for s in all_runs if s["n"] and (args.include_invalid or not s["issues"])
    ]
    if summaries:
        print_agent_runs(summaries)
    if invalid:
        print_invalid(invalid, args.include_invalid, "dispatch")
    print_completion(completion_runs(), args.include_invalid)
    print_spawn()
    print_idle_effect()
    print_net()
    print_llm()
    if args.csv:
        write_csv(summaries, args.csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
