"""Overview captions never hide information silently."""

from pathlib import Path

from PIL import Image, ImageDraw

from annolabel.modules.overview import FONT, OverviewPages, _caption, _plain
from annolabel.schemas.audit import AuditObject, AuditSource


def source(**fields: object) -> AuditSource:
    base = {"id": 7, "image": "a.png", "path": "/a.png", "sidecar": "/a.png.labels.json"}
    return AuditSource(**base, page="/out/page-001.png", **fields)


def test_undrawable_characters_become_visible_marks() -> None:
    assert _plain("café 中 ok") == "caf? ? ok"
    assert FONT.getlength("?") > 0


def test_overflowing_object_key_points_to_the_report() -> None:
    objects = [
        AuditObject(
            tag=n,
            object_id=f"o{n}",
            label=f"label-{n}",
            geometry=["box"],
            annotation_ids=[f"o{n}"],
            notes=[],
        )
        for n in range(1, 30)
    ]
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = [text for text, _ in _caption(draw, source(status="saved", objects=objects))]
    assert lines[1] == "SAVED, 29 objects"
    assert lines[-1] == "+29 total; see audit.json source #7"


def test_pages_flush_one_at_a_time(tmp_path: Path) -> None:
    pages = OverviewPages(tmp_path, 13, "notice")
    for number in range(1, 14):
        pages.add(source(status="unannotated").model_copy(update={"id": number}), None, None)
        assert pages.page is None or number % 12
    assert sorted(p.name for p in tmp_path.iterdir()) == ["page-001.png", "page-002.png"]
