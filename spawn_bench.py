"""Process-spawn latency baseline: how long do shells, Python, git, etc. take to start?

Run it manually from a plain terminal (baseline), and optionally from inside an agent
harness (make_prompt.py --spawn-bench) to see whether processes spawned under the
harness are treated differently by security software.

Each command runs twice back to back ("first" then "repeat") after each idle period,
which shows whether a per-executable check is cached and for how long:
    python spawn_bench.py --idle 0,10,60,300 --reps 3
"""

import argparse
import random
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from harnessbench import runs, stats, timing

PY_STAMP = "import time; print(time.time_ns())"
PS_STAMP = "[DateTime]::UtcNow.Ticks"


@dataclass
class SpawnCmd:
    name: str
    argv: list[str]
    stamp: str | None = (
        None  # how the child reports its own start: ns | ticks | epoch | ms
    )
    script: tuple[str, str] | None = (
        None  # (suffix, body) written to "{script}" in argv
    )
    fresh: bool = False  # write a never-seen-before script file for every execution


def find_git_bash() -> str | None:
    """Git for Windows bash.exe (not System32\\bash.exe, which is the WSL launcher)."""
    git = shutil.which("git")
    candidates = [Path(git).resolve().parent.parent / "bin" / "bash.exe"] if git else []
    candidates.append(Path(r"C:\Program Files\Git\bin\bash.exe"))
    return next((str(path) for path in candidates if path.exists()), None)


def python_commands() -> list[SpawnCmd]:
    py = sys.executable
    body = ".py", "import time\nprint(time.time_ns())\n# {nonce}\n"
    commands = [
        SpawnCmd("python -I -S -c", [py, "-I", "-S", "-c", PY_STAMP], "ns"),
        SpawnCmd("python -c", [py, "-c", PY_STAMP], "ns"),
        SpawnCmd("python same.py", [py, "{script}"], "ns", body),
        SpawnCmd("python fresh.py", [py, "{script}"], "ns", body, fresh=True),
    ]
    base = getattr(sys, "_base_executable", py)
    if sys.platform == "win32" and Path(base) != Path(py):
        commands.append(SpawnCmd("python(base exe) -c", [base, "-c", PY_STAMP], "ns"))
    return commands


def windows_commands() -> list[SpawnCmd]:
    ps_flags = ["-NoLogo", "-NonInteractive"]
    ps1 = ".ps1", f"{PS_STAMP}\n# {{nonce}}\n"
    file_flags = [
        *ps_flags,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "{script}",
    ]
    commands = [
        SpawnCmd("cmd /c exit", ["cmd.exe", "/d", "/c", "exit 0"]),
        SpawnCmd("whoami.exe", ["whoami.exe"]),
        SpawnCmd(
            "powershell -NoProfile",
            ["powershell.exe", *ps_flags, "-NoProfile", "-Command", PS_STAMP],
            "ticks",
        ),
        SpawnCmd(
            "powershell +profile",
            ["powershell.exe", *ps_flags, "-Command", PS_STAMP],
            "ticks",
        ),
        SpawnCmd("powershell same.ps1", ["powershell.exe", *file_flags], "ticks", ps1),
        SpawnCmd(
            "powershell fresh.ps1",
            ["powershell.exe", *file_flags],
            "ticks",
            ps1,
            fresh=True,
        ),
    ]
    if pwsh := shutil.which("pwsh"):
        commands += [
            SpawnCmd(
                "pwsh -NoProfile",
                [pwsh, *ps_flags, "-NoProfile", "-Command", PS_STAMP],
                "ticks",
            ),
            SpawnCmd("pwsh +profile", [pwsh, *ps_flags, "-Command", PS_STAMP], "ticks"),
        ]
    if git_bash := find_git_bash():
        commands += [
            SpawnCmd("git-bash -c", [git_bash, "-c", "echo $EPOCHREALTIME"], "epoch"),
            SpawnCmd(
                "git-bash -l -c", [git_bash, "-l", "-c", "echo $EPOCHREALTIME"], "epoch"
            ),
        ]
    return commands


def unix_commands() -> list[SpawnCmd]:
    commands = [
        SpawnCmd("true", [shutil.which("true") or "/usr/bin/true"]),
        SpawnCmd("sh -c true", ["/bin/sh", "-c", "true"]),
    ]
    if bash := shutil.which("bash"):
        commands.append(
            SpawnCmd("bash -c", [bash, "-c", "echo $EPOCHREALTIME"], "epoch")
        )
        commands.append(
            SpawnCmd(
                "bash -i -c (rc files)",
                [bash, "-i", "-c", "echo $EPOCHREALTIME"],
                "epoch",
            )
        )
    if zsh := shutil.which("zsh"):
        zsh_stamp = "zmodload zsh/datetime; echo $EPOCHREALTIME"
        sh_body = ".zsh", f"{zsh_stamp}\n# {{nonce}}\n"
        commands += [
            SpawnCmd("zsh -c", [zsh, "-c", zsh_stamp], "epoch"),
            SpawnCmd("zsh -i -c (rc files)", [zsh, "-i", "-c", zsh_stamp], "epoch"),
            SpawnCmd("zsh fresh.zsh", [zsh, "{script}"], "epoch", sh_body, fresh=True),
        ]
    return commands


def all_commands() -> list[SpawnCmd]:
    platform_cmds = windows_commands() if sys.platform == "win32" else unix_commands()
    commands = platform_cmds + python_commands()
    if git := shutil.which("git"):
        commands.append(SpawnCmd("git --version", [git, "--version"]))
    if node := shutil.which("node"):
        commands.append(
            SpawnCmd("node -e", [node, "-e", "console.log(Date.now())"], "ms")
        )
    return commands


def parse_stamp(stdout: str, kind: str | None) -> int | None:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if kind is None or not lines:
        return None
    text = lines[-1]
    try:
        match kind:
            case "ns":
                return int(text)
            case "ticks":
                return timing.dotnet_ticks_to_ns(int(text))
            case "ms":
                return int(text) * 1_000_000
            case "epoch":
                return timing.epoch_text_to_ns(text)
    except ValueError:
        return None
    return None


def script_path(cmd: SpawnCmd, workdir: Path, cache: dict[str, Path]) -> str | None:
    if cmd.script is None:
        return None
    if not cmd.fresh and cmd.name in cache:
        return str(cache[cmd.name])
    suffix, body = cmd.script
    nonce = uuid.uuid4().hex
    path = workdir / f"s_{nonce[:12]}{suffix}"
    path.write_text(body.format(nonce=nonce), encoding="utf-8")
    cache[cmd.name] = path
    return str(path)


def run_once(cmd: SpawnCmd, script: str | None, timeout: float) -> dict:
    argv = [script if arg == "{script}" else arg for arg in cmd.argv]
    launch_ns = time.time_ns()
    start = time.perf_counter_ns()
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:300]}
    result = {
        "total_ms": (time.perf_counter_ns() - start) / 1e6,
        "startup_ms": timing.diff_ms(parse_stamp(proc.stdout, cmd.stamp), launch_ns),
        "returncode": proc.returncode,
    }
    if proc.returncode:
        result["stderr"] = proc.stderr[-300:]
    return result


def run_bench(args: argparse.Namespace, out: Path) -> list[dict]:
    commands = all_commands()
    if args.only:
        commands = [
            c for c in commands if any(o.lower() in c.name.lower() for o in args.only)
        ]
    last_end: dict[str, float] = {}
    records: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="spawnbench-") as tmp:
        cache: dict[str, Path] = {}
        for rep in range(args.reps):
            for idle in args.idle:
                if idle:
                    print(f"rep {rep + 1}/{args.reps}: idle {idle}s ...", flush=True)
                    time.sleep(idle)
                for cmd in random.sample(commands, len(commands)):
                    for position in ("first", "repeat"):
                        since = last_end.get(cmd.name)
                        result = run_once(
                            cmd, script_path(cmd, Path(tmp), cache), args.timeout
                        )
                        last_end[cmd.name] = time.monotonic()
                        record = {
                            "kind": "spawn",
                            "env": args.env,
                            "tag": args.tag,
                            "name": cmd.name,
                            "rep": rep,
                            "idle_s": idle,
                            "position": position,
                            "since_same_s": None
                            if since is None
                            else round(time.monotonic() - since, 3),
                            **result,
                        }
                        records.append(record)
                        runs.append_jsonl(out, record)
    return records


def print_summary(records: list[dict]) -> None:
    groups = stats.group_by(records, lambda r: (r["name"], r["idle_s"], r["position"]))
    rows = []
    for (name, idle, position), group in sorted(groups.items()):
        startup = stats.summarize(r.get("startup_ms") for r in group)
        total = stats.summarize(r.get("total_ms") for r in group)
        errors = sum(1 for r in group if r.get("error") or r.get("returncode"))
        rows.append(
            [
                name,
                str(idle),
                position,
                str(total["n"]),
                stats.fmt(startup["median"]),
                stats.fmt(total["median"]),
                stats.fmt(total["p90"]),
                stats.fmt(total["max"]),
                str(errors),
            ]
        )
    headers = [
        "command",
        "idle_s",
        "position",
        "n",
        "startup_ms",
        "total_ms",
        "p90",
        "max",
        "errors",
    ]
    print(stats.render_table(headers, rows, text_cols=3))
    print(
        "startup_ms = launch -> first line of the child runs (only where the child prints a stamp)"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env", default=runs.default_env())
    parser.add_argument("--tag", default="manual", help="e.g. manual, agent-cursor-gui")
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument(
        "--idle", default="0", help="comma-separated idle seconds before each round"
    )
    parser.add_argument("--only", nargs="*", help="substring filter on command names")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument(
        "--quick", action="store_true", help="2 reps, no idle (for agent runs)"
    )
    parser.add_argument("--run", help="store results inside an agent run directory")
    args = parser.parse_args()
    args.idle = [float(v) if "." in v else int(v) for v in args.idle.split(",")]
    if args.quick:
        args.reps, args.idle = 2, [0]
    return args


def main() -> int:
    args = parse_args()
    out = (
        runs.run_dir(args.run) / "spawn.jsonl"
        if args.run
        else runs.kind_file("spawn", args.env, args.tag)
    )
    runs.append_jsonl(
        out,
        {"kind": "meta", "env": args.env, "tag": args.tag, "host": runs.host_info()},
    )
    print(f"writing {out}")
    print_summary(run_bench(args, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
