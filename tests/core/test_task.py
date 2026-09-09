"""Real CLI checks for the short task protocol and safe recovery."""
import json
from pathlib import Path
import pytest
from PIL import Image, ImageOps
from tests.helpers import cli, save


def submission() -> dict:
    return {"classifications": [{"label": "shapes"}], "objects": [
        {"key": "one", "label": "rectangle", "polygon": [[10,10],[60,10],[60,50],[10,50]]}]}


def test_short_task_two_passes_recovery_retry_and_export(source: Path, tmp_path: Path) -> None:
    initial = cli("task", source, "-o", tmp_path/"task")
    payload = save(tmp_path/"labels.json", submission())
    first = cli("submit", initial["task"], "--file", payload)
    assert first["objects"] == 1 and first["passes_left"] == 1
    assert len(json.dumps(first)) < 1000
    assert set(first) == {"status", "task", "view", "objects", "passes_left"}
    assert cli("submit", initial["task"], "--file", payload) == first  # lost-response retry
    assert cli("task-status", initial["task"]) == first
    assert not list(Path(first["view"]).parent.glob("review-*.png"))
    before = cli("info", source)["annotations"]
    changed = submission(); changed["objects"][0]["label"] = "square"
    changed["objects"][0]["polygon"] = [[20,20],[40,20],[40,40],[20,40]]
    correction = save(tmp_path/"correction.json", changed)
    used = cli("submit", initial["task"], "--file", correction, success=False)
    assert used["code"] == "CHECKPOINT_USED" and "task-status" in used["recovery"]
    second = cli("submit", first["task"], "--file", correction)
    assert second["passes_left"] == 0
    assert cli("task-status", initial["task"]) == second
    assert {a["id"] for a in before} == {a["id"] for a in cli("info", source)["annotations"]}
    assert cli("submit", second["task"], "--file", correction, success=False)["code"] == "PASS_LIMIT"
    assert cli("export", source, "-o", tmp_path/"coco")["objects"] == 1
    # Source, initial view and both reviews share the exact same pixel origin and extent.
    with Image.open(source) as original, Image.open(initial["view"]) as a, Image.open(first["view"]) as b, Image.open(second["view"]) as c:
        assert a.size == b.size == c.size == original.size
        assert a.tobytes() == original.convert("RGB").tobytes()
        assert b.getpixel((10, 10)) != a.getpixel((10, 10))  # first polygon starts here
        assert c.getpixel((20, 20)) != a.getpixel((20, 20))  # corrected polygon starts here
        assert c.getpixel((10, 10)) == a.getpixel((10, 10))  # old outline is removed
    packets = [json.loads((Path(v["view"]).parent/"packet.json").read_text()) for v in [initial, first, second]]
    for packet in packets:
        view = next(v for v in packet["views"] if v["id"] == "view")
        assert view["source_rect"] == view["content_rect"] == [0, 0, initial["width"], initial["height"]]


def test_short_view_exif_orientation_and_edge_coordinates(tmp_path: Path) -> None:
    source = tmp_path/"rotated.jpg"
    image = Image.new("RGB", (100, 60), "white")
    image.paste("black", (0, 0, 50, 30))
    exif = Image.Exif(); exif[274] = 6
    image.save(source, exif=exif)
    initial = cli("task", source, "-o", tmp_path/"task")
    with Image.open(source) as raw, Image.open(initial["view"]) as view:
        oriented = ImageOps.exif_transpose(raw).convert("RGB")
        assert view.size == (60, 100) == (initial["width"], initial["height"])
        assert view.tobytes() == oriented.tobytes()
    # Explicit view coordinates are also identity coordinates, right up to the outer edges.
    payload = {"classifications": [], "objects": [{"key": "edge", "label": "region", "view_id": "view",
               "polygon": [[0,0], [60,0], [60,100], [0,100]]}]}
    saved = cli("submit", initial["task"], "--file", save(tmp_path/"edge.json", payload))
    annotations = cli("info", source)["annotations"]
    assert next(a for a in annotations if a["kind"] == "polygon")["points"] == payload["objects"][0]["polygon"]
    with Image.open(saved["view"]) as view:
        assert view.size == (60, 100)
        assert view.getpixel((0, 50)) != oriented.getpixel((0, 50))


def test_invalid_submission_and_external_edit_leave_task_unchanged(source: Path, tmp_path: Path) -> None:
    initial = cli("task", source, "-o", tmp_path/"task")
    bad = submission(); bad["objects"][0]["polygon"][0] = [-1,10]
    error = cli("submit", initial["task"], "--file", save(tmp_path/"bad.json", bad), success=False)
    assert error["code"] == "VALIDATION_ERROR" and error["recovery"]
    assert not (tmp_path/"task/pass1").exists()
    assert not Path(str(source)+".labels.json").exists()
    cli("label", source, "--label", "external edit")
    before = Path(str(source)+".labels.json").read_bytes()
    payload = save(tmp_path/"good.json", submission())
    assert cli("submit", initial["task"], "--file", payload, success=False)["code"] == "TASK_CONFLICT"
    assert cli("task-status", initial["task"], success=False)["code"] == "TASK_CONFLICT"
    assert Path(str(source)+".labels.json").read_bytes() == before


def test_one_pass_task_and_duplicate_object_keys(source: Path, tmp_path: Path) -> None:
    initial = cli("task", source, "--max-passes", 1, "-o", tmp_path/"task")
    bad = submission(); bad["objects"].append(bad["objects"][0].copy())
    assert cli("submit", initial["task"], "--file", save(tmp_path/"bad.json",bad),success=False)["code"] == "VALIDATION_ERROR"
    payload = save(tmp_path/"good.json", submission())
    result = cli("submit", initial["task"], "--file", payload)
    assert result["passes_left"] == 0
    assert cli("submit", result["task"], "--file", payload, success=False)["code"] == "PASS_LIMIT"
