"""Image source contract consumed by annotation and export logic."""
from abc import ABC, abstractmethod
from pathlib import Path
from PIL import Image
from annolabel.schemas.annotations import ImageInfo


class ImageServiceBase(ABC):
    """Read a source image without altering its bytes or orientation metadata."""

    @abstractmethod
    def load(self, path: Path) -> tuple[Image.Image, ImageInfo]:
        """Load one image in its displayed orientation.

        :param path: Source image file.
        :returns: Oriented RGB pixels and source identity.
        """
        ...
