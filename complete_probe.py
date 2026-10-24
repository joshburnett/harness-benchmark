"""Completion-detection probe. The agent runs this via its shell tool; see make_completion_prompt.py.

Each variant behaves like a kind of command that harnesses can be slow (or wrong)
at noticing has finished. The probe records its exact exit time; the next marker
file the agent writes shows when the harness delivered the result and the model
moved on. Comparing variants against the plain "line" baseline cancels out the
model's turn time, leaving the harness's completion-detection cost.
"""

import time

START_NS = time.time_ns()

import argparse  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from collections.abc import Callable  # noqa: E402

from harnessbench import runs  # noqa: E402

BASELINE = "line"
BACKGROUND_HOLD_S = 5


def silent(args: argparse.Namespace) -> int:
    return 0


def line(args: argparse.Namespace) -> int:
    print(f"step {args.seq} done")
    return 0


def many_lines(args: argparse.Namespace) -> int:
    sys.stdout.write("".join(f"line {i:04d}\n" for i in range(1000)))
    return 0


def no_newline(args: argparse.Namespace) -> int:
    sys.stdout.write(f"step {args.seq} done, no trailing newline")
    return 0


def progress(args: argparse.Namespace) -> int:
    for pct in range(101):
        sys.stdout.write(f"\r[{'#' * (pct // 5):<20}] {pct:3d}%")
        sys.stdout.flush()
        time.sleep(0.01)
    sys.stdout.write("\n")
    return 0


def drip(args: argparse.Namespace) -> int:
    for tick in range(8):
        print(f"tick {tick}", flush=True)
        time.sleep(0.25)
    return 0


def stderr_only(args: argparse.Namespace) -> int:
    print(f"warning: step {args.seq} wrote to stderr", file=sys.stderr)
    return 0


def exit_1(args: argparse.Namespace) -> int:
    print(f"error: step {args.seq} simulated failure (expected)", file=sys.stderr)
    return 1


def sleeper(seconds: int) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        time.sleep(seconds)
        print(f"slept {seconds}s")
        return 0

    return run


def child_argv(args: argparse.Namespace) -> list[str]:
    return [
        sys.executable,
        __file__,
        "--run",
        args.run,
        "--seq",
        str(args.seq),
        "--child",
        str(BACKGROUND_HOLD_S),
    ]


def bg_holds_output(args: argparse.Namespace) -> int:
    """Leave a child running that still holds this command's stdout/stderr open.
    A harness that waits for output EOF stalls until the child exits."""
    print(
        f"started background child holding output for {BACKGROUND_HOLD_S}s", flush=True
    )
    subprocess.Popen(
        child_argv(args), stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr
    )
    return 0


def bg_detached(args: argparse.Namespace) -> int:
    """Control for bg-holds-output: same child, but fully detached from the output."""
    print(f"started detached child for {BACKGROUND_HOLD_S}s", flush=True)
    detach = (
        {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        }
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    subprocess.Popen(
        child_argv(args),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )
    return 0


VARIANTS: dict[str, Callable[[argparse.Namespace], int]] = {
    "line": line,
    "silent": silent,
    "many-lines": many_lines,
    "no-newline": no_newline,
    "progress": progress,
    "drip": drip,
    "stderr": stderr_only,
    "exit-1": exit_1,
    "sleep-2": sleeper(2),
    "sleep-10": sleeper(10),
    "bg-holds-output": bg_holds_output,
    "bg-detached": bg_detached,
    "sleep-30": sleeper(30),
}
DEFAULT_VARIANTS = [name for name in VARIANTS if name != "sleep-30"]
BACKGROUND_VARIANTS = {"bg-holds-output", "bg-detached"}


def run_child(args: argparse.Namespace) -> int:
    time.sleep(args.child)
    record = {"kind": "child_exit", "seq": args.seq, "exit_ns": time.time_ns()}
    runs.append_jsonl(runs.run_dir(args.run) / "complete.jsonl", record)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--seq", type=int, required=True)
    parser.add_argument("--variant", choices=tuple(VARIANTS))
    parser.add_argument("--child", type=float, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.child is not None:
        return run_child(args)
    returncode = VARIANTS[args.variant](args)
    sys.stdout.flush()
    sys.stderr.flush()
    record = {
        "kind": "complete",
        "run": args.run,
        "seq": args.seq,
        "variant": args.variant,
        "start_ns": START_NS,
        "exit_ns": time.time_ns(),
        "returncode": returncode,
    }
    runs.append_jsonl(runs.run_dir(args.run) / "complete.jsonl", record)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
