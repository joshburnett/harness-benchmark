import subprocess

import pytest

import make_prompt


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_posix_command_has_no_errors_in_either_shell(shell):
    command = make_prompt.probe_command("zsh", "python", "RUN", 1)
    stub = command.replace('"python" ', 'echo ready="$t" #', 1)
    result = subprocess.run(
        [shell, "-c", stub], capture_output=True, text=True, check=True
    )
    assert result.stderr == ""
    assert result.stdout.startswith("ready=")


def test_powershell_command_uses_ticks():
    command = make_prompt.probe_command("powershell", r"C:\py\python.exe", "RUN", 3)
    assert command.startswith("$t=[DateTime]::UtcNow.Ticks; & ")
    assert "--seq 3 --ready-ticks $t" in command
