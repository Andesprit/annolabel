"""CLI entrypoint tests; domain workflows live under tests/core."""

import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from tests.helpers import cli


def test_version_matches_installed_distribution() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "annolabel.main", "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == f"annolabel {version('annolabel')}"
    assert not result.stderr


def test_submission_accepts_standard_input(source: Path, tmp_path: Path) -> None:
    task = cli("task", source, "-o", tmp_path / "task", "--geometry", "boxes")
    payload = {
        "classifications": [],
        "objects": [{"key": "one", "label": "square", "box": [10, 10, 30, 30]}],
    }
    receipt = cli("submit", task["task"], "--file", "-", stdin=json.dumps(payload))
    assert receipt["status"] == "saved" and receipt["objects"] == 1
    error = cli("submit", receipt["task"], "--file", "-", stdin="{", success=False)
    assert error["code"] == "VALIDATION_ERROR"
    assert cli("task-status", task["task"]) == receipt


def test_missing_task_returns_actionable_io_error(tmp_path: Path) -> None:
    error = cli("task-status", tmp_path / "missing.json", success=False)
    assert error["code"] == "IO_ERROR" and error["recovery"]
