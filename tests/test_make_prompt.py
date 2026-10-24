import subprocess
import sys

import pytest

import complete_probe
import make_completion_prompt
from harnessbench import prompts, runs


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_posix_command_has_no_errors_in_either_shell(shell):
    command = prompts.probe_command("zsh", "python", "RUN", 1)
    stub = command.replace('"python" ', 'echo ready="$t" #', 1)
    result = subprocess.run(
        [shell, "-c", stub], capture_output=True, text=True, check=True
    )
    assert result.stderr == ""
    assert result.stdout.startswith("ready=")


def test_powershell_command_uses_ticks():
    command = prompts.probe_command("powershell", r"C:\py\python.exe", "RUN", 3)
    assert command.startswith("$t=[DateTime]::UtcNow.Ticks; & ")
    assert "--seq 3 --ready-ticks $t" in command


def test_completion_plan_runs_every_variant_once_per_round():
    variants = ["line", "silent", "drip"]
    plan = make_completion_prompt.plan_steps(variants, reps=4, seed=7)
    assert [s["seq"] for s in plan] == list(range(1, 13))
    for start in range(0, 12, 3):
        assert sorted(s["variant"] for s in plan[start : start + 3]) == sorted(variants)


@pytest.mark.parametrize(
    "variant", ["line", "no-newline", "progress", "stderr", "exit-1"]
)
def test_complete_probe_variants_record_exit(variant):
    run_id = f"pytest-{variant}"
    result = subprocess.run(
        [
            sys.executable,
            complete_probe.__file__,
            "--run",
            run_id,
            "--seq",
            "1",
            "--variant",
            variant,
        ],
        capture_output=True,
        text=True,
    )
    path = runs.RUNS_DIR / run_id / "complete.jsonl"
    try:
        [record] = runs.read_jsonl(path)
    finally:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    assert record["variant"] == variant
    assert (
        record["returncode"] == result.returncode == (1 if variant == "exit-1" else 0)
    )
    assert record["start_ns"] < record["exit_ns"]
