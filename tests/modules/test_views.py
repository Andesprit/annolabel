"""Bundle staging cleans up after filesystem failures."""

from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from annolabel.modules.views import write_bundle
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
