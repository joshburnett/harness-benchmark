"""Tool-call probe. The agent runs this via its shell tool; see make_prompt.py.

The calling one-liner captures a shell-builtin timestamp *before* launching Python
(no process spawn needed), so one record decomposes a tool call into:
  shell process created -> shell ready -> python created -> python running -> done.
"""

import time

PY_T0_NS = time.time_ns()

import argparse  # noqa: E402
import sys  # noqa: E402

from harnessbench import procinfo, runs, timing  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--seq", type=int, required=True)
    parser.add_argument("--ready-ticks", help="PowerShell [DateTime]::UtcNow.Ticks")
    parser.add_argument("--ready-epoch", help="bash/zsh $EPOCHREALTIME")
    return parser.parse_args()


def shell_ready_ns(args: argparse.Namespace) -> int | None:
    if args.ready_ticks and args.ready_ticks.isdigit():
        return timing.dotnet_ticks_to_ns(int(args.ready_ticks))
    return timing.epoch_text_to_ns(args.ready_epoch)


def main() -> int:
    args = parse_args()
    record = {
        "kind": "probe",
        "run": args.run,
        "seq": args.seq,
        "shell_ready_ns": shell_ready_ns(args),
        "py_t0_ns": PY_T0_NS,
        "chain": procinfo.ancestors(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
    }
    record["probe_end_ns"] = time.time_ns()
    runs.append_jsonl(runs.run_dir(args.run) / "probe.jsonl", record)
    print(f"probe {args.seq} recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
