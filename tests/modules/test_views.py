"""Bundle staging cleans up after filesystem failures."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from annolabel.modules.views import write_bundle, write_preview
from annolabel.schemas.annotations import Document
from annolabel.schemas.workflow import Rules


def test_failed_publish_removes_staged_views(document: Document, tmp_path: Path) -> None:
    output = tmp_path / "views"
    with patch("annolabel.modules.views.os.rename", side_effect=OSError("publish failed")):
        with pytest.raises(OSError, match="publish failed"):
            write_bundle(
                Image.new("RGB", (100, 80)), document, str(output), rules=Rules(), short=True
            )
    assert not output.exists()
    assert not list(tmp_path.glob(".annolabel-views-*"))


def test_short_bundle_omits_unused_panels_and_says_so(document: Document, tmp_path: Path) -> None:
    image = Image.new("RGB", (100, 80), "white")
    short = write_bundle(
        image, document, str(tmp_path / "short"), rules=Rules(), prepare=True, short=True
    )
    assert short["overview"] is None and short["grid"] is None and short["review_pages"] == []
    assert sorted(p.name for p in (tmp_path / "short").glob("*.png")) == [
        "original.png",
        "view.png",
    ]
    detailed = write_bundle(
        image, document, str(tmp_path / "detailed"), rules=Rules(), prepare=True
    )
    assert Path(detailed["overview"]).exists() and Path(detailed["grid"]).exists()
    for bundle in [short, detailed]:
        views = json.loads(Path(bundle["packet"]).read_text())["views"]
        assert all(Path(v["path"]).exists() for v in views)


def test_write_preview_failure_leaves_no_output_or_staging(
    document: Document, tmp_path: Path
) -> None:
    parent = tmp_path / "previews"
    with pytest.raises(OSError):
        write_preview(
            Image.new("RGB", (100, 80)), document, None, parent / "out", {"a/b.json": "{}"}
        )
    assert list(parent.iterdir()) == []
