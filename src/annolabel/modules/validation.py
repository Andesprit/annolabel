"""Plain diagnostics shared by annotation input boundaries."""

from pydantic import ValidationError


class InputError(ValueError):
    """A failure with a specific recovery step suitable for CLI JSON."""

    def __init__(
        self, code: str, message: str, recovery: str, details: list[str] | None = None
    ) -> None:
        super().__init__(message)
        self.code, self.recovery, self.details = code, recovery, details


def validation_details(error: ValidationError, data: object = None) -> list[str]:
    """Name fields and object keys without exposing library internals or input dumps."""
    details = []
    for issue in error.errors(include_url=False, include_input=False):
        location = list(issue["loc"])
        prefix = ""
        if len(location) >= 2 and location[0] in {"objects", "classifications"}:
            collection, index = location[:2]
            prefix = f"{collection}[{index}]"
            items = data.get(collection) if isinstance(data, dict) else None
            if isinstance(items, list) and isinstance(index, int) and index < len(items):
                item = items[index]
                key = (
                    item.get("key" if collection == "objects" else "label")
                    if isinstance(item, dict)
                    else None
                )
                if isinstance(key, str):
                    prefix += f" ({key})"
            location = location[2:]
        field = "".join(
            f"[{part}]" if isinstance(part, int) else f".{part}" for part in location
        ).lstrip(".")
        if issue["type"] == "missing":
            message = f"field {field} is required"
            if field == "classifications":
                message += '; send "classifications": [] when there are none'
        elif issue["type"] == "extra_forbidden":
            message = f"unknown field {field}"
            if field == "bbox":
                message += "; use box"
        else:
            message = str(issue.get("ctx", {}).get("error", issue["msg"]))
            if field:
                message = f"{field}: {message}"
        details.append(f"{prefix}: {message}" if prefix else message)
    return details
