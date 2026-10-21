"""Real CLI checks for the short task protocol and safe recovery."""

import json
import os
import shlex
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.task import Task, TaskError, start_task
from annolabel.modules.discovery import discover_images
from tests.helpers import cli, save

CONTEXT = {"guide", "submission", "next"}


def assert_lean_bundle(reply: dict) -> None:
    """A short checkpoint holds only its two source-sized PNGs, and every path it names exists."""
    bundle = Path(reply["task"]).parent
    assert sorted(p.name for p in bundle.glob("*.png")) == ["original.png", "view.png"]
    views = json.loads((bundle / "packet.json").read_text())["views"]
    assert [v["id"] for v in views] == ["original", "view"]
    assert all(Path(v["path"]).exists() for v in views)
    assert all(Path(reply[key]).exists() for key in ["task", "view", "guide", "submission"])


def submission() -> dict:
    return {
        "classifications": [{"label": "shapes"}],
        "objects": [
            {
                "key": "one",
                "label": "rectangle",
                "polygon": [[10, 10], [60, 10], [60, 50], [10, 50]],
            }
        ],
    }


def test_folder_start_creates_distinct_submittable_tasks(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    for name in ["a.png", "b.png", "sub/a.png", "a.jpg"]:
        image = folder / name
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 80), "white").save(image)
    (folder / "notes.txt").write_text("Not a source image")
    # Previously generated views and datasets must not become new sources.
    start_task(str(folder / "a.png"), str(folder / "previous-task"))
    dataset = folder / "previous-export"
    (dataset / "annotations").mkdir(parents=True)
    (dataset / "annotations/instances_default.json").write_text("{}")
    Image.new("RGB", (100, 80)).save(dataset / "export.png")
    (folder / "previous-audit").mkdir()
    (folder / "previous-audit/audit.json").write_text("{}")
    Image.new("RGB", (100, 80)).save(folder / "previous-audit/page-001.png")
    output = folder / "new-tasks"
    initial = cli("task", folder, "-o", output, "--max-passes", 1)
    assert initial["index"] == str(output / "tasks.json")
    index = json.loads(Path(initial["index"]).read_text())
    assert index["source"] == str(folder)
    assert initial["tasks"] == index["tasks"]
    tasks = initial["tasks"]
    assert {Path(t["image"]).relative_to(folder).as_posix() for t in tasks} == {
        "a.png",
        "b.png",
        "sub/a.png",
        "a.jpg",
    }
    assert len({t["task"] for t in tasks}) == 4
    assert "annolabel submit" in initial["next"]
    for item in tasks:
        assert item["passes_left"] == 1
        saved = Task(item["task"]).submit(json.dumps(submission()))
        assert saved["status"] == "saved" and saved["passes_left"] == 0
        assert saved["changes"] == {"added": ["one"], "changed": [], "removed": []}
        assert Task(item["task"]).status() == saved


@pytest.mark.parametrize("non_images", [False, True])
def test_folder_start_rejects_empty_and_used_output(tmp_path: Path, non_images: bool) -> None:
    folder = tmp_path / "images"
    folder.mkdir()
    if non_images:
        (folder / "notes.txt").write_text("No images here")
    output = tmp_path / "tasks"
    error = cli("task", folder, "-o", output, success=False)
    assert error["code"] == "NO_IMAGES" and str(folder) in error["error"]
    assert error["recovery"] and not output.exists()
    Image.new("RGB", (100, 80)).save(folder / "a.png")
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("Keep this file")
    error = cli("task", folder, "-o", output, success=False)
    assert error["code"] == "OUTPUT_EXISTS" and "--output" in error["recovery"]
    assert marker.read_text() == "Keep this file"


def test_folder_failure_cleans_up_and_forwards_rules(tmp_path: Path) -> None:
    folder = tmp_path / "images"
    folder.mkdir()
    Image.new("RGB", (100, 80)).save(folder / "a.png")
    bad = folder / "b.png"
    bad.write_text("broken image")
    output = tmp_path / "tasks"
    error = cli("task", folder, "-o", output, success=False)
    assert error["code"] == "IO_ERROR" and str(bad) in error["error"]
    assert not output.exists() and not list(folder.glob("*.labels.json"))
    bad.unlink()
    categories = save(tmp_path / "categories.json", ["rectangle"])
    instructions = tmp_path / "instructions.txt"
    instructions.write_text("Find rectangles")
    initial = cli(
        "task",
        folder,
        "-o",
        output,
        "--categories",
        categories,
        "--instructions",
        instructions,
        "--geometry",
        "polygons",
    )
    entry = initial["tasks"][0]
    packet = json.loads((Path(entry["task"]).parent / "packet.json").read_text())
    assert packet["rules"] == {
        "categories": ["rectangle"],
        "geometry": "polygons",
        "instructions": "Find rectangles",
        "boundary_policy": "visible",
    }
    bad_payload = {
        "classifications": [],
        "objects": [{"key": "one", "label": "circle", "box": [10, 10, 30, 30]}],
    }
    with pytest.raises(ValueError):
        Task(entry["task"]).submit(json.dumps(bad_payload))
    assert not (Path(entry["task"]).parent / "pass1").exists()
    assert Task(entry["task"]).submit(json.dumps(submission()))["status"] == "saved"


def test_folder_status_tracks_progress_corrections_and_export(source: Path, tmp_path: Path) -> None:
    from annolabel.core.task import task_status

    folder = tmp_path / "images"
    for name in ["a.png", "b.png", "sub/a.png"]:
        image = folder / name
        image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, image)
    initial = start_task(str(folder), str(tmp_path / "tasks"))
    before = task_status(initial["index"])
    assert before["totals"] == {"images": 3, "saved": 0, "pending": 3}
    assert [t["state"] for t in before["tasks"]] == ["pending"] * 3
    assert before["next_task"] == initial["tasks"][0]["task"]
    assert before["next_task"] in before["next"]
    assert all(t["objects"] == 0 and t["passes_left"] == 2 for t in before["tasks"])
    payload = json.dumps(submission())
    first = Task(before["next_task"]).submit(payload)
    during = task_status(initial["index"])
    assert during["totals"] == {"images": 3, "saved": 1, "pending": 2}
    assert during["next_task"] == initial["tasks"][1]["task"]
    assert during["tasks"][0] == {
        "image": initial["tasks"][0]["image"],
        "state": "saved",
        **{key: first[key] for key in ["task", "view", "objects", "passes_left"]},
    }
    changed = submission()
    changed["objects"][0]["label"] = "square"
    corrected = Task(first["task"]).submit(json.dumps(changed))
    after_correction = task_status(initial["index"])
    assert after_correction["tasks"][0]["task"] == corrected["task"]
    assert after_correction["tasks"][0]["passes_left"] == 0
    for item in after_correction["tasks"][1:]:
        Task(item["task"]).submit(payload)
    complete = task_status(initial["index"])
    assert complete["status"] == "complete"
    assert complete["totals"] == {"images": 3, "saved": 3, "pending": 0}
    assert complete["next_task"] is None
    assert complete["next"] == f"annolabel export {folder} --output NEW_DATASET"
    exported = cli("export", folder, "-o", tmp_path / "dataset")
    assert exported["images"] == exported["objects"] == 3


@pytest.mark.parametrize(
    "broken", ["missing", "missing_view", "invalid_receipt", "conflict", "wrong_image"]
)
def test_folder_status_isolates_broken_entries(source: Path, tmp_path: Path, broken: str) -> None:
    from annolabel.core.task import task_status

    folder = tmp_path / "images"
    folder.mkdir()
    for name in ["a.png", "b.png"]:
        shutil.copyfile(source, folder / name)
    initial = start_task(str(folder), str(tmp_path / "tasks"))
    first, second = initial["tasks"]
    if broken == "missing":
        shutil.rmtree(Path(first["task"]).parent)
    elif broken == "missing_view":
        Path(first["view"]).unlink()
    elif broken == "invalid_receipt":
        saved = Task(first["task"]).submit(json.dumps(submission()))
        (Path(saved["task"]).parent / "receipt.json").write_text("{}")
    elif broken == "conflict":
        AnnoLabel(first["image"]).annotate("label", "external edit", [])
    else:
        index = json.loads(Path(initial["index"]).read_text())
        index["tasks"][0]["image"] = second["image"]
        Path(initial["index"]).write_text(json.dumps(index))
        first["image"] = second["image"]
    status = task_status(initial["index"])
    assert status["status"] == "attention"
    assert status["totals"] == {"images": 2, "saved": 0, "pending": 2}
    bad, good = status["tasks"]
    assert bad["state"] == "error" and bad["image"] == first["image"]
    assert bad["error"] and bad["code"] and bad["recovery"]
    assert bad["objects"] is None and bad["passes_left"] is None
    assert good["state"] == "pending" and good["task"] == second["task"]
    assert status["next_task"] == second["task"]
    Task(second["task"]).submit(json.dumps(submission()))
    status = task_status(initial["index"])
    assert status["totals"] == {"images": 2, "saved": 1, "pending": 1}
    assert status["next_task"] is None and "task-status" in status["next"]
    assert "annolabel export" not in status["next"]


def test_short_task_two_passes_recovery_retry_and_export(source: Path, tmp_path: Path) -> None:
    initial = cli("task", source, "-o", tmp_path / "task")
    payload = save(tmp_path / "labels.json", submission())
    first = cli("submit", initial["task"], "--file", payload)
    assert first["objects"] == 1 and first["passes_left"] == 1
    assert len(json.dumps(first)) < 2000
    assert set(first) == {"status", "task", "view", "objects", "passes_left", "changes"} | CONTEXT
    stored = json.loads((Path(first["task"]).parent / "receipt.json").read_text())["result"]
    assert set(stored) == {"status", "task", "view", "objects", "passes_left", "changes"}
    assert first["changes"] == {"added": ["one"], "changed": [], "removed": []}
    assert cli("submit", initial["task"], "--file", payload) == first  # lost-response retry
    assert cli("task-status", initial["task"]) == first
    assert not list(Path(first["view"]).parent.glob("review-*.png"))
    before = cli("info", source)["annotations"]
    changed = submission()
    changed["objects"][0]["label"] = "square"
    changed["objects"][0]["polygon"] = [[20, 20], [40, 20], [40, 40], [20, 40]]
    correction = save(tmp_path / "correction.json", changed)
    used = cli("submit", initial["task"], "--file", correction, success=False)
    assert used["code"] == "CHECKPOINT_USED" and "task-status" in used["recovery"]
    second = cli("submit", first["task"], "--file", correction)
    assert second["passes_left"] == 0
    assert second["changes"] == {"added": [], "changed": ["one"], "removed": []}
    assert cli("task-status", initial["task"]) == second
    assert {a["id"] for a in before} == {a["id"] for a in cli("info", source)["annotations"]}
    assert (
        cli("submit", second["task"], "--file", correction, success=False)["code"] == "PASS_LIMIT"
    )
    assert cli("export", source, "-o", tmp_path / "coco")["objects"] == 1
    # Source, initial view and both reviews share the exact same pixel origin and extent.
    with (
        Image.open(source) as original,
        Image.open(initial["view"]) as a,
        Image.open(first["view"]) as b,
        Image.open(second["view"]) as c,
    ):
        assert a.size == b.size == c.size == original.size
        assert a.tobytes() == original.convert("RGB").tobytes()
        assert b.getpixel((10, 10)) != a.getpixel((10, 10))  # first polygon starts here
        assert c.getpixel((20, 20)) != a.getpixel((20, 20))  # corrected polygon starts here
        assert c.getpixel((10, 10)) == a.getpixel((10, 10))  # old outline is removed
    packets = [
        json.loads((Path(v["view"]).parent / "packet.json").read_text())
        for v in [initial, first, second]
    ]
    for reply in [initial, first, second]:
        assert_lean_bundle(reply)
    for packet in packets:
        view = next(v for v in packet["views"] if v["id"] == "view")
        assert (
            view["source_rect"]
            == view["content_rect"]
            == [0, 0, initial["width"], initial["height"]]
        )


def test_short_view_exif_orientation_and_edge_coordinates(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 60), "white")
    image.paste("black", (0, 0, 50, 30))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    initial = cli("task", source, "-o", tmp_path / "task")
    with Image.open(source) as raw, Image.open(initial["view"]) as view:
        oriented = ImageOps.exif_transpose(raw).convert("RGB")
        assert view.size == (60, 100) == (initial["width"], initial["height"])
        assert view.tobytes() == oriented.tobytes()
    # Explicit view coordinates are also identity coordinates, right up to the outer edges.
    payload = {
        "classifications": [],
        "objects": [
            {
                "key": "edge",
                "label": "region",
                "view_id": "view",
                "polygon": [[0, 0], [60, 0], [60, 100], [0, 100]],
            }
        ],
    }
    saved = cli("submit", initial["task"], "--file", save(tmp_path / "edge.json", payload))
    annotations = cli("info", source)["annotations"]
    assert (
        next(a for a in annotations if a["kind"] == "polygon")["points"]
        == payload["objects"][0]["polygon"]
    )
    with Image.open(saved["view"]) as view:
        assert view.size == (60, 100)
        assert view.getpixel((0, 50)) != oriented.getpixel((0, 50))
    assert_lean_bundle(initial)
    assert_lean_bundle(saved)


def test_invalid_submission_and_external_edit_leave_task_unchanged(
    source: Path, tmp_path: Path
) -> None:
    initial = cli("task", source, "-o", tmp_path / "task")
    bad = submission()
    bad["objects"][0]["polygon"][0] = [-1, 10]
    error = cli(
        "submit", initial["task"], "--file", save(tmp_path / "bad.json", bad), success=False
    )
    assert error["code"] == "VALIDATION_ERROR" and error["recovery"]
    assert not (tmp_path / "task/pass1").exists()
    assert not Path(str(source) + ".labels.json").exists()
    cli("label", source, "--label", "external edit")
    before = Path(str(source) + ".labels.json").read_bytes()
    payload = save(tmp_path / "good.json", submission())
    assert (
        cli("submit", initial["task"], "--file", payload, success=False)["code"] == "TASK_CONFLICT"
    )
    assert cli("task-status", initial["task"], success=False)["code"] == "TASK_CONFLICT"
    assert Path(str(source) + ".labels.json").read_bytes() == before


def test_one_pass_task_and_duplicate_object_keys(source: Path, tmp_path: Path) -> None:
    initial = cli("task", source, "--max-passes", 1, "-o", tmp_path / "task")
    bad = submission()
    bad["objects"].append(bad["objects"][0].copy())
    assert (
        cli("submit", initial["task"], "--file", save(tmp_path / "bad.json", bad), success=False)[
            "code"
        ]
        == "VALIDATION_ERROR"
    )
    payload = save(tmp_path / "good.json", submission())
    result = cli("submit", initial["task"], "--file", payload)
    assert result["passes_left"] == 0
    assert cli("submit", result["task"], "--file", payload, success=False)["code"] == "PASS_LIMIT"


def test_submission_reports_each_invalid_object_before_saving(source: Path, tmp_path: Path) -> None:
    categories = save(tmp_path / "categories.json", ["rectangle", "triangle"])
    initial = cli("task", source, "-o", tmp_path / "task", "--categories", categories)
    bad = {
        "classifications": [],
        "objects": [
            {"key": "bad1", "label": "circle", "polygon": [[10, 10], [30, 10], [20, 30]]},
            {"key": "bad2", "label": "rectangle", "box": [9, 9, 1, 1]},
            {"key": "bad3", "label": "triangle", "polygon": [[1, 1], [2, 2]]},
        ],
    }
    error = cli("submit", initial["task"], "--file", "-", stdin=json.dumps(bad), success=False)
    assert error["code"] == "VALIDATION_ERROR"
    details = error["details"]
    for index, (key, message) in enumerate(
        [
            ("bad1", "allowed categories"),
            ("bad2", "x1 < x2"),
            ("bad3", "three distinct points"),
        ]
    ):
        assert any(
            f"objects[{index}] ({key})" in detail and message in detail for detail in details
        )
    assert all(
        noise not in json.dumps(error) for noise in ["Value error,", "input_value", "pydantic"]
    )
    assert not (tmp_path / "task/pass1").exists()
    assert not Path(str(source) + ".labels.json").exists()
    # The same handle still accepts a corrected complete snapshot.
    saved = cli("submit", initial["task"], "--file", "-", stdin=json.dumps(submission()))
    assert saved["objects"] == 1 and saved["passes_left"] == 1
    assert set(saved) == {"status", "task", "view", "objects", "passes_left", "changes"} | CONTEXT


def test_changes_use_saved_geometry_and_report_removed_keys(source: Path, tmp_path: Path) -> None:
    initial = start_task(str(source), str(tmp_path / "task"), geometry="boxes")
    objects = [
        {"key": key, "label": "square", "box": [10, 10, 30, 30]}
        for key in ["label", "box", "note", "drop"]
    ]
    objects.append({"key": "keep", "label": "triangle", "polygon": [[0, 0], [10, 0], [5, 10]]})
    first = Task(initial["task"]).submit(json.dumps({"classifications": [], "objects": objects}))
    assert first["changes"] == {
        "added": ["label", "box", "note", "drop", "keep"],
        "changed": [],
        "removed": [],
    }
    objects[0]["label"] = "rectangle"
    objects[1]["box"] = [10, 10, 40, 40]
    objects[2]["note"] = "uncertain"
    objects.pop(3)
    # A derived box and explicit identity view do not change the polygon's geometry.
    objects[-1]["view_id"] = "view"
    objects.append({"key": "new", "label": "square", "box": [40, 40, 50, 50]})
    payload = json.dumps({"classifications": [], "objects": objects})
    second = Task(first["task"]).submit(payload)
    assert second["changes"] == {
        "added": ["new"],
        "changed": ["label", "box", "note"],
        "removed": ["drop"],
    }
    assert "drop" in second["warnings"][0]
    assert Task(first["task"]).submit(payload) == second
    assert Task(initial["task"]).status() == second


def test_empty_correction_requires_opt_in_without_writing_or_using_pass(
    source: Path, tmp_path: Path
) -> None:
    initial = start_task(str(source), str(tmp_path / "task"))
    first = Task(initial["task"]).submit(json.dumps(submission()))
    empty = json.dumps({"classifications": [{"label": "empty scene"}], "objects": []})
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(TaskError) as caught:
        Task(first["task"]).submit(empty)
    assert caught.value.code == "OBJECTS_REMOVED"
    assert "--allow-empty" in caught.value.recovery and "one" in caught.value.recovery
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
    assert Task(initial["task"]).status() == first
    second = Task(first["task"]).submit(empty, allow_empty=True)
    assert second["objects"] == 0 and second["passes_left"] == 0
    assert second["changes"] == {"added": [], "changed": [], "removed": ["one"]}
    assert "one" in second["warnings"][0]
    assert Task(first["task"]).submit(empty) == second  # replay precedes the opt-in guard
    assert Task(first["task"]).submit(empty, allow_empty=True) == second
    assert Task(initial["task"]).status() == second


def test_empty_first_save_allowed_only_when_previous_snapshot_has_no_objects(
    source: Path, tmp_path: Path
) -> None:
    empty = json.dumps({"classifications": [], "objects": []})
    initial = start_task(str(source), str(tmp_path / "empty-task"))
    saved = Task(initial["task"]).submit(empty)
    assert saved["objects"] == 0 and saved["passes_left"] == 1
    assert saved["changes"] == {"added": [], "changed": [], "removed": []}
    assert "warnings" not in saved
    assert Task(initial["task"]).submit(empty) == saved
    kit = AnnoLabel(str(source))
    annotation = kit.annotate("box", "square", [(10, 10), (30, 30)])
    existing = start_task(str(source), str(tmp_path / "existing-task"))
    with pytest.raises(TaskError) as caught:
        Task(existing["task"]).submit(empty)
    assert caught.value.code == "OBJECTS_REMOVED"
    assert annotation["annotation"]["object_id"] in caught.value.recovery
    assert not (tmp_path / "existing-task/pass1").exists()


def test_working_copy_handoff_retains_state_and_isolates_edits(
    source: Path, tmp_path: Path
) -> None:
    seed = submission()
    seed["objects"][0]["note"] = "faint edge"
    square = [[70, 5], [90, 5], [90, 25], [70, 25]]
    seed["objects"].append({"key": "two", "label": "dot", "polygon": square})
    Task(start_task(str(source), str(tmp_path / "seed"))["task"]).submit(json.dumps(seed))
    rules = save(tmp_path / "rules.txt", "Outline every shape, even faint ones.")
    labels = save(tmp_path / "labels.json", ["rectangle", "dot", 'say "hi", ok'])
    initial = cli(
        "task", source, "-o", tmp_path / "task", "--instructions", rules, "--categories", labels
    )
    guide = Path(initial["guide"]).read_text()
    assert "Outline every shape, even faint ones." in guide
    assert '["rectangle", "dot", "say \\"hi\\", ok"]' in guide
    assert "Scene classifications are whole-image labels" in guide
    assert "Geometry: both. Each object needs a polygon" in guide
    assert "UNSAVED" in guide and "(0,0)" in guide
    schema = json.loads((Path(initial["task"]).parent / "schema.json").read_text())
    assert set(schema["properties"]) == {"classifications", "objects"}
    working = Path(initial["submission"])
    assert initial["next"].endswith(f"--file {working}.") and "Read" in initial["next"]
    copy = json.loads(working.read_text())
    assert copy == {
        "classifications": [{"label": "shapes", "note": None}],
        "objects": [
            {**seed["objects"][0], "box": [10, 10, 60, 50]},
            {**seed["objects"][1], "box": [70, 5, 90, 25], "note": None},
        ],
    }
    internal = Path(initial["task"]).parent / "annotations.json"
    frozen = internal.read_bytes()
    copy["objects"][1]["box"] = [70, 5, 95, 30]
    copy["objects"][1]["polygon"] = [[70, 5], [95, 5], [95, 30], [70, 30]]
    working.write_text(json.dumps(copy))
    edited = working.read_bytes()
    # Status is read-only: it reports saved counts and leaves the unsaved edit alone.
    assert cli("task-status", initial["task"]) == {
        **{key: initial[key] for key in ["status", "task", "view", "passes_left"]},
        "objects": 2,
        **{key: initial[key] for key in CONTEXT},
    }
    assert internal.read_bytes() == frozen and working.read_bytes() == edited
    first = cli("submit", initial["task"], "--file", working)
    assert first["changes"] == {"added": [], "changed": ["two"], "removed": []}
    assert internal.read_bytes() == frozen and working.read_bytes() == edited
    assert cli("task-status", initial["task"]) == first  # lost response
    assert cli("submit", initial["task"], "--file", working) == first  # exact replay
    assert "If a correction is needed" in first["next"] and "export" in first["next"]
    saved = {a["object_id"]: a for a in cli("info", source)["annotations"] if a["kind"] == "box"}
    assert saved["one"]["note"] == "faint edge" and saved["two"]["points"] == [[70, 5], [95, 30]]
    assert any(a["label"] == "shapes" for a in cli("info", source)["annotations"])
    assert json.loads(Path(first["submission"]).read_text())["objects"][1]["box"] == [70, 5, 95, 30]
    last = cli("submit", first["task"], "--file", first["submission"])
    assert last["passes_left"] == 0 and last["changes"]["changed"] == []
    assert "annolabel submit" not in last["next"] and "stop labeling" in last["next"]
    assert last["next"].endswith(f"annolabel export '{source}' --output NEW_DATASET.")


@pytest.mark.parametrize(
    ("categories", "geometry", "expected"),
    [
        (None, "boxes", ["any label (unrestricted)", "a box [x1,y1,x2,y2] or a polygon"]),
        ([], "polygons", ["none; this task allows no objects", "a missing box is derived"]),
    ],
)
def test_guide_states_label_and_geometry_rules(
    source: Path, tmp_path: Path, categories: list | None, geometry: str, expected: list
) -> None:
    labels = None if categories is None else str(save(tmp_path / "labels.json", categories))
    task = start_task(str(source), str(tmp_path / "task"), categories=labels, geometry=geometry)
    guide = Path(task["guide"]).read_text()
    assert all(text in guide for text in expected) and f"Geometry: {geometry}." in guide


def test_legacy_bundle_without_working_copy_keeps_old_replies(source: Path, tmp_path: Path) -> None:
    initial = start_task(str(source), str(tmp_path / "task"))
    Path(initial["submission"]).unlink()  # bundles written before working copies existed
    core = {"status", "task", "view", "objects", "passes_left"}
    assert set(Task(initial["task"]).status()) == core
    first = Task(initial["task"]).submit(json.dumps(submission()))
    assert set(first) == core | {"changes"} | CONTEXT
    assert set(Task(initial["task"]).submit(json.dumps(submission()))) == set(first)


def test_legacy_packet_panels_still_convert_and_drive_detailed_review(
    source: Path, tmp_path: Path
) -> None:
    initial = start_task(str(source), str(tmp_path / "task"))
    bundle = Path(initial["task"]).parent
    # Bundles written before short tasks dropped it also listed a padded grid panel.
    old = json.loads(Path(cli("prepare", source, "-o", tmp_path / "old")["packet"]).read_text())
    grid = next(v for v in old["views"] if v["id"] == "grid")
    packet = json.loads((bundle / "packet.json").read_text())
    packet["views"] = [v for v in packet["views"] if v["id"] != "grid"] + [grid]
    (bundle / "packet.json").write_text(json.dumps(packet))
    x, y = grid["content_rect"][:2]
    payload = submission()
    payload["objects"][0]["view_id"] = "grid"
    payload["objects"][0]["polygon"] = [
        [px + x, py + y] for px, py in submission()["objects"][0]["polygon"]
    ]
    first = Task(initial["task"]).submit(json.dumps(payload))
    saved = json.loads((Path(first["task"]).parent / "annotations.json").read_text())
    assert saved["objects"][0]["polygon"] == submission()["objects"][0]["polygon"]
    review = cli("review", source, "--packet", bundle / "packet.json", "-o", tmp_path / "review")
    assert review["rules"] == packet["rules"]
    assert Path(review["overview"]).exists() and all(
        map(Path.exists, map(Path, review["review_pages"]))
    )


def test_folder_next_leads_to_pending_image_context(source: Path, tmp_path: Path) -> None:
    from annolabel.core.task import task_status

    folder = tmp_path / "images"
    folder.mkdir()
    for name in ["a.png", "b.png"]:
        shutil.copyfile(source, folder / name)
    initial = start_task(str(folder), str(tmp_path / "tasks"))
    assert set(initial["tasks"][0]) == {"image", "task", "view", "width", "height", "passes_left"}
    first = Task(initial["tasks"][0]["task"]).status()
    assert first["next"] in initial["next"] and initial["index"] in initial["next"]
    Task(initial["tasks"][0]["task"]).submit(json.dumps(submission()))
    status = task_status(initial["index"])
    second = Task(initial["tasks"][1]["task"]).status()
    assert second["submission"] in status["next"] and second["next"] in status["next"]
    assert "submission" not in status["tasks"][1]


def tree(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file()
    }


def mixed_folder(source: Path, tmp_path: Path) -> Path:
    folder = tmp_path / "café's images"
    (folder / "nested").mkdir(parents=True)
    shutil.copyfile(source, folder / "a.png")
    shutil.copyfile(source, folder / "nested/a.png")
    (folder / "b.png").write_text("broken image")
    return folder


def test_keep_going_folder_labels_healthy_images_and_retries_fixed_source(
    source: Path, tmp_path: Path
) -> None:
    folder = mixed_folder(source, tmp_path)
    instructions = tmp_path / "rules.txt"
    instructions.write_text("Find rectangles")
    categories = save(tmp_path / "categories.json", ["rectangle"])
    rules = ["--instructions", instructions, "--categories", categories, "--max-passes", 1]
    error = cli("task", folder, "-o", tmp_path / "atomic", *rules, success=False)
    assert error["code"] == "IO_ERROR" and str(folder / "b.png") in error["error"]
    assert "--keep-going" in error["recovery"] and not (tmp_path / "atomic").exists()

    output = tmp_path / "tasks"
    initial = cli("task", folder, "-o", output, *rules, "--keep-going")
    index = Path(initial["index"])
    assert initial["status"] == "attention" and initial["images"] == 3
    a, b, nested = initial["tasks"]
    assert b["image"] == str(folder / "b.png") and b["code"] == "IO_ERROR"
    assert "task-retry" in b["recovery"] and "view" not in b
    assert initial["next_task"] == a["task"] and "task-retry" in initial["next"]
    before = tree(output)
    status = cli("task-status", index)
    assert tree(output) == before
    assert status["status"] == "attention" and status["totals"]["images"] == 3
    assert [t["state"] for t in status["tasks"]] == ["pending", "error", "pending"]
    assert "annolabel submit" in status["next"] and "task-retry" in status["next"]

    for item in [a, nested]:
        cli("submit", item["task"], "--file", save(tmp_path / "s.json", submission()))
    status = cli("task-status", index)
    assert status["status"] == "attention" and status["totals"]["saved"] == 2
    assert status["next"].startswith("1 source(s) could not start")
    assert "export" not in status["next"]
    retry = cli("task-retry", index)
    assert retry["retried"] == [] and retry["next"].startswith("task-retry started nothing")
    assert retry["tasks"][1]["state"] == "error"

    healthy = {name: tree(output / name) for name in ["000001", "000003"]}
    sidecars = {p: p.read_bytes() for p in folder.rglob("*.labels.json")}
    index_bytes = index.read_bytes()
    shutil.copyfile(source, folder / "b.png")
    instructions.write_text("Changed rules")
    categories.unlink()
    shutil.copyfile(source, folder / "late.png")
    retry = cli("task-retry", index)
    assert retry["retried"] == [str(folder / "b.png")] and retry["status"] == "ready"
    assert retry["next_task"] == str(output / "000002/task.json")
    assert {name: tree(output / name) for name in healthy} == healthy
    assert {p: p.read_bytes() for p in sidecars} == sidecars and index.read_bytes() == index_bytes
    assert len(retry["tasks"]) == 3
    packet = json.loads((output / "000002/packet.json").read_text())
    assert packet["rules"] == json.loads((output / "000001/packet.json").read_text())["rules"]
    assert packet["rules"]["instructions"] == "Find rectangles"
    assert Task(retry["next_task"]).status()["passes_left"] == 1
    before = tree(output)
    assert cli("task-retry", index)["retried"] == [] and tree(output) == before

    cli("submit", retry["next_task"], "--file", save(tmp_path / "s.json", submission()))
    status = cli("task-status", index)
    assert status["status"] == "complete" and status["next"].startswith("annolabel export")
    exported = cli("export", folder, "-o", tmp_path / "dataset")
    assert exported["images"] == 3 and exported["objects"] == 3


@pytest.mark.parametrize("saved", [True, False])
def test_retry_never_regenerates_a_started_slot(source: Path, tmp_path: Path, saved: bool) -> None:
    from annolabel.core.task import retry_tasks, task_status

    folder = mixed_folder(source, tmp_path)
    initial = start_task(str(folder), str(tmp_path / "tasks"), keep_going=True)
    shutil.copyfile(source, folder / "b.png")
    task = retry_tasks(initial["index"])["tasks"][1]["task"]
    if saved:
        corrected = Task(task).submit(json.dumps(submission()))
        Task(corrected["task"]).submit(json.dumps({**submission(), "classifications": []}))
    shutil.rmtree(Path(task).parent)
    retry = retry_tasks(initial["index"])
    assert retry["retried"] == [] and not Path(task).parent.exists()
    entry = task_status(initial["index"])["tasks"][1]
    assert entry["state"] == "error" and entry["code"] == "TASK_MISSING"
    assert entry["task"] == task and "not regenerated" in entry["recovery"]


@pytest.mark.parametrize("occupant", ["directory", "symlink"])
def test_retry_does_not_overwrite_an_occupied_slot(
    source: Path, tmp_path: Path, occupant: str
) -> None:
    from annolabel.core.task import retry_tasks

    folder = mixed_folder(source, tmp_path)
    initial = start_task(str(folder), str(tmp_path / "tasks"), keep_going=True)
    shutil.copyfile(source, folder / "b.png")
    slot = tmp_path / "tasks/000002"
    if occupant == "directory":
        slot.mkdir()
        (slot / "keep.txt").write_text("keep")
    else:
        slot.symlink_to(tmp_path / "tasks/000001", target_is_directory=True)
    before = tree(tmp_path / "tasks")
    retry = retry_tasks(initial["index"])
    assert retry["retried"] == [] and retry["tasks"][1]["state"] == "error"
    assert tree(tmp_path / "tasks") == before


def test_retry_publication_failure_keeps_earlier_retried_slots(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from annolabel.core import task as task_module

    folder = tmp_path / "images"
    folder.mkdir()
    for name in ["a.png", "b.png"]:
        (folder / name).write_text("broken")
    initial = start_task(str(folder), str(tmp_path / "tasks"), categories=None, keep_going=True)
    assert initial["status"] == "attention" and initial["next_task"] is None
    assert initial["next"].startswith("2 source(s) could not start")
    for name in ["a.png", "b.png"]:
        shutil.copyfile(source, folder / name)
    publish, calls = task_module._publish, []

    def flaky(*args: object) -> dict:
        calls.append(args)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return publish(*args)

    monkeypatch.setattr(task_module, "_publish", flaky)
    with pytest.raises(OSError):
        task_module.retry_tasks(initial["index"])
    first = tree(tmp_path / "tasks/000001")
    assert not (tmp_path / "tasks/000002").exists()
    assert not (tmp_path / "tasks/retries/000002.json").exists()
    monkeypatch.setattr(task_module, "_publish", publish)
    assert task_module.retry_tasks(initial["index"])["retried"] == [str(folder / "b.png")]
    assert tree(tmp_path / "tasks/000001") == first


def test_start_publication_failure_cleans_up(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from annolabel.core import task as task_module

    folder = mixed_folder(source, tmp_path)

    def fail(*args: object) -> dict:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(task_module, "_publish", fail)
    with pytest.raises(OSError):
        start_task(str(folder), str(tmp_path / "tasks"), keep_going=True)
    assert not (tmp_path / "tasks").exists()


def test_retry_freezes_empty_category_list(source: Path, tmp_path: Path) -> None:
    from annolabel.core.task import retry_tasks

    folder = mixed_folder(source, tmp_path)
    categories = save(tmp_path / "categories.json", [])
    initial = start_task(
        str(folder), str(tmp_path / "tasks"), categories=str(categories), keep_going=True
    )
    categories.unlink()
    shutil.copyfile(source, folder / "b.png")
    retry_tasks(initial["index"])
    packet = json.loads((tmp_path / "tasks/000002/packet.json").read_text())
    assert packet["rules"]["categories"] == []


def test_shared_inputs_and_index_errors_are_actionable(source: Path, tmp_path: Path) -> None:
    folder = mixed_folder(source, tmp_path)
    error = cli(
        "task",
        folder,
        "-o",
        tmp_path / "t",
        "--keep-going",
        "--categories",
        tmp_path / "no.json",
        success=False,
    )
    assert "--categories" in error["error"] and not (tmp_path / "t").exists()
    error = cli("task", source, "-o", tmp_path / "t", "--keep-going", success=False)
    assert "folders" in error["error"] and not (tmp_path / "t").exists()
    single = cli("task", source, "-o", tmp_path / "single")
    error = cli("task-retry", single["task"], success=False)
    assert error["code"] == "FOLDER_INDEX_REQUIRED"

    initial = start_task(str(folder), str(tmp_path / "tasks"), keep_going=True)
    index = Path(initial["index"])
    data = json.loads(index.read_text())
    # A legacy index has no frozen rules; with only started entries retry is a no-op.
    legacy = {k: v for k, v in data.items() if k not in {"rules", "max_passes"}}
    save(index, {**legacy, "tasks": [data["tasks"][0]]})
    retry = cli("task-retry", index)
    assert retry["retried"] == [] and retry["status"] == "ready"
    for broken in [legacy, {**data, "tasks": [{**data["tasks"][1], "code": None}]}]:
        save(index, broken)
        for command in ["task-retry", "task-status"]:
            error = cli(command, index, success=False)
            assert error["code"] == "VALIDATION_ERROR" and "tasks.json" in error["recovery"]


def test_retry_interrupted_after_publication_keeps_its_history(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from annolabel.core import task as task_module

    folder = mixed_folder(source, tmp_path)
    initial = start_task(str(folder), str(tmp_path / "tasks"), keep_going=True)
    shutil.copyfile(source, folder / "b.png")
    publish = task_module._publish

    def interrupted(*args: object) -> dict:
        publish(*args)
        raise KeyboardInterrupt

    monkeypatch.setattr(task_module, "_publish", interrupted)
    with pytest.raises(KeyboardInterrupt):
        task_module.retry_tasks(initial["index"])
    monkeypatch.setattr(task_module, "_publish", publish)
    slot = tmp_path / "tasks/000002"
    assert (tmp_path / "tasks/retries/000002.json").exists()
    saved = Task(str(slot / "task.json")).submit(json.dumps(submission()))
    Task(saved["task"]).submit(json.dumps({**submission(), "classifications": []}))
    shutil.move(slot, tmp_path / "backup")
    retry = task_module.retry_tasks(initial["index"])
    assert retry["retried"] == [] and not slot.exists()
    assert retry["tasks"][1]["code"] == "TASK_MISSING"


def _tree(root: Path) -> dict:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _run(command: str) -> dict:
    """Run a returned 'annolabel ...' command through the real CLI."""
    words = shlex.split(command)
    assert words[0] == "annolabel"
    return cli(*words[1:])


def _two_saved_objects(source: Path, tmp_path: Path) -> dict:
    """Save a box and a polygon-only object, leaving one correction pass."""
    initial = cli("task", source, "-o", tmp_path / "task")
    payload = {
        "classifications": [{"label": "shapes", "note": "keep me"}],
        "objects": [
            {"key": "rect", "label": "rectangle", "polygon": [[5, 5], [40, 5], [40, 40], [5, 40]]},
            {"key": "tri", "label": "triangle", "polygon": [[50, 10], [90, 10], [70, 60]]},
        ],
    }
    return cli("submit", initial["task"], "--file", save(tmp_path / "first.json", payload))


def test_preview_final_pass_is_read_only_and_offered_submit_saves_it(
    source: Path, tmp_path: Path
) -> None:
    saved = _two_saved_objects(source, tmp_path)
    assert saved["passes_left"] == 1
    working = json.loads(Path(saved["submission"]).read_text())
    working["objects"] = [o for o in working["objects"] if o["key"] == "tri"]
    working["objects"][0]["polygon"] = [[52, 12], [88, 12], [70, 58]]
    working["objects"][0].pop("box")
    candidate = save(tmp_path / "candidate.json", working)
    before, status = _tree(tmp_path / "task"), cli("task-status", saved["task"])
    sidecar = Path(str(source) + ".labels.json").read_bytes()
    first = cli("preview", saved["task"], "--file", candidate, "-o", tmp_path / "previews/one")
    assert first["status"] == "unsaved" and first["passes_left"] == 1
    assert first["changes"] == {"added": [], "changed": ["tri"], "removed": ["rect"]}
    assert "rect" in first["warnings"][0]
    assert first["saved_view"] == saved["view"] and first["task"] == saved["task"]
    # Repeated previews (stdin, spaces in the path) never save, use a pass or create a handle.
    working["objects"][0]["polygon"] = [[54, 12], [86, 12], [70, 56]]
    second = cli(
        "preview",
        saved["task"],
        "--file",
        "-",
        "-o",
        tmp_path / "previews/two with space",
        stdin=json.dumps(working),
    )
    assert _tree(tmp_path / "task") == before
    assert cli("task-status", saved["task"]) == status
    assert Path(str(source) + ".labels.json").read_bytes() == sidecar
    assert not (Path(saved["task"]).parent / "pass2").exists()
    bundle = Path(second["candidate"]).parent
    assert sorted(p.name for p in bundle.iterdir()) == [
        "candidate.json",
        "changes.png",
        "guide.txt",
        "preview.json",
        "view.png",
    ]
    assert json.loads((bundle / "preview.json").read_text()) == second
    assert not os.access(second["candidate"], os.W_OK)
    for key in ["view", "changes_view", "saved_view", "candidate", "guide", "task"]:
        assert Path(second[key]).exists()
    guide = Path(second["guide"]).read_text()
    assert "UNSAVED" in guide and "DIAGNOSTIC ONLY" in guide and second["submit"] in guide
    with Image.open(second["changes_view"]) as changes, Image.open(second["view"]) as view:
        assert view.size == (100, 80) and changes.width == 100 and changes.height > 80
        assert changes.crop((0, 0, 100, 80)).tobytes() != view.tobytes()
    assert second["submit"] == (
        f"annolabel submit {shlex.quote(saved['task'])} --file {shlex.quote(second['candidate'])}"
    )
    final = _run(second["submit"])
    assert final["changes"] == second["changes"] and final["passes_left"] == 0
    assert Path(final["view"]).read_bytes() == Path(second["view"]).read_bytes()
    stored = json.loads(Path(final["submission"]).read_text())
    assert stored == json.loads(Path(second["candidate"]).read_text())
    assert stored["classifications"] == [{"label": "shapes", "note": "keep me"}]
    assert stored["objects"][0]["box"] == [54, 12, 86, 56]  # derived from the polygon
    # The used checkpoint and the terminal handle refuse previews, even of the same payload.
    used = cli("preview", saved["task"], "--file", candidate, "-o", tmp_path / "u", success=False)
    assert used["code"] == "CHECKPOINT_USED" and "task-status" in used["recovery"]
    done = cli("preview", final["task"], "--file", candidate, "-o", tmp_path / "d", success=False)
    assert done["code"] == "PASS_LIMIT"
    assert not (tmp_path / "u").exists() and not (tmp_path / "d").exists()


def test_preview_refusals_leave_everything_unchanged(source: Path, tmp_path: Path) -> None:
    saved = _two_saved_objects(source, tmp_path)
    task = Path(saved["task"])
    good = save(tmp_path / "good.json", json.loads(Path(saved["submission"]).read_text()))
    empty = save(tmp_path / "empty.json", {"classifications": [], "objects": []})
    bad = save(
        tmp_path / "bad.json",
        {"classifications": [], "objects": [{"key": "a", "label": "x", "polygon": [[1, 1]]}]},
    )
    (tmp_path / "alias").symlink_to(task.parent)
    (tmp_path / "taken").mkdir()
    before = _tree(tmp_path)
    cases = [
        (bad, tmp_path / "p", "VALIDATION_ERROR"),
        (empty, tmp_path / "p", "OBJECTS_REMOVED"),
        (good, task.parent / "pass2", "OUTPUT_RESERVED"),
        (good, task.parent / "pass2/nested", "OUTPUT_RESERVED"),
        (good, tmp_path / "alias/pass2/nested", "OUTPUT_RESERVED"),
        (good, tmp_path / "task/elsewhere", "OUTPUT_RESERVED"),
        (good, tmp_path / "taken", "OUTPUT_EXISTS"),
    ]
    for payload, output, code in cases:
        error = cli("preview", task, "--file", payload, "-o", output, success=False)
        assert error["code"] == code and error["recovery"], (output, error)
        assert _tree(tmp_path) == before
        assert not (task.parent / "pass2").exists() and not (tmp_path / "p").exists()
    assert (
        "preview"
        in cli("preview", task, "--file", empty, "-o", tmp_path / "p", success=False)["recovery"]
    )
    removal = cli("preview", task, "--file", empty, "-o", tmp_path / "p", "--allow-empty")
    assert removal["submit"].endswith(" --allow-empty") and removal["objects"] == 0
    assert removal["changes"]["removed"] == ["rect", "tri"]
    inside = cli("preview", task, "--file", good, "-o", tmp_path / "p/again", success=False)
    assert inside["code"] == "OUTPUT_RESERVED"
    # A source change after a successful preview still stops the offered submit and later previews.
    ok = cli("preview", task, "--file", good, "-o", tmp_path / "ok")
    cli("label", source, "--label", "external edit")
    assert cli(*shlex.split(ok["submit"])[1:], success=False)["code"] == "TASK_CONFLICT"
    stale = cli("preview", task, "--file", good, "-o", tmp_path / "stale", success=False)
    assert stale["code"] == "TASK_CONFLICT" and not (tmp_path / "stale").exists()


def test_preview_unchanged_exif_candidate_has_no_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (100, 60), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    initial = cli("task", source, "-o", tmp_path / "task")
    payload = {
        "classifications": [],
        "objects": [{"key": "p", "label": "part", "polygon": [[5, 5], [55, 5], [30, 95]]}],
    }
    saved = cli("submit", initial["task"], "--file", save(tmp_path / "a.json", payload))
    payload["objects"][0]["view_id"] = "view"  # identity coordinates on the source-sized view
    reply = cli(
        "preview", saved["task"], "--file", save(tmp_path / "b.json", payload), "-o", tmp_path / "p"
    )
    assert reply["changes"] == {"added": [], "changed": [], "removed": []}
    assert reply["changes_view"] is None and "warnings" not in reply
    assert not (tmp_path / "p/changes.png").exists()
    with Image.open(reply["view"]) as view:
        assert view.size == (60, 100)
    assert Path(reply["view"]).read_bytes() == Path(saved["view"]).read_bytes()
    assert json.loads(Path(reply["candidate"]).read_text()) == json.loads(
        Path(saved["submission"]).read_text()
    )


def test_preview_keeps_folder_progress_audit_and_discovery_on_saved_state(
    tmp_path: Path,
) -> None:
    images = tmp_path / "images"
    images.mkdir()
    for name in ["a.png", "b.png"]:
        Image.new("RGB", (40, 30), "white").save(images / name)
    folder = cli("task", images, "-o", tmp_path / "tasks")
    one = folder["tasks"][0]["task"]
    payload = save(tmp_path / "s.json", submission() | {"objects": []})
    saved = cli("submit", one, "--file", payload)
    status = cli("task-status", folder["index"])
    audit = cli("audit", images, "-o", tmp_path / "audit1")
    redirect = cli(
        "preview", folder["index"], "--file", payload, "-o", tmp_path / "x", success=False
    )
    assert redirect["code"] == "IMAGE_TASK_REQUIRED" and "task-status" in redirect["recovery"]
    box = {
        "classifications": [],
        "objects": [{"key": "b", "label": "x", "polygon": [[1, 1], [9, 1], [9, 9]]}],
    }
    cli("preview", saved["task"], "--file", save(tmp_path / "b.json", box), "-o", images / "pv")
    assert cli("task-status", folder["index"]) == status
    after = cli("audit", images, "-o", tmp_path / "audit2")
    assert {k: after[k] for k in ["totals", "object_labels", "scene_classes"]} == {
        k: audit[k] for k in ["totals", "object_labels", "scene_classes"]
    }
    assert discover_images(images, tmp_path / "none") == sorted(
        p.resolve() for p in images.glob("*.png")
    )
