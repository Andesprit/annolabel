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
from annolabel.schemas.audit import AuditReport
from tests.helpers import cli


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


def noted_folder(folder: Path) -> dict[str, str]:
    linked = AnnoLabel(str(image(folder / "linked.png")))
    box = linked.annotate("box", "cell", [(10, 10), (60, 50)], note="blurred edge")
    ids = {"linked": box["annotation"]["object_id"]}
    linked.annotate(
        "polygon",
        "cell",
        [(12, 12), (58, 12), (58, 48)],
        object_id=ids["linked"],
        note="blurred edge",
    )
    linked.annotate("box", "cell", [(20, 20), (70, 60)])  # overlapping unnoted sibling
    linked.annotate("box", "cell", [(1, 1), (9, 9)], note="   ")  # blank: not a note
    exif = Image.Exif()
    exif[0x0112] = 6  # stored 40x90 portrait, oriented 90x40 landscape
    rotated = folder / "it's ünï" / "rot 'q'.jpg"
    rotated.parent.mkdir(parents=True)
    Image.new("RGB", (40, 90)).save(rotated, exif=exif)
    shape = AnnoLabel(str(rotated))
    long_label = "x" * 150
    first = shape.annotate("box", long_label, [(70, 5), (88, 30)], note=" keep spaces ")
    ids["rotated"] = first["annotation"]["object_id"]
    shape.annotate(
        "polygon", long_label, [(72, 6), (86, 6), (86, 29)], object_id=ids["rotated"], note='"q" é'
    )
    shape.annotate("polygon", "edge", [(0, 30), (10, 40), (0, 40)], note="touches corner")
    scene = AnnoLabel(str(image(folder / "scene.png")))
    scene.annotate("label", "shapes", [], note="Bookkeeping: recorded by Ana.")
    AnnoLabel(str(image(folder / "plain.png"))).annotate("box", "cell", [(5, 5), (20, 20)])
    image(folder / "bad.png")
    (folder / "bad.png.labels.json").write_text("{")
    return ids


def test_noted_items_queue_lists_literal_notes_with_focused_actions(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    ids = noted_folder(folder)
    before = hashes(folder)
    out = tmp_path / "audit"
    reply = audit_folder(str(folder), str(out))
    assert hashes(folder) == before
    assert sorted(p.name for p in out.rglob("*.png")) == ["page-001.png"]
    assert reply["noted_items"] == 4 and reply["notes"] == str(out / "notes.json")
    assert "notes.json" in reply["next"] and "not an uncertainty" in reply["next"]
    queue = json.loads((out / "notes.json").read_text())
    assert queue["count"] == 4 and "not an uncertainty" in queue["note"]
    report = json.loads((out / "audit.json").read_text())
    assert report["noted_items"] == 4 and report["notes"] == reply["notes"]
    items = {(i["image"], i["label"][:4]): i for i in queue["items"]}
    assert [i["item"] for i in queue["items"]] == ["note-001", "note-002", "note-003", "note-004"]

    # A linked box+polygon with one repeated note is one item with the note once.
    linked = items[("linked.png", "cell")]
    assert linked["object_id"] == ids["linked"] and linked["notes"] == ["blurred edge"]
    assert linked["geometry"] == ["box", "polygon"] and len(linked["annotation_ids"]) == 2
    assert linked["region"]["box"] == [10, 10, 60, 50]
    assert sum(i["image"] == "linked.png" for i in queue["items"]) == 1  # sibling/blank skipped

    # Distinct notes kept literally; a long label is cut in the key, never the item ID.
    rotated = items[("it's ünï/rot 'q'.jpg", "xxxx")]
    assert rotated["notes"] == [" keep spaces ", '"q" é'] and rotated["label"] == "x" * 150
    assert rotated["region"]["key"].startswith("note-") and len(rotated["region"]["key"]) == 100
    assert rotated["region"]["box"] == [70, 5, 88, 30]
    edge = items[("it's ünï/rot 'q'.jpg", "edge")]
    assert edge["geometry"] == ["polygon"] and edge["region"]["box"] == [0, 30, 10, 40]
    scene = items[("scene.png", "shap")]
    assert scene["kind"] == "classification" and scene["region"] is None
    assert scene["notes"] == ["Bookkeeping: recorded by Ana."] and "overview.png" in scene["open"]
    source = next(s for s in report["sources"] if s["image"] == "scene.png")
    assert scene["annotation_ids"] == [source["classifications"][0]["id"]]
    assert scene["annotation_revision"] and scene["image_sha256"]

    # Region files exist after publication and the quoted actions run as written.
    for item in (rotated, edge):
        region = json.loads(Path(item["region"]["file"]).read_text())
        assert region == [{"key": item["region"]["key"], "box": item["region"]["box"]}]
    for name, item in [("obj", edge), ("scene", scene)]:
        argv = shlex.split(item["review"].replace("NEW_REVIEW_DIR", str(tmp_path / name)))
        assert argv[2] == item["path"]
        result = cli(*argv[1:])
        assert len(result["review_pages"]) == (1 if name == "obj" else 0)
        assert (tmp_path / name / "overview.png").is_file()
    crop = json.loads((tmp_path / "obj" / "packet.json").read_text())["views"]
    assert any(v["source_rect"] == [0, 14, 26, 40] for v in crop), crop
    assert hashes(folder) == before


def test_no_notes_gives_empty_queue_and_old_reports_still_load(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    AnnoLabel(str(image(folder / "a.png"))).annotate("box", "cell", [(5, 5), (20, 20)])
    reply = audit_folder(str(folder), str(tmp_path / "audit"))
    assert reply["noted_items"] == 0 and "notes.json" not in reply["next"]
    queue = json.loads(Path(reply["notes"]).read_text())
    assert queue["count"] == 0 and queue["items"] == []
    assert list((tmp_path / "audit" / "notes").iterdir()) == []
    old = json.loads(Path(reply["report"]).read_text())
    del old["noted_items"], old["notes"]
    legacy = AuditReport.model_validate(old)
    assert legacy.noted_items == 0 and legacy.notes is None


def test_vocabulary_lists_every_excluded_object_once_and_ignores_scene_classes(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "my images"
    mixed(folder)
    before = hashes(folder)
    vocab = tmp_path / "cats.json"
    vocab.write_text('["rectangle", "outdoor"]')
    reply = audit_folder(str(folder), str(tmp_path / "audit"), categories=str(vocab))
    vocab.write_text('["changed"]')
    assert hashes(folder) == before
    report = AuditReport.model_validate_json(Path(reply["report"]).read_text())
    [miss] = report.vocabulary_mismatches
    assert (miss.image, miss.label, len(miss.annotation_ids)) == ("nested/a.png", "Rectangle", 2)
    assert miss.geometry == ["box", "polygon"] and reply["vocabulary_mismatches"] == 1
    snapshot = tmp_path / "audit" / "categories.json"
    assert reply["category_file"] == report.category_file == str(snapshot)
    assert json.loads(snapshot.read_text()) == ["rectangle", "outdoor"] == reply["categories"]
    assert shlex.split(miss.correct)[1:] == [
        "task",
        miss.path,
        "--categories",
        str(snapshot),
        "--geometry",
        "boxes",
        "--output",
        "NEW_TASK_DIR",
    ]
    assert reply["status"] == "attention" and "vocabulary_mismatches" in reply["next"]
    assert report.totals.objects == 2 and report.object_labels == {"Rectangle": 1, "rectangle": 1}
    assert report.totals.errors == 3 and report.noted_items == 2

    clean = tmp_path / "clean"
    AnnoLabel(str(image(clean / "a.png"))).annotate("box", "cell", [(5, 5), (20, 20)])
    AnnoLabel(str(image(clean / "b.png"))).annotate("label", "anything", [])
    vocab.write_text("[]")
    empty = audit_folder(str(clean), str(tmp_path / "empty"), categories=str(vocab))
    assert empty["status"] == "attention" and empty["vocabulary_mismatches"] == 1
    vocab.write_text('["cell"]')
    ok = audit_folder(str(clean), str(tmp_path / "ok"), categories=str(vocab))
    assert ok["status"] == "ready" and ok["vocabulary_mismatches"] == 0
    plain = audit_folder(str(clean), str(tmp_path / "plain"))
    assert "categories" not in plain and not (tmp_path / "plain" / "categories.json").exists()
    old = json.loads(Path(plain["report"]).read_text())
    assert old["categories"] is None and old["vocabulary_mismatches"] == []
    for key in ("categories", "category_file", "vocabulary_mismatches"):
        del old[key]
    assert AuditReport.model_validate(old).vocabulary_mismatches == []


@pytest.mark.parametrize("text", ["[", '{"a": 1}', '["a", "a"]', '[" "]', None])
def test_bad_vocabulary_fails_before_any_output(tmp_path: Path, text: str | None) -> None:
    folder = tmp_path / "images"
    AnnoLabel(str(image(folder / "a.png"))).annotate("box", "cell", [(5, 5), (20, 20)])
    vocab = tmp_path / "cats.json"
    if text is not None:
        vocab.write_text(text)
    before = hashes(folder)
    with pytest.raises(InputError) as error:
        audit_folder(str(folder), str(tmp_path / "out"), categories=str(vocab))
    assert error.value.recovery and "--categories" in str(error.value)
    assert not (tmp_path / "out").exists() and hashes(folder) == before
    assert not list(tmp_path.glob(".annolabel-audit-*"))
