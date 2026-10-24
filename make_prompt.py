"""Create a dispatch-benchmark run: writes runs/<id>/meta.json + prompt.txt and prints the prompt.

Paste the prompt into a fresh chat/session in the harness under test (auto-approve on).
In "alternate" mode the agent interleaves a native file-write tool call (no process
spawn; its timestamp is the file mtime) with a shell tool call to probe.py, so the
gaps between them separate "LLM + harness" time from "shell tool dispatch" time.
"""

import argparse
from pathlib import Path

from harnessbench import prompts, runs


def build_steps(args: argparse.Namespace, run_id: str, markers: Path) -> list[str]:
    steps: list[str] = []
    for seq in range(1, args.calls + 1):
        command = prompts.probe_command(args.shell, args.python, run_id, seq)
        if args.mode == "alternate":
            steps.append(prompts.marker_step(markers, seq, len(command)))
        steps.append(f"RUN: `{command}`")
    if args.mode == "alternate":
        steps.append(prompts.marker_step(markers, args.calls + 1, len(command)))
    if args.spawn_bench:
        bench_args = f"--quick --env {args.env} --tag agent-{runs.slug(args.harness)} --run {run_id}"
        bench = prompts.invoke(args.shell, args.python, "spawn_bench.py", bench_args)
        steps.append(f"RUN: `{bench}`")
    return steps


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    prompts.add_common_args(parser)
    parser.add_argument(
        "--calls", type=int, default=12, help="number of shell probe calls"
    )
    parser.add_argument(
        "--mode", choices=("alternate", "shell-only"), default="alternate"
    )
    parser.add_argument(
        "--spawn-bench", action="store_true", help="end with spawn_bench --quick"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = runs.new_run_id(args.env, args.harness, args.model)
    markers = runs.run_dir(run_id) / "markers"
    markers.mkdir(exist_ok=True)
    prompt = prompts.build_prompt(build_steps(args, run_id, markers))
    prompts.save_run(
        run_id,
        args,
        prompt,
        {"benchmark": "dispatch", "calls": args.calls, "mode": args.mode},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
