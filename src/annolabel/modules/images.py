"""Annotation overlays and masks on in-memory pixels; no inference models."""
from PIL import Image, ImageDraw, ImageFont
from annolabel.schemas.annotations import Document


def render(image: Image.Image, document: Document, grid: int = 0) -> Image.Image:
    """Draw IDs, labels, boxes, polygons, and an optional pixel grid."""
    canvas = image.convert("RGBA")
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default(size=max(12, min(24, image.width // 50)))
    if grid:
        for x in range(0, image.width, grid):
            draw.line([(x, 0), (x, image.height)], fill=(255, 255, 255, 130))
            draw.text((x + 2, 2), str(x), font=font, fill="black", stroke_width=1, stroke_fill="white")
        for y in range(0, image.height, grid):
            draw.line([(0, y), (image.width, y)], fill=(255, 255, 255, 130))
            draw.text((2, y + 2), str(y), font=font, fill="black", stroke_width=1, stroke_fill="white")
    palette = [(14, 165, 233), (234, 88, 12), (139, 92, 246), (22, 163, 74)]
    label_y = 24 if grid else 4
    for index, annotation in enumerate(document.annotations):
        color = palette[index % len(palette)]
        if annotation.kind == "box":
            draw.rectangle(annotation.points, outline=(*color, 255), width=3)
        elif annotation.kind == "polygon":
            draw.polygon(annotation.points, fill=(*color, 65))
            draw.line(annotation.points + annotation.points[:1], fill=(*color, 255), width=3)
        if annotation.points:
            x, y = annotation.points[0]
            y = max(0, y - 20)
        else:
            x, y = 4, label_y
            label_y += 24
        caption = f"{annotation.id}: {annotation.label}"
        bounds = draw.textbbox((0, 0), caption, font=font)
        x = max(0, min(x, image.width - (bounds[2] - bounds[0]) - 8))
        y = max(0, min(y, image.height - 24))
        box = draw.textbbox((x + 3, y + 2), caption, font=font)
        draw.rectangle((box[0]-3, box[1]-2, box[2]+3, box[3]+2), fill=(*color, 255))
        draw.text((x + 3, y + 2), caption, font=font, fill="white")
    return Image.alpha_composite(canvas, overlay).convert("RGB")


def polygon_mask(document: Document, annotation_id: str) -> Image.Image:
    """Rasterize one polygon to an 8-bit binary mask (0/255)."""
    annotation = next((a for a in document.annotations if a.id == annotation_id), None)
    if annotation is None or annotation.kind != "polygon":
        raise ValueError("mask requires the ID of an existing polygon")
    mask = Image.new("L", (document.image.width, document.image.height), 0)
    ImageDraw.Draw(mask).polygon(annotation.points, fill=255)
    return mask
