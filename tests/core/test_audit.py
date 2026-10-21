"""Read-only dataset audit: complete inventory, literal counts, bounded pages, safe output."""

import hashlib
import json
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.audit import audit_folder
from annolabel.core.export import export_dataset
from annolabel.core.task import start_task
from annolabel.modules.overview import PAGE_MAX_H, PAGE_W
from annolabel.modules.validation import InputError


def image(path: Path, size: tuple[int, int] = (100, 80)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, "white").save(path)
    return path


def hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def mixed(folder: Path) -> None:
    linked = AnnoLabel(str(image(folder / "nested/a.png")))
    box = linked.annotate("box", "Rectangle", [(10, 10), (60, 50)], note="blurred edge")
    object_id = box["annotation"]["object_id"]
    linked.annotate("polygon", "Rectangle", [(12, 12), (58, 12), (58, 48)], object_id=object_id)
    linked.annotate("label", "outdoor", [], note="guess")
    AnnoLabel(str(image(folder / "a.png"))).annotate("box", "rectangle", [(5, 5), (20, 20)])
    AnnoLabel(str(image(folder / "empty.png"))).annotate("label", "nothing-here", [])
    image(folder / "unlabeled.png")
    (folder / "corrupt.png").write_text("not an image")
    image(folder / "bad.png")
    (folder / "bad.png.labels.json").write_text("{")
    stale = image(folder / "stale.png")
    AnnoLabel(str(stale)).annotate("box", "rectangle", [(5, 5), (20, 20)])
    Image.new("RGB", (100, 80), "black").save(stale)
    (folder / "ghost.png.labels.json").write_text("{}")
    (folder / "notes.txt").write_text("text")
    (folder / "notes.txt.labels.json").write_text("{}")
    start_task(str(folder / "a.png"), str(folder / "old-task"))


def test_audit_accounts_for_every_source_without_changing_files(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    mixed(folder)
    before = hashes(folder)
    reply = audit_folder(str(folder), str(folder / "audit-1"))
    assert {k: v for k, v in hashes(folder).items() if not k.startswith("audit-1/")} == before
    report = json.loads(Path(reply["report"]).read_text())
    status = {s["image"]: s.get("code") or s["status"] for s in report["sources"]}
    assert status == {
        "a.png": "saved",
        "bad.png": "SIDECAR_INVALID",
        "corrupt.png": "IMAGE_UNREADABLE",
        "empty.png": "saved",
        "nested/a.png": "saved",
        "stale.png": "SIDECAR_STALE",
        "unlabeled.png": "unannotated",
    }
    assert reply["status"] == "attention"
    assert reply["totals"] == {
        "sources": 7,
        "saved": 3,
        "saved_zero_objects": 1,
        "unannotated": 1,
        "errors": 3,
        "orphan_sidecars": 2,
        "objects": 2,
        "classifications": 2,
    }
    # Literal labels, one object for a linked box+polygon, scenes kept apart from objects.
    assert report["object_labels"] == {"Rectangle": 1, "rectangle": 1}
    assert report["object_geometry"] == {"box": 1, "box+polygon": 1}
    assert report["scene_classes"] == {"nothing-here": 1, "outdoor": 1}
    nested = next(s for s in report["sources"] if s["image"] == "nested/a.png")
    assert nested["objects"][0]["notes"] == ["blurred edge"]
    assert nested["classifications"][0]["note"] == "guess"
    assert len(nested["objects"][0]["annotation_ids"]) == 2
    orphans = {Path(o["sidecar"]).name: o["error"] for o in report["orphan_sidecars"]}
    assert "does not exist" in orphans["ghost.png.labels.json"]
    assert "not a supported source image" in orphans["notes.txt.labels.json"]
    assert {i["code"] for i in reply["issues"]} == {
        "IMAGE_UNREADABLE",
        "SIDECAR_INVALID",
        "SIDECAR_STALE",
        "ORPHAN_SIDECAR",
    }
    assert all(Path(p).is_file() for p in reply["pages"])
    assert "not mean reviewed" in report["note"] and "NEW task" in report["note"]

    # A later audit (and folder task discovery) does not enroll the first audit's pages.
    again = audit_folder(str(folder), str(tmp_path / "audit-2"))
    assert again["totals"]["sources"] == 7
    assert {k: v for k, v in hashes(folder).items() if not k.startswith("audit-1/")} == before


def test_audit_counts_match_saved_coco_export(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    mixed(folder)
    for name in ["corrupt.png", "bad.png", "bad.png.labels.json", "stale.png.labels.json"]:
        (folder / name).unlink()
    for name in ["ghost.png.labels.json", "notes.txt.labels.json"]:
        (folder / name).unlink()
    reply = audit_folder(str(folder), str(tmp_path / "audit"))
    assert reply["totals"]["errors"] == reply["totals"]["orphan_sidecars"] == 0
    assert reply["status"] == "attention"  # unlabeled.png and stale.png still lack sidecars
    exported = export_dataset(str(folder), str(tmp_path / "dataset"))
    coco = json.loads(Path(exported["annotations"]).read_text())
    assert len(coco["images"]) == reply["totals"]["saved"]
    assert len(coco["annotations"]) == reply["totals"]["objects"]
    assert {c["name"] for c in coco["categories"]} == set(reply["object_labels"])


def test_actions_quote_paths_and_pages_stay_bounded(tmp_path: Path) -> None:
    folder = tmp_path / "it's a folder"
    for number in range(13):
        image(folder / f"img {number:02d}.png", (60, 120) if number % 2 else (120, 60))
    saved = image(folder / "zz café 'quoted'.png")
    AnnoLabel(str(saved)).annotate("box", "très", [(1, 1), (5, 5)])
    reply = audit_folder(str(folder), str(tmp_path / "audit"))
    report = json.loads(Path(reply["report"]).read_text())
    assert len(reply["pages"]) == 2
    for page in reply["pages"]:
        with Image.open(page) as png:
            assert png.width == PAGE_W and png.height <= PAGE_MAX_H
    source = next(s for s in report["sources"] if s["status"] == "saved")
    assert source["page"] == reply["pages"][1] and source["id"] == 14
    for action in source["actions"].values():
        assert shlex.split(action)[2] == str(saved.resolve())
    assert report["object_labels"] == {"très": 1}


def test_exif_orientation_matches_saved_coordinates(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    folder.mkdir()
    exif = Image.Exif()
    exif[0x0112] = 6  # stored portrait, displayed landscape
    Image.new("RGB", (40, 90)).save(folder / "rotated.jpg", exif=exif)
    AnnoLabel(str(folder / "rotated.jpg")).annotate("box", "mark", [(70, 5), (88, 30)])
    reply = audit_folder(str(folder), str(tmp_path / "audit"))
    assert reply["status"] == "ready" and reply["totals"]["objects"] == 1


def test_output_is_protected_and_failures_leave_nothing(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    image(folder / "a.png")
    existing = tmp_path / "existing"
    existing.mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "missing")
    for output in [existing, tmp_path / "link"]:
        with pytest.raises(InputError) as error:
            audit_folder(str(folder), str(output))
        assert error.value.code == "OUTPUT_EXISTS"
    with pytest.raises(InputError) as error:
        audit_folder(str(folder / "a.png"), str(tmp_path / "out"))
    assert error.value.code == "NOT_A_FOLDER" and "annolabel review" in error.value.recovery
    with patch("annolabel.core.audit.os.rename", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            audit_folder(str(folder), str(tmp_path / "out"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing", "images", "link"]
