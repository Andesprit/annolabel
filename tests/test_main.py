"""CLI entrypoint tests; domain workflows live under tests/core."""

import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    "arguments",
    [
        ["info", "missing.png"],
        ["submit", "missing-task.json", "--file", "missing-payload.json"],
        ["task", "IMAGE"],
        ["task", "IMAGE", "--output", "OUTPUT", "--geometry", "triangles"],
        ["label", "IMAGE", "--label", " "],
        ["box", "IMAGE", "--label", "leaf", "--xyxy", "9", "9", "1", "1"],
        ["polygon", "IMAGE", "--label", "leaf", "--points", "[[1,1],[2"],
        ["link", "IMAGE", "--ids", "missing", "other"],
        ["remove", "IMAGE", "--id", "missing"],
        ["render", "IMAGE", "--output", "preview.jpg"],
        ["mask", "IMAGE", "--id", "missing", "--output", "mask.png"],
        ["prepare", "IMAGE", "--output", "OUTPUT", "--categories", "CATEGORIES"],
        ["apply", "IMAGE", "--file", "missing.json", "--output", "OUTPUT"],
        ["review", "IMAGE", "--output", "OUTPUT", "--per-page", "5"],
        ["export", "missing", "--output", "OUTPUT"],
        ["task", "IMAGE", "--output", "OUTPUT", "--instructions", "missing.txt"],
        ["task-status", "CATEGORIES"],
        ["box", "IMAGE", "--label", "leaf", "--xyxy", "x", "0", "10", "10"],
    ],
)
def test_all_command_errors_share_plain_json_contract(
    source: Path, tmp_path: Path, monkeypatch, capsys, arguments: list[str]
) -> None:
    from annolabel.main import main

    categories = tmp_path / "bad-categories.json"
    categories.write_text("not JSON")
    substitutions = {
        "IMAGE": str(source),
        "OUTPUT": str(tmp_path / "output"),
        "CATEGORIES": str(categories),
    }
    arguments = [substitutions.get(arg, arg) for arg in arguments]
    monkeypatch.setattr(sys, "argv", ["/path/main.py", *arguments])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    error = json.loads(output.err)
    assert all(error.get(field) for field in ["error", "code", "recovery"])
    if error["code"] == "INVALID_ARGUMENTS":
        assert error["recovery"].startswith("Run annolabel")
        assert "main.py" not in error["recovery"]
    assert all(isinstance(detail, str) for detail in error.get("details", []))
    assert not any(
        noise in output.err.lower()
        for noise in [
            "errno",
            "pydantic",
            "value error,",
            "input_value",
            "traceback",
            "errors.pydantic.dev",
        ]
    )
    if arguments[0] == "polygon":
        assert "--points" in output.err and "line 1" in output.err
    if "--categories" in arguments:
        assert str(categories) in output.err and "JSON array" in output.err
    if arguments[0] == "task-status":
        assert str(categories) in output.err and "task.json" in output.err
    assert not Path(str(source) + ".labels.json").exists()


@pytest.mark.parametrize("command", ["task", "prepare", "review", "export", "render"])
def test_existing_output_names_path_and_keeps_contents(
    source: Path, tmp_path: Path, monkeypatch, capsys, command: str
) -> None:
    from annolabel.main import main

    output = tmp_path / ("existing.png" if command == "render" else "existing")
    output.write_bytes(b"keep this existing file")
    monkeypatch.setattr(sys, "argv", ["annolabel", command, str(source), "--output", str(output)])
    with pytest.raises(SystemExit) as caught:
        main()
    assert caught.value.code == 2
    reply = capsys.readouterr()
    assert not reply.out
    error = json.loads(reply.err)
    assert error["code"] == "OUTPUT_EXISTS"
    assert str(output) in error["error"] and "--output" in error["recovery"]
    assert output.read_bytes() == b"keep this existing file"
