"""File boundaries retain paths, flags and practical recovery steps."""

from pathlib import Path

import pytest

from annolabel.core.inputs import read_categories, read_model, read_text
from annolabel.modules.validation import InputError
from annolabel.schemas.task import TaskHandle


@pytest.mark.parametrize("text", ["red\nblue", "null", '["leaf", "leaf"]', "[1]"])
def test_bad_categories_name_file_and_expected_format(tmp_path: Path, text: str) -> None:
    path = tmp_path / "category names.json"
    path.write_text(text)
    with pytest.raises(InputError) as caught:
        read_categories(str(path))
    error = caught.value
    assert error.code == "VALIDATION_ERROR"
    assert str(path) in str(error) and "--categories" in str(error)
    assert "JSON array" in error.recovery
    assert "Value error," not in str(error.details)


def test_missing_and_non_text_inputs_name_the_option(tmp_path: Path) -> None:
    missing = tmp_path / "missing instructions.txt"
    with pytest.raises(InputError) as caught:
        read_text(str(missing), "--instructions")
    assert caught.value.code == "IO_ERROR"
    assert str(missing) in str(caught.value) and "--instructions" in caught.value.recovery
    assert "Errno" not in str(caught.value)
    missing.write_bytes(b"\xff")
    with pytest.raises(InputError, match="UTF-8"):
        read_text(str(missing), "--instructions")


@pytest.mark.parametrize("text", ['["leaf"]', "{}", "not JSON"])
def test_wrong_handle_uses_handle_recovery(tmp_path: Path, text: str) -> None:
    path = tmp_path / "not a task.json"
    path.write_text(text)
    with pytest.raises(InputError) as caught:
        read_model(str(path), TaskHandle, "task handle", "Pass the task.json returned by task.")
    error = caught.value
    assert str(path) in str(error) and "task.json" in error.recovery
    assert all(isinstance(detail, str) for detail in error.details)
    assert not any(
        word in str(error.details) for word in ["Value error,", "input_value", "pydantic"]
    )
