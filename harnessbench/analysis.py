"""Turn raw probe records + marker-file mtimes into per-tool-call latency components.

Timeline for one alternate-mode step pair (all wall-clock, same machine):

  marker i written ──(LLM + harness + spawn)──► shell process created ──► shell ready
      ──► python created ──► python first line ──► probe end
      ──(LLM + harness, native tool)──► marker i+1 written

  pre_create_ms   marker i -> shell process created   (LLM turn + harness dispatch)
  shell_init_ms   shell created -> first command runs (shell startup, profile, AV/EDR)
  exec_launch_ms  shell ready -> python process created (CreateProcess/exec from the shell)
  py_init_ms      python created -> first line of probe.py (image load + interpreter init)
  turn_to_shell_ms   marker i -> shell ready        (whole LLM+harness+shell-start gap)
  turn_to_native_ms  probe end i -> marker i+1      (same, but the next tool spawns nothing)

  shell_overhead_ms = median(turn_to_shell) - median(turn_to_native): what a shell
  tool call costs over a native one, net of the LLM turn.
"""

from pathlib import Path

from harnessbench import procinfo, runs, stats, timing

TOOL_METRICS = ["shell_init_ms", "exec_launch_ms", "py_init_ms", "probe_ms"]
TURN_METRICS = ["pre_create_ms", "turn_to_shell_ms", "turn_to_native_ms"]


def marker_times(run_path: Path) -> dict[int, int]:
    markers: dict[int, int] = {}
    for path in (run_path / "markers").glob("m*.txt"):
        if path.stem[1:].isdigit():
            markers[int(path.stem[1:])] = path.stat().st_mtime_ns
    return markers


def call_rows(probes: list[dict], markers: dict[int, int], mode: str) -> list[dict]:
    first_by_seq: dict[int, dict] = {}
    for probe in probes:
        first_by_seq.setdefault(probe["seq"], probe)
    rows = []
    prev_end: int | None = None
    for seq, probe in sorted(first_by_seq.items()):
        chain = probe.get("chain") or []
        py_create = chain[0].get("create_ns") if chain else None
        shell_index = procinfo.find_shell(chain)
        shell = chain[shell_index] if shell_index is not None else {}
        shell_create = shell.get("create_ns")
        ready = probe.get("shell_ready_ns")
        anchor = markers.get(seq) if mode == "alternate" else prev_end
        reused = bool(shell_create and anchor and shell_create < anchor)
        rows.append(
            {
                "seq": seq,
                "shell": shell.get("name"),
                "reused_shell": reused,
                "pre_create_ms": None
                if reused
                else timing.diff_ms(shell_create, anchor),
                "shell_init_ms": None
                if reused
                else timing.diff_ms(ready, shell_create),
                "exec_launch_ms": timing.diff_ms(py_create, ready),
                "py_init_ms": timing.diff_ms(probe["py_t0_ns"], py_create),
                "probe_ms": timing.diff_ms(probe["probe_end_ns"], probe["py_t0_ns"]),
                "turn_to_shell_ms": timing.diff_ms(ready or py_create, anchor),
                "turn_to_native_ms": timing.diff_ms(
                    markers.get(seq + 1), probe["probe_end_ns"]
                ),
                "probe_end_ns": probe["probe_end_ns"],
            }
        )
        prev_end = probe["probe_end_ns"]
    return rows


def summarize_run(run_path: Path, include_first: bool = False) -> dict | None:
    meta = runs.read_json(run_path / "meta.json")
    if meta is None:
        return None
    probes = runs.read_jsonl(run_path / "probe.jsonl")
    markers = marker_times(run_path)
    rows = call_rows(probes, markers, meta.get("mode", "alternate"))
    steady = rows if include_first else [r for r in rows if r["seq"] != 1]
    summary: dict = {
        "meta": meta,
        "rows": rows,
        "n": len(rows),
        "markers": len(markers),
    }
    for metric in TOOL_METRICS + TURN_METRICS:
        summary[metric] = stats.summarize(r[metric] for r in steady)
    summary["reused"] = sum(r["reused_shell"] for r in rows)
    shell_turn = summary["turn_to_shell_ms"]["median"]
    native_turn = summary["turn_to_native_ms"]["median"]
    summary["shell_overhead_ms"] = (
        shell_turn - native_turn if shell_turn and native_turn else None
    )
    summary["first_tool_ms"] = None
    summary["wall_s"] = None
    if rows:
        start = markers.get(1) or rows[0]["probe_end_ns"]
        summary["wall_s"] = (rows[-1]["probe_end_ns"] - start) / 1e9
    launch = runs.read_json(run_path / "launch.json")
    if launch:
        first_event = markers.get(1) or (rows[0]["probe_end_ns"] if rows else None)
        summary["first_tool_ms"] = timing.diff_ms(first_event, launch["launch_ns"])
    return summary
