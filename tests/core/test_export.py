"""Object identity, compatibility, and portable COCO dataset integration checks."""

import csv
import json
from pathlib import Path
from unittest.mock import create_autospec

import pytest
from PIL import Image
from pycocotools.coco import COCO

from annolabel.core.export import export_dataset
from annolabel.services.images.base import ImageServiceBase
from annolabel.services.images.local import LocalImageService
from tests.helpers import cli


def source_at(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (100, 80), "white").save(path)
    return path


def read_coco(root: Path) -> dict:
    return json.loads((root / "annotations/instances_default.json").read_text())


def test_linked_object_exports_once_and_classification_is_separate(tmp_path: Path) -> None:
    source = source_at(tmp_path / "photo.png")
    box = cli("box", source, "--label", "triangle", "--xyxy", 10, 10, 50, 50)["annotation"]
    polygon = cli(
        "polygon",
        source,
        "--label",
        "triangle",
        "--points",
        "[[10,10],[50,10],[30,50]]",
        "--object-id",
        box["object_id"],
    )["annotation"]
    assert polygon["object_id"] == box["object_id"]
    cli("label", source, "--label", "indoor", "--note", "scene classification")
    output = tmp_path / "dataset"
    result = cli("export", source, "--output", output)
    assert result["format"] == "coco" and result["objects"] == 1 and result["classifications"] == 1
    coco = read_coco(output)
    assert coco["categories"] == [{"id": 1, "name": "triangle", "supercategory": ""}]
    assert coco["annotations"] == [
        {
            "id": 1,
            "image_id": 1,
            "category_id": 1,
            "bbox": [10, 10, 40, 40],
            "area": 800,
            "iscrowd": 0,
            "segmentation": [[10, 10, 50, 10, 30, 50]],
        }
    ]
    with (output / "classifications.csv").open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["label"] == "indoor"
    assert (output / rows[0]["file_name"]).exists()
    provenance = json.loads((output / "provenance.json").read_text())
    assert provenance["objects"][0]["object_id"] == box["object_id"]
    assert len(provenance["objects"][0]["source_annotations"]) == 2
    reference = COCO(str(output / "annotations/instances_default.json"))
    assert reference.getAnnIds() == [1]
    mask = reference.annToMask(reference.loadAnns([1])[0])
    assert mask.shape == (80, 100)
    assert mask[20, 30] == 1 and mask[70, 90] == 0
    cli("export", source, "--output", output, success=False)
    assert read_coco(output) == coco


def test_link_existing_shapes_and_preserve_identity_on_edits(tmp_path: Path) -> None:
    source = source_at(tmp_path / "photo.png")
    box = cli("box", source, "--label", "leaf", "--xyxy", 0, 0, 60, 60)["annotation"]
    polygon = cli("polygon", source, "--label", "leaf", "--points", "[[10,10],[50,10],[30,50]]")[
        "annotation"
    ]
    assert polygon["object_id"] != box["object_id"]
    cli("link", source, "--ids", box["id"], polygon["id"])
    changed = cli("box", source, "--id", box["id"], "--label", "petal", "--xyxy", 5, 5, 55, 55)[
        "annotation"
    ]
    assert changed["object_id"] == box["object_id"]
    annotations = cli("info", source)["annotations"]
    assert {a["label"] for a in annotations} == {"petal"}
    cli("remove", source, "--id", box["id"])
    output = tmp_path / "dataset"
    cli("export", source, "-o", output)
    # Polygon-only objects derive a box, retaining the object identity.
    assert read_coco(output)["annotations"][0]["bbox"] == [10, 10, 40, 40]
    assert cli("info", source)["annotations"][0]["object_id"] == box["object_id"]


def test_same_label_objects_are_not_merged_and_v1_is_readable(tmp_path: Path) -> None:
    source = source_at(tmp_path / "photo.png")
    cli("box", source, "--label", "car", "--xyxy", 1, 1, 10, 10)
    cli("box", source, "--label", "car", "--xyxy", 20, 20, 40, 40)
    sidecar = source.with_name(source.name + ".labels.json")
    data = json.loads(sidecar.read_text())
    data["version"] = 1
    for annotation in data["annotations"]:
        annotation.pop("object_id")
    sidecar.write_text(json.dumps(data))
    original = sidecar.read_bytes()
    output = tmp_path / "dataset"
    result = cli("export", source, "-o", output)
    assert result["objects"] == 2
    assert sidecar.read_bytes() == original
    assert all("segmentation" not in a for a in read_coco(output)["annotations"])
    assert cli("info", source)["version"] == 2


def test_invalid_links_do_not_modify_sidecar(tmp_path: Path) -> None:
    source = source_at(tmp_path / "photo.png")
    a = cli("box", source, "--label", "a", "--xyxy", 0, 0, 20, 20)["annotation"]
    b = cli("box", source, "--label", "a", "--xyxy", 0, 0, 30, 30)["annotation"]
    c = cli("polygon", source, "--label", "b", "--points", "[[0,0],[20,0],[10,20]]")["annotation"]
    d = cli("polygon", source, "--label", "a", "--points", "[[0,0],[40,0],[10,40]]")["annotation"]
    label = cli("label", source, "--label", "scene")["annotation"]
    sidecar = Path(cli("info", source)["sidecar"])
    original = sidecar.read_bytes()
    for other in [b["id"], c["id"], d["id"], label["id"], "unknown", a["id"]]:
        cli("link", source, "--ids", a["id"], other, success=False)
        assert sidecar.read_bytes() == original
    cli(
        "polygon",
        source,
        "--label",
        "a",
        "--points",
        "[[0,0],[20,0],[10,20]]",
        "--object-id",
        "missing",
        success=False,
    )
    assert sidecar.read_bytes() == original


def test_folder_export_handles_duplicate_names_exif_and_shared_vocabulary(tmp_path: Path) -> None:
    images = tmp_path / "inputs"
    a = source_at(images / "a/photo.jpg")
    b = source_at(images / "b/photo.jpg")
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (100, 80), "red").save(b, exif=exif)
    cli("box", a, "--label", "zebra", "--xyxy", 0, 0, 100, 80)
    cli("box", b, "--label", "ant", "--xyxy", 0, 0, 80, 100)
    output = tmp_path / "dataset"
    cli("export", images, "-o", output)
    coco = read_coco(output)
    assert [c["name"] for c in coco["categories"]] == ["ant", "zebra"]
    assert len({i["file_name"] for i in coco["images"]}) == 2
    for record in coco["images"]:
        with Image.open(output / "images/default" / record["file_name"]) as image:
            assert image.size == (record["width"], record["height"])
            assert image.getexif().get(274) is None
    subset = tmp_path / "subset"
    cli("export", a, "-o", subset, "--categories", output / "categories.json")
    assert read_coco(subset)["annotations"][0]["category_id"] == 2
    assert read_coco(subset)["categories"] == coco["categories"]


@pytest.mark.parametrize("vocabulary", [["missing"], ["car", "car"], [1], "car", {"car": 1}, None])
def test_bad_vocabulary_leaves_no_partial_export(tmp_path: Path, vocabulary: object) -> None:
    source = source_at(tmp_path / "photo.png")
    cli("box", source, "--label", "car", "--xyxy", 0, 0, 10, 10)
    vocab = tmp_path / "categories.json"
    vocab.write_text(json.dumps(vocabulary))
    output = tmp_path / "dataset"
    cli("export", source, "-o", output, "--categories", vocab, success=False)
    assert not output.exists()


def test_empty_images_and_stale_sources(tmp_path: Path) -> None:
    source = source_at(tmp_path / "photo.png")
    output = tmp_path / "empty"
    cli("export", source, "-o", output)
    assert len(read_coco(output)["images"]) == 1
    assert read_coco(output)["annotations"] == []
    cli("box", source, "--label", "car", "--xyxy", 0, 0, 10, 10)
    Image.new("RGB", (100, 80), "black").save(source)
    cli("export", source, "-o", tmp_path / "stale", success=False)
    assert not (tmp_path / "stale").exists()


def test_injected_reader_failure_during_export_leaves_no_dataset(
    source: Path, tmp_path: Path
) -> None:
    service = create_autospec(ImageServiceBase, instance=True)
    service.load.side_effect = [
        LocalImageService().load(source),
        OSError("source became unavailable"),
    ]
    output = tmp_path / "dataset"
    with pytest.raises(OSError, match="source became unavailable"):
        export_dataset(str(source), str(output), image_service=service)
    assert service.load.call_count == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".annolabel-export-*"))
