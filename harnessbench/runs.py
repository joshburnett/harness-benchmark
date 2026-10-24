"""Where results live and how they are written. Everything is plain JSON / JSONL under runs/."""

import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"


def run_dir(run_id: str) -> Path:
    path = RUNS_DIR / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_file(kind: str, env: str, tag: str | None = None) -> Path:
    """Results file for stand-alone benchmarks (spawn, net, llm) run outside an agent run."""
    path = RUNS_DIR / kind / f"{new_run_id(env, tag)}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-").lower()


def new_run_id(*parts: str | None) -> str:
    suffix = "-".join(slug(part) for part in parts if part)
    return f"{datetime.now():%Y%m%d-%H%M%S}-{suffix}".rstrip("-")


def is_wsl() -> bool:
    return (
        sys.platform.startswith("linux") and "microsoft" in platform.release().lower()
    )


def default_env() -> str:
    system = "wsl" if is_wsl() else platform.system().lower()
    return f"{platform.node().split('.')[0]}-{system}".lower()


def host_info() -> dict:
    return {
        "node": platform.node(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "wsl": is_wsl(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
    }
