"""Shared subprocess and JSON helpers, independent of test modules."""
import json
import subprocess
import sys
from pathlib import Path


def cli(*args: object, success: bool = True, stdin: str | None = None) -> dict:
    """Run the installed CLI and check its JSON output contract.

    :param args: Command arguments.
    :param success: Whether the operation should succeed.
    :param stdin: Optional JSON supplied through standard input.
    :returns: Decoded success receipt or error.
    """
    result = subprocess.run([sys.executable, "-m", "annolabel.main", *map(str, args)],
                            text=True, input=stdin, capture_output=True)
    assert result.returncode == (0 if success else 2), result.stderr
    if success:
        assert not result.stderr
        return json.loads(result.stdout)
    assert not result.stdout
    return json.loads(result.stderr)


def save(path: Path, value: object) -> Path:
    """Write a JSON input file.

    :param path: Destination filename.
    :param value: JSON-compatible input.
    :returns: Destination path.
    """
    path.write_text(json.dumps(value))
    return path
