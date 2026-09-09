"""Exercise the image service against actual files and EXIF metadata."""

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from annolabel.services.images.local import LocalImageService


def test_orients_pixels_without_modifying_source(tmp_path: Path) -> None:
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (80, 40), "red")
    image.paste("blue", (0, 0, 40, 20))
    exif = Image.Exif()
    exif[274] = 6
    image.save(path, exif=exif)
    original = path.read_bytes()
    actual, info = LocalImageService().load(path)
    with Image.open(path) as raw:
        assert actual.tobytes() == ImageOps.exif_transpose(raw).convert("RGB").tobytes()
    assert actual.size == (40, 80) == (info.width, info.height)
    assert info.sha256 == hashlib.sha256(original).hexdigest()
    assert path.read_bytes() == original


def test_rejects_multiframe_images(tmp_path: Path) -> None:
    path = tmp_path / "animated.gif"
    Image.new("RGB", (10, 10), "red").save(
        path, save_all=True, append_images=[Image.new("RGB", (10, 10), "blue")]
    )
    with pytest.raises(ValueError, match="multi-frame"):
        LocalImageService().load(path)
