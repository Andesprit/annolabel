"""Real CLI/image tests of the complete agent workflow and failure boundaries."""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from pycocotools.coco import COCO

from tests.helpers import cli, save


def prepared(source: Path, tmp_path: Path) -> tuple[dict, dict]:
    result = cli("prepare", source, "--output", tmp_path / "prepared")
    return result, json.loads(Path(result["annotations_file"]).read_text())


def test_batch_update_is_atomic_preserves_ids_and_exports(source: Path, tmp_path: Path) -> None:
    context, batch = prepared(source, tmp_path)
    assert not source.with_name(source.name + ".labels.json").exists()
    batch["classifications"] = [{"label": "shapes"}]
    batch["objects"] = [
        {
            "key": "obj1",
            "label": "rectangle",
            "box": [10, 10, 90, 70],
            "polygon": [[10, 10], [90, 10], [90, 70], [10, 70]],
        },
        {"key": "obj2", "label": "triangle", "polygon": [[20, 20], [50, 20], [35, 50]]},
    ]
    payload = save(tmp_path / "batch.json", batch)
    first = cli(
        "apply",
        source,
        "--file",
        payload,
        "--packet",
        context["packet"],
        "-o",
        tmp_path / "review1",
    )
    assert first["object_count"] == 2
    assert len(first["review_pages"]) == 1
    annotations = cli("info", source)["annotations"]
    assert len(annotations) == 5  # class + two linked pairs
    assert {a["object_id"] for a in annotations if a["kind"] != "label"} == {"obj1", "obj2"}
    old_ids = {a["id"] for a in annotations}
    corrected = json.loads(Path(first["annotations_file"]).read_text())
    corrected["objects"][0].update(
        box=[20, 20, 60, 60], polygon=[[20, 20], [60, 20], [60, 60], [20, 60]]
    )
    corrected["objects"][0]["label"] = "square"
    result = cli(
        "apply",
        source,
        "--file",
        save(tmp_path / "corrected.json", corrected),
        "--packet",
        first["packet"],
        "-o",
        tmp_path / "review2",
    )
    assert {a["id"] for a in cli("info", source)["annotations"]} == old_ids
    assert {a["label"] for a in cli("info", source)["annotations"] if a["object_id"] == "obj1"} == {
        "square"
    }
    before = Path(result["sidecar"]).read_bytes()
    assert (
        "stale"
        in cli("apply", source, "--file", payload, "-o", tmp_path / "retry", success=False)["error"]
    )
    assert Path(result["sidecar"]).read_bytes() == before
    assert not (tmp_path / "retry").exists()
    cli("export", source, "-o", tmp_path / "dataset")
    coco = COCO(str(tmp_path / "dataset/annotations/instances_default.json"))
    assert len(coco.anns) == 2
    assert all(coco.annToMask(a).sum() > 0 for a in coco.anns.values())


@pytest.mark.parametrize(
    "case", ["outside", "self_intersect", "duplicate", "unknown_view", "extra", "wrong_source"]
)
def test_invalid_batch_preserves_entire_existing_snapshot(
    source: Path, tmp_path: Path, case: str
) -> None:
    old = cli("box", source, "--label", "keep", "--xyxy", 5, 5, 10, 10)
    context, batch = prepared(source, tmp_path)
    batch["objects"] = [{"key": "new", "label": "leaf", "polygon": [[20, 20], [50, 20], [35, 50]]}]
    if case == "outside":
        batch["objects"][0]["polygon"][0] = [-1, 20]
    if case == "self_intersect":
        batch["objects"][0]["polygon"] = [[0, 0], [90, 70], [0, 70], [70, 0]]
    if case == "duplicate":
        batch["objects"].append(batch["objects"][0].copy())
    if case == "unknown_view":
        batch["objects"][0]["view_id"] = "missing"
    if case == "extra":
        batch["objects"][0]["confidence"] = 0.9
    if case == "wrong_source":
        batch["image_sha256"] = "wrong"
    path = Path(old["sidecar"])
    before = path.read_bytes()
    failure = cli(
        "apply",
        source,
        "--file",
        save(tmp_path / "bad.json", batch),
        "--packet",
        context["packet"],
        "-o",
        tmp_path / "bad-review",
        success=False,
    )
    assert failure["code"] == "VALIDATION_ERROR"
    assert path.read_bytes() == before
    assert not (tmp_path / "bad-review").exists()


def test_task_rules_geometry_and_category_enforcement(source: Path, tmp_path: Path) -> None:
    instructions = tmp_path / "instructions.txt"
    instructions.write_text("Label leaves; ignore tiny background objects.")
    categories = save(tmp_path / "categories.json", ["leaf"])
    context = cli(
        "prepare",
        source,
        "-o",
        tmp_path / "prep",
        "--instructions",
        instructions,
        "--categories",
        categories,
    )
    assert context["rules"]["instructions"] == instructions.read_text()
    batch = json.loads(Path(context["annotations_file"]).read_text())
    batch["objects"] = [{"key": "one", "label": "car", "box": [10, 10, 20, 20]}]
    payload = save(tmp_path / "batch.json", batch)
    assert (
        "allowed categories"
        in cli("apply", source, "--file", payload, "--packet", context["packet"], success=False)[
            "error"
        ]
    )
    batch["objects"][0]["label"] = "leaf"
    save(payload, batch)
    assert (
        "requires a polygon"
        in cli("apply", source, "--file", payload, "--packet", context["packet"], success=False)[
            "error"
        ]
    )
    batch["objects"][0]["polygon"] = [[10, 10], [20, 10], [15, 20]]
    save(payload, batch)
    result = cli("apply", source, "--file", payload, "--packet", context["packet"])
    assert Path(result["overview"]).exists()


def test_view_pixels_map_back_with_padding_and_page_offsets(source: Path, tmp_path: Path) -> None:
    region_file = save(
        tmp_path / "regions.json", [{"key": f"r{i}", "box": [20, 20, 40, 40]} for i in range(3)]
    )
    review = cli("review", source, "--regions", region_file, "-o", tmp_path / "crops")
    assert len(review["review_pages"]) == 2
    packet = json.loads(Path(review["packet"]).read_text())
    view = next(v for v in packet["views"] if v["id"] == "region-2-annotated")
    assert view["source_rect"] == [4, 4, 56, 56]  # requested box plus the 16-pixel minimum context
    a, b, c, d = view["content_rect"]
    assert a > 400 and b > 100  # second column and second row
    batch = json.loads(Path(review["annotations_file"]).read_text())
    batch["objects"] = [
        {
            "key": "region",
            "label": "area",
            "view_id": view["id"],
            "polygon": [[a, b], [c, b], [c, d], [a, d]],
        }
    ]
    result = cli(
        "apply",
        source,
        "--file",
        save(tmp_path / "batch.json", batch),
        "--packet",
        review["packet"],
        "-o",
        tmp_path / "applied",
    )
    actual = json.loads(Path(result["annotations_file"]).read_text())["objects"][0]
    assert actual["box"] == view["source_rect"]
    assert "view_id" not in actual


def test_coordinate_context_rejects_padding_and_wrong_image(source: Path, tmp_path: Path) -> None:
    context, batch = prepared(source, tmp_path)
    batch["objects"] = [
        {"key": "one", "label": "shape", "view_id": "grid", "polygon": [[0, 0], [70, 50], [80, 70]]}
    ]
    payload = save(tmp_path / "batch.json", batch)
    assert (
        "outside view content"
        in cli("apply", source, "--file", payload, "--packet", context["packet"], success=False)[
            "error"
        ]
    )
    packet = json.loads(Path(context["packet"]).read_text())
    packet["width"] += 1
    assert (
        "does not match"
        in cli(
            "review",
            source,
            "--packet",
            save(tmp_path / "wrong.json", packet),
            "-o",
            tmp_path / "wrong-review",
            success=False,
        )["error"]
    )


def test_prepare_orientation_and_review_preserves_original_pixels(tmp_path: Path) -> None:
    source = tmp_path / "oriented.jpg"
    exif = Image.Exif()
    exif[274] = 6
    patterned = Image.new("RGB", (80, 40), "red")
    draw = ImageDraw.Draw(patterned)
    draw.rectangle((0, 0, 39, 19), fill="blue")
    draw.rectangle((40, 20, 79, 39), fill="green")
    patterned.save(source, exif=exif)
    context, batch = prepared(source, tmp_path)
    assert (context["width"], context["height"]) == (40, 80)
    with Image.open(context["original"]) as image:
        assert image.size == (40, 80)
    batch["objects"] = [
        {"key": "one", "label": "red area", "polygon": [[10, 20], [30, 20], [30, 60], [10, 60]]}
    ]
    result = cli(
        "apply", source, "--file", save(tmp_path / "batch.json", batch), "-o", tmp_path / "review"
    )
    packet = json.loads(Path(result["packet"]).read_text())
    view = next(v for v in packet["views"] if v["id"] == "region-1-original")
    x1, y1, x2, y2 = map(int, view["content_rect"])
    with Image.open(view["path"]) as image, Image.open(context["original"]) as original:
        crop = image.crop((x1, y1, x2, y2))
        expected = original.crop(tuple(view["source_rect"])).resize(
            crop.size, Image.Resampling.NEAREST
        )
        assert crop.tobytes() == expected.tobytes()  # no rulers or labels hide original pixels


def test_output_failure_and_full_replacement_semantics(source: Path, tmp_path: Path) -> None:
    existing = cli("label", source, "--label", "keep")
    context, batch = prepared(source, tmp_path)
    before = Path(existing["sidecar"]).read_bytes()
    batch["objects"] = [{"key": "one", "label": "shape", "box": [10, 10, 20, 20]}]
    payload = save(tmp_path / "batch.json", batch)
    cli("apply", source, "--file", payload, "-o", tmp_path / "prepared", success=False)
    assert Path(existing["sidecar"]).read_bytes() == before
    batch["classifications"] = []
    result = cli("apply", source, "--file", save(payload, batch), "-o", tmp_path / "new")
    assert len(cli("info", source)["annotations"]) == 1
    current = json.loads(Path(result["annotations_file"]).read_text())
    current["objects"] = []
    cli("apply", source, "--file", save(payload, current), "-o", tmp_path / "empty")
    assert cli("info", source)["annotations"] == []
