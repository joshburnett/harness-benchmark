"""Shared prompt building for the agent benchmarks (dispatch and completion)."""

import argparse
import os
import sys
import time
from pathlib import Path

from harnessbench import clipboard, runs

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


def marker_step(markers: Path, seq: int, command_len: int) -> str:
    content = marker_content(seq, int(command_len * 0.6))
    return f"WRITE file `{markers / f'm{seq:02d}.txt'}` with content: {content}"


def build_prompt(steps: list[str], extra_rules: list[str] | None = None) -> str:
    numbered = "\n".join(
        f"{index}. {step}" for index, step in enumerate(steps, start=1)
    )
    extra = "".join(f"- {rule}\n" for rule in extra_rules or [])
    return f"""This is a latency benchmark of the tool-calling loop. Follow these instructions exactly and mechanically.

Rules:
- Perform the steps strictly in order, ONE tool call per step. Wait for each to finish before starting the next.
- Never run tool calls in parallel, never combine steps, never modify the commands.
- Use ONLY your built-in file-write tool and your built-in terminal tool. Never use MCP tools or any other tools.
- Do not read, list, search, or explore any files. Do not plan, make todo lists, or explain anything between steps.
- WRITE steps: use your file-creation/edit tool (NOT the terminal) to create the file with exactly the given content.
- RUN steps: use your terminal/shell tool to run the command exactly as written, in the foreground.
- If a step fails, do not retry or troubleshoot; continue with the next step.
{extra}- After the last step, reply with only: DONE

Steps:
{numbered}
"""


def add_common_args(parser: argparse.ArgumentParser) -> None:
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
        "--python", default=sys.executable, help="interpreter the agent runs"
    )
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--no-copy", action="store_true", help="don't copy the prompt to the clipboard"
    )


def copy_to_clipboard(prompt: str) -> None:
    try:
        method = clipboard.copy(prompt)
    except OSError as exc:
        print(
            f"# could not copy the prompt to the clipboard ({exc}); copy it from prompt.txt"
        )
        return
    print(f"# prompt copied to the clipboard via {method}")


def save_run(
    run_id: str, args: argparse.Namespace, prompt: str, extra_meta: dict
) -> None:
    directory = runs.run_dir(run_id)
    meta = {
        "run_id": run_id,
        "env": args.env,
        "harness": args.harness,
        "model": args.model,
        "shell": args.shell,
        "notes": args.notes,
        "created_ns": time.time_ns(),
        "host": runs.host_info(),
        **extra_meta,
    }
    runs.write_json(directory / "meta.json", meta)
    (directory / "prompt.txt").write_text(prompt, encoding="utf-8")
    print(prompt)
    print(f"# run id: {run_id}")
    print(f"# prompt saved to {directory / 'prompt.txt'}")
    print(f"# afterwards: python analyze.py --run {run_id}")
    if not args.no_copy:
        copy_to_clipboard(prompt)
