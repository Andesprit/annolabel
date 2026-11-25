"""Rendering and mask operations on in-memory pixels."""

from PIL import Image

from annolabel.modules.images import polygon_mask, render
from annolabel.schemas.annotations import Annotation, Document


def test_render_and_mask_preserve_source_pixels(document: Document) -> None:
    original = Image.new("RGB", (100, 80), "white")
    annotated = Document(
        image=document.image,
        annotations=[
            Annotation(
                id="leaf", kind="polygon", label="leaf", points=[(10, 30), (70, 30), (40, 70)]
            )
        ],
    )
    before = original.tobytes()
    preview = render(original, annotated)
    mask = polygon_mask(annotated, "leaf")
    assert original.tobytes() == before
    assert preview.getpixel((40, 50)) != original.getpixel((40, 50))
    assert mask.mode == "L" and set(mask.tobytes()) == {0, 255}
    assert mask.getpixel((40, 50)) == 255 and mask.getpixel((90, 70)) == 0


def test_label_color_follows_categories_not_present_labels() -> None:
    from annolabel.modules.images import COLORS, label_color
    from annolabel.schemas.annotations import Annotation, Document, ImageInfo

    doc = Document(
        image=ImageInfo(file="x.png", width=10, height=10, sha256="0"),
        annotations=[Annotation(id="b1", kind="line", label="b", points=[(1, 1), (5, 1)])],
    )
    assert label_color("b", doc) == COLORS[0]
    assert label_color("b", doc, ["c", "b", "a"]) == COLORS[1]
