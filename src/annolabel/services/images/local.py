"""Local image loading through Pillow."""
import hashlib
from pathlib import Path
from PIL import Image, ImageOps
from annolabel.schemas.annotations import ImageInfo
from annolabel.services.images.base import ImageServiceBase


class LocalImageService(ImageServiceBase):
    """Read single-frame raster files from the local filesystem."""

    def load(self, path: Path) -> tuple[Image.Image, ImageInfo]:
        """Read pixels, apply EXIF orientation, and identify the source bytes.

        :param path: Source image file.
        :returns: Oriented RGB pixels and source identity.
        """
        with Image.open(path) as source:
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("multi-frame images are unsupported; extract a frame first")
            image = ImageOps.exif_transpose(source).convert("RGB")
        info = ImageInfo(file=path.name, width=image.width, height=image.height,
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        return image, info
