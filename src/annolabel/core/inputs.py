"""Read named CLI inputs with file-specific diagnostics."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from annolabel.modules.validation import InputError, validation_details
from annolabel.schemas.workflow import Rules

Model = TypeVar("Model", bound=BaseModel)


def read_text(path: str, option: str) -> str:
    """Read a UTF-8 input and retain its path and option in any failure."""
    try:
        return Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as error:
        raise InputError(
            "IO_ERROR",
            f"Cannot read {option} file {path!r}: {error.strerror or 'file unavailable'}.",
            f"Pass an existing readable file with {option}: {path!r}.",
        ) from None
    except UnicodeError:
        raise InputError(
            "VALIDATION_ERROR",
            f"{option} file {path!r} is not UTF-8 text.",
            f"Save {path!r} as UTF-8 text and retry {option}.",
        ) from None


def parse_json(text: str, source: str) -> object:
    """Parse JSON while retaining the input flag or filename and exact location."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise InputError(
            "VALIDATION_ERROR",
            f"Invalid JSON in {source}: {error.msg} at line {error.lineno}, column {error.colno}.",
            f"Correct the JSON in {source} at line {error.lineno}, column {error.colno} and retry.",
        ) from None


def read_model(path: str, schema: type[Model], option: str, recovery: str) -> Model:
    """Validate a saved input file without leaking a model dump into the error."""
    try:
        text = read_text(path, option)
    except InputError as error:
        raise InputError(error.code, str(error), f"{error.recovery} {recovery}") from None
    try:
        return schema.model_validate_json(text)
    except ValidationError as error:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        raise InputError(
            "VALIDATION_ERROR",
            f"Invalid {option} file {path!r}.",
            recovery,
            validation_details(error, data),
        ) from None


def read_categories(path: str) -> list[str]:
    """Use the same category vocabulary rules for tasks, packets and exports."""
    source = f"--categories file {path!r}"
    recovery = f'Set {source} to a JSON array of unique nonempty label names, for example ["leaf"].'
    try:
        data = parse_json(read_text(path, "--categories"), source)
        if not isinstance(data, list):
            raise ValueError("categories must be a JSON array of label names")
        return Rules(categories=data).categories
    except InputError as error:
        raise InputError(
            error.code, str(error), f"{error.recovery} {recovery}", error.details
        ) from None
    except ValueError as error:
        details = validation_details(error) if isinstance(error, ValidationError) else [str(error)]
        raise InputError("VALIDATION_ERROR", f"Invalid {source}.", recovery, details) from None


def read_rules(instructions: str | None, categories: str | None, geometry: str) -> Rules:
    """Read researcher rule files once so tasks and packets share them."""
    return Rules(
        geometry=geometry,
        **({"instructions": read_text(instructions, "--instructions")} if instructions else {}),
        categories=read_categories(categories) if categories else None,
    )
