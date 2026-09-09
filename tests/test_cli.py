"""End-to-end checks against real images and the public CLI."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
from PIL import Image


def cli(*args: object, success: bool = True) -> dict:
    result = subprocess.run([sys.executable, "-m", "annolabel.main", *map(str, args)],
                            text=True, capture_output=True)
    assert result.returncode == (0 if success else 2), result.stderr
    if success:
        assert not result.stderr
        return json.loads(result.stdout)
    assert not result.stdout
    return json.loads(result.stderr)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "image with spaces.png"
    Image.new("RGB", (100, 80), "white").save(path)
    return path


def test_full_annotation_and_correction_workflow(source: Path, tmp_path: Path) -> None:
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    info = cli("info", source)
    assert info["image"]["width"] == 100
    assert not Path(info["sidecar"]).exists()
    classification = cli("label", source, "--label", "shapes")
    box = cli("box", source, "--label", "square", "--xyxy", 10, 10, 40, 40)
    polygon = cli("polygon", source, "--label", "triangle", "--points", "[[50,10],[90,10],[70,60]]")
    assert len(cli("info", source)["annotations"]) == 3
    corrected = cli("box", source, "--label", "rectangle", "--xyxy", 10, 10, 45, 40,
                    "--id", box["annotation"]["id"])
    assert corrected["annotation"]["points"] == [[10, 10], [45, 40]]
    assert len(cli("info", source)["annotations"]) == 3
    preview = tmp_path / "preview.png"
    cli("render", source, "--output", preview, "--grid", 20)
    with Image.open(preview) as image:
        assert image.size == (100, 80)
        assert image.getpixel((70, 30)) != (255, 255, 255)
    mask = tmp_path / "mask.png"
    cli("mask", source, "--id", polygon["annotation"]["id"], "--output", mask)
    with Image.open(mask) as image:
        assert image.mode == "L" and image.size == (100, 80)
        assert image.getpixel((70, 30)) == 255
        assert image.getpixel((5, 70)) == 0
        assert set(image.tobytes()) == {0, 255}
    cli("remove", source, "--id", classification["annotation"]["id"])
    assert len(cli("info", source)["annotations"]) == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


@pytest.mark.parametrize("arguments", [
    ["box", "--label", "bad", "--xyxy", 10, 10, 101, 60],
    ["box", "--label", "bad", "--xyxy", 10, 10, 5, 60],
    ["box", "--label", "bad", "--xyxy", 0, 0, "nan", 60],
    ["polygon", "--label", "bad", "--points", "[[0,0],[10,10]]"],
    ["polygon", "--label", "bad", "--points", "[[0,0],[10,10],[20,20]]"],
    ["polygon", "--label", "bad", "--points", "[[0,0],[90,70],[0,70],[70,0]]"],
    ["polygon", "--label", "bad", "--points", "[[0,0],[90,0],[0,70],[0,0]]"],
    ["polygon", "--label", "bad", "--points", "not json"],
    ["label", "--label", "   "],
    ["label", "--label", "new", "--id", "missing"],
    ["remove", "--id", "missing"],
])
def test_invalid_edits_leave_sidecar_unchanged(source: Path, arguments: list) -> None:
    info = cli("label", source, "--label", "keep")
    sidecar = Path(info["sidecar"])
    original = sidecar.read_bytes()
    result = cli(arguments[0], source, *arguments[1:], success=False)
    assert "error" in result
    assert sidecar.read_bytes() == original


def test_orientation_and_changed_source(tmp_path: Path) -> None:
    source = tmp_path / "rotated.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (100, 60), "red").save(source, exif=exif)
    info = cli("info", source)
    assert (info["image"]["width"], info["image"]["height"]) == (60, 100)
    cli("box", source, "--label", "full", "--xyxy", 0, 0, 60, 100)
    output = tmp_path / "oriented.png"
    cli("render", source, "--output", output)
    with Image.open(output) as image:
        assert image.size == (60, 100)
    Image.new("RGB", (100, 60), "blue").save(source, exif=exif)
    assert "differs" in cli("info", source, success=False)["error"]


def test_output_protection_and_mask_errors(source: Path, tmp_path: Path) -> None:
    original = source.read_bytes()
    cli("render", source, "--output", source, "--force", success=False)
    alias = tmp_path / "alias.png"
    alias.hardlink_to(source)
    cli("render", source, "--output", alias, "--force", success=False)
    assert source.read_bytes() == original
    preview = tmp_path / "preview.png"
    cli("render", source, "--output", preview)
    cli("render", source, "--output", preview, success=False)
    cli("render", source, "--output", preview, "--force")
    cli("render", source, "--output", tmp_path / "bad.jpg", success=False)
    cli("render", source, "--output", preview, "--grid", -1, success=False)
    label = cli("label", source, "--label", "image")
    cli("mask", source, "--id", label["annotation"]["id"], "-o", tmp_path / "mask.png", success=False)
    cli("mask", source, "--id", "missing", "-o", tmp_path / "mask.png", success=False)


def test_points_file_and_corrupt_sidecar(source: Path, tmp_path: Path) -> None:
    points = tmp_path / "points.json"
    points.write_text("[[0,0],[80,0],[40,70]]")
    result = cli("polygon", source, "--label", "object", "--points-file", points)
    sidecar = Path(result["sidecar"])
    sidecar.write_text("{}")
    cli("label", source, "--label", "new", success=False)
    assert sidecar.read_text() == "{}"
