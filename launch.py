"""Launch a CLI harness on a run's prompt and record submit/exit times (CLI harnesses only).

Placeholders in the command: {prompt} (prompt text) and {prompt_file} (path to prompt.txt).
    python launch.py --run <id> -- cursor-agent -p --force {prompt}
    python launch.py --run <id> -- claude -p --dangerously-skip-permissions {prompt}
    python launch.py --run <id> -- opencode run {prompt}
    python launch.py --run <id> --stdin -- pi -p
"""

import argparse
import shutil
import subprocess
import time

from harnessbench import runs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", required=True)
    parser.add_argument("--stdin", action="store_true", help="pipe the prompt on stdin")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("give the harness command after --")
    return args


def main() -> int:
    args = parse_args()
    directory = runs.RUNS_DIR / args.run
    prompt_file = directory / "prompt.txt"
    if not prompt_file.exists():
        raise SystemExit(f"no prompt at {prompt_file}; run make_prompt.py first")
    prompt = prompt_file.read_text(encoding="utf-8")
    replacements = {"{prompt}": prompt, "{prompt_file}": str(prompt_file)}
    command = [replacements.get(part, part) for part in args.command]
    command[0] = shutil.which(command[0]) or command[0]

    launch_ns = time.time_ns()
    try:
        proc = subprocess.run(
            command, input=prompt if args.stdin else None, text=True, cwd=runs.ROOT
        )
    except OSError as exc:
        raise SystemExit(f"could not start {command[0]}: {exc}") from exc
    exit_ns = time.time_ns()
    runs.write_json(
        directory / "launch.json",
        {
            "launch_ns": launch_ns,
            "exit_ns": exit_ns,
            "returncode": proc.returncode,
            "command": args.command,
        },
    )
    print(
        f"\nwall time {(exit_ns - launch_ns) / 1e9:.1f}s; now run: python analyze.py --run {args.run}"
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
