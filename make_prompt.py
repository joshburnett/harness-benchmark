"""Create an agent benchmark run: writes runs/<id>/meta.json + prompt.txt and prints the prompt.

Paste the prompt into a fresh chat/session in the harness under test (auto-approve on).
In "alternate" mode the agent interleaves a native file-write tool call (no process
spawn; its timestamp is the file mtime) with a shell tool call to probe.py, so the
gaps between them separate "LLM + harness" time from "shell tool dispatch" time.
"""

import argparse
import os
import sys
import time
from pathlib import Path

from harnessbench import runs

SHELLS = ("powershell", "pwsh", "gitbash", "bash", "zsh")
FILLER = (
    "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
    "november oscar papa quebec romeo sierra tango uniform victor whiskey xray"
).split()


def default_shell() -> str:
    if sys.platform == "win32":
        return "powershell"
    name = Path(os.environ.get("SHELL", "bash")).name
    return name if name in SHELLS else "bash"


def shell_path(path: Path, shell: str) -> str:
    return path.as_posix() if shell == "gitbash" else str(path)


def invoke(shell: str, python: str, script: str, script_args: str) -> str:
    py = shell_path(Path(python), shell)
    target = shell_path(runs.ROOT / script, shell)
    call = f'"{py}" "{target}" {script_args}'
    return f"& {call}" if shell in ("powershell", "pwsh") else call


def probe_command(shell: str, python: str, run_id: str, seq: int) -> str:
    args = f"--run {run_id} --seq {seq}"
    if shell in ("powershell", "pwsh"):
        call = invoke(shell, python, "probe.py", f"{args} --ready-ticks $t")
        return f"$t=[DateTime]::UtcNow.Ticks; {call}"
    # Harnesses don't always use the login shell, so one command must work in zsh and bash.
    # bash < 5 (macOS /bin/bash) has no $EPOCHREALTIME; the probe then records no ready time.
    call = invoke(shell, python, "probe.py", f'{args} --ready-epoch "$t"')
    return f'[ -n "$ZSH_VERSION" ] && zmodload zsh/datetime; t=$EPOCHREALTIME; {call}'


def marker_content(seq: int, target_len: int) -> str:
    """Marker text of roughly the probe command's length, so both steps cost similar output tokens."""
    words = [f"marker {seq:02d}"]
    while len(" ".join(words)) < target_len:
        words.append(FILLER[(len(words) - 1) % len(FILLER)])
    return " ".join(words)


def build_steps(args: argparse.Namespace, run_id: str, markers: Path) -> list[str]:
    steps: list[str] = []
    for seq in range(1, args.calls + 1):
        command = probe_command(args.shell, args.python, run_id, seq)
        if args.mode == "alternate":
            content = marker_content(seq, int(len(command) * 0.6))
            steps.append(
                f"WRITE file `{markers / f'm{seq:02d}.txt'}` with content: {content}"
            )
        steps.append(f"RUN: `{command}`")
    if args.mode == "alternate":
        content = marker_content(args.calls + 1, int(len(command) * 0.6))
        steps.append(
            f"WRITE file `{markers / f'm{args.calls + 1:02d}.txt'}` with content: {content}"
        )
    if args.spawn_bench:
        bench_args = f"--quick --env {args.env} --tag agent-{runs.slug(args.harness)} --run {run_id}"
        steps.append(
            f"RUN: `{invoke(args.shell, args.python, 'spawn_bench.py', bench_args)}`"
        )
    return steps


def build_prompt(steps: list[str]) -> str:
    numbered = "\n".join(
        f"{index}. {step}" for index, step in enumerate(steps, start=1)
    )
    return f"""This is a latency benchmark of the tool-calling loop. Follow these instructions exactly and mechanically.

Rules:
- Perform the steps strictly in order, ONE tool call per step. Wait for each to finish before starting the next.
- Never run tool calls in parallel, never combine steps, never modify the commands.
- Use ONLY your built-in file-write tool and your built-in terminal tool. Never use MCP tools or any other tools.
- Do not read, list, search, or explore any files. Do not plan, make todo lists, or explain anything between steps.
- WRITE steps: use your file-creation/edit tool (NOT the terminal) to create the file with exactly the given content.
- RUN steps: use your terminal/shell tool to run the command exactly as written, in the foreground.
- If a step fails, do not retry or troubleshoot; continue with the next step.
- After the last step, reply with only: DONE

Steps:
{numbered}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--harness", required=True, help="e.g. cursor-gui, cursor-cli, cline, pi"
    )
    parser.add_argument(
        "--model", required=True, help="model name as selected in the harness"
    )
    parser.add_argument(
        "--env", default=runs.default_env(), help="e.g. corp-win, corp-wsl, mac"
    )
    parser.add_argument("--shell", choices=SHELLS, default=default_shell())
    parser.add_argument(
        "--calls", type=int, default=12, help="number of shell probe calls"
    )
    parser.add_argument(
        "--mode", choices=("alternate", "shell-only"), default="alternate"
    )
    parser.add_argument(
        "--python", default=sys.executable, help="interpreter the agent runs"
    )
    parser.add_argument(
        "--spawn-bench", action="store_true", help="end with spawn_bench --quick"
    )
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_id = runs.new_run_id(args.env, args.harness, args.model)
    directory = runs.run_dir(run_id)
    markers = directory / "markers"
    markers.mkdir(exist_ok=True)
    prompt = build_prompt(build_steps(args, run_id, markers))
    runs.write_json(
        directory / "meta.json",
        {
            "run_id": run_id,
            "env": args.env,
            "harness": args.harness,
            "model": args.model,
            "shell": args.shell,
            "calls": args.calls,
            "mode": args.mode,
            "notes": args.notes,
            "created_ns": time.time_ns(),
            "host": runs.host_info(),
        },
    )
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(prompt)
    print(f"# run id: {run_id}")
    print(f"# prompt saved to {directory / 'prompt.txt'}")
    print(f"# afterwards: python analyze.py --run {run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
