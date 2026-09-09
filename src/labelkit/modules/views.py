"""Deterministic image packets and paginated reviews; no boundary inference."""
import json
import math
import os
import shutil
import tempfile
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from labelkit.modules.batch import snapshot
from labelkit.schemas.annotations import Document
from labelkit.schemas.workflow import Batch, Packet, Region, Rules, View

MARGIN_X, MARGIN_Y = 58, 50
COLORS = ["#f43f5e", "#00a6ff", "#f59e0b", "#16a34a", "#a855f7", "#06b6d4"]


def _font(size: int = 15) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _ruler(image: Image.Image, rect: tuple, title: str, grid: bool = False) -> Image.Image:
    """Add original-pixel rulers outside the content; never obscure it with labels."""
    canvas = Image.new("RGB", (image.width + MARGIN_X, image.height + MARGIN_Y), "#101827")
    canvas.paste(image, (MARGIN_X, MARGIN_Y))
    draw = ImageDraw.Draw(canvas)
    draw.text((5, 3), title, fill="white", font=_font(14))
    x1, y1, x2, y2 = rect
    for axis in (0, 1):
        low, high = (x1, x2) if axis == 0 else (y1, y2)
        length = image.width if axis == 0 else image.height
        step = max(1, math.ceil((high-low)/max(1, length//90)))
        ticks = [low] + list(range(math.ceil(low/step)*step, math.floor(high/step)*step+1, step)) + [high]
        positions = []
        for value in sorted(set(ticks)):
            pos = round((value-low)*length/(high-low))
            # Avoid crowded endpoint labels.
            if positions and pos-positions[-1] < 42:
                continue
            positions.append(pos)
            label = f"{value:g}"
            if axis == 0:
                x = MARGIN_X + min(pos, image.width-1)
                draw.text((max(MARGIN_X, min(x-10, canvas.width-40)), 27), label, font=_font(12), fill="white")
                if grid:
                    draw.line((x, MARGIN_Y, x, canvas.height-1), fill="#9ca3af", width=1)
            else:
                y = MARGIN_Y + min(pos, image.height-1)
                draw.text((2, min(y-6, canvas.height-16)), label, font=_font(12), fill="white")
                if grid:
                    draw.line((MARGIN_X, y, canvas.width-1, y), fill="#9ca3af", width=1)
    return canvas


def _outlined(image: Image.Image, document: Document, keys: set[str] | None = None) -> Image.Image:
    result = image.copy()
    draw = ImageDraw.Draw(result)
    ids = list(dict.fromkeys(a.object_id for a in document.annotations if a.object_id is not None))
    for a in document.annotations:
        if a.object_id is None or (keys is not None and a.object_id not in keys):
            continue
        color = COLORS[ids.index(a.object_id) % len(COLORS)]
        if a.kind == "polygon":
            draw.line(a.points + a.points[:1], fill=color, width=2)
        elif a.kind == "box":
            draw.rectangle(a.points, outline=color, width=1)
    return result


def write_bundle(image: Image.Image, document: Document, output: str, *,
                 rules: Rules, prepare: bool = False, regions: list[Region] | None = None,
                 per_page: int = 2, padding: float = .25, short: bool = False,
                 extra_files: dict[str, dict] | None = None) -> dict:
    """Write into a new directory, staging all files before publishing it."""
    if not 1 <= per_page <= 4 or not 0 < padding <= 2:
        raise ValueError("per_page must be 1..4 and padding must be > 0 and <= 2")
    destination = Path(output).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("view output already exists; choose a new directory")
    if regions is not None and len({r.key for r in regions}) != len(regions):
        raise ValueError("region keys must be unique")
    selected = []
    objects = snapshot(document)["objects"]
    for item in ([] if short else (regions if regions is not None else objects)):
        if isinstance(item, Region):
            key, label, box = item.key, "requested region", item.box
        else:
            key, label = item["key"], item["label"]
            box = item.get("box")
            if box is None:
                xs, ys = zip(*item["polygon"])
                box = (min(xs), min(ys), max(xs), max(ys))
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= image.width and 0 <= y1 < y2 <= image.height):
            raise ValueError(f"region {key}: box must be ordered and inside the source image")
        px, py = max(16, (x2-x1)*padding), max(16, (y2-y1)*padding)
        crop = (max(0, math.floor(x1-px)), max(0, math.floor(y1-py)),
                min(image.width, math.ceil(x2+px)), min(image.height, math.ceil(y2+py)))
        selected.append((key, label, crop))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".labelkit-views-", dir=destination.parent))
    packet = Packet(image_sha256=document.image.sha256, width=image.width, height=image.height, rules=rules)
    pages = []
    try:
        rect = (0, 0, image.width, image.height)
        image.save(temporary / "original.png")
        packet.views.append(View(id="original", path=str(destination/"original.png"), source_rect=rect, content_rect=rect))
        if short:
            # One coordinate frame: PNG pixels are oriented source pixels, including at the edges.
            _outlined(image, document).save(temporary/"view.png")
            packet.views.append(View(id="view", path=str(destination/"view.png"),
                                     source_rect=rect, content_rect=rect))
        if prepare:
            _ruler(image, rect, "Original oriented pixels: x right, y down", grid=True).save(temporary/"grid.png")
            packet.views.append(View(id="grid", path=str(destination/"grid.png"), source_rect=rect,
                                     content_rect=(MARGIN_X, MARGIN_Y, MARGIN_X+image.width, MARGIN_Y+image.height)))
        overview = _ruler(_outlined(image, document), rect, "Overview: thin boxes and polygons; coordinates are ORIGINAL pixels")
        legend = [f"{i+1}. {obj['key']}: {obj['label']}" for i, obj in enumerate(objects)]
        legend_lines = [(i, line) for i, label in enumerate(legend) for line in textwrap.wrap(label, max(30, overview.width//9))]
        page = Image.new("RGB", (overview.width, overview.height + max(30, len(legend_lines)*22+12)), "#101827")
        page.paste(overview, (0,0))
        draw = ImageDraw.Draw(page)
        for line_no, (obj_no, line) in enumerate(legend_lines):
            draw.text((10, overview.height+6+line_no*22), line, font=_font(), fill=COLORS[obj_no % len(COLORS)])
        page.save(temporary/"overview.png")
        packet.views.append(View(id="overview", path=str(destination/"overview.png"), source_rect=rect,
                                 content_rect=(MARGIN_X, MARGIN_Y, MARGIN_X+image.width, MARGIN_Y+image.height)))
        for start in range(0, len(selected), per_page):
            rows = []
            page_views = []
            offset_y = 0
            filename = f"review-{start//per_page+1:03}.png"
            for index, (key, label, crop) in enumerate(selected[start:start+per_page], start):
                original = image.crop(crop)
                factor = min(3, 384/original.width, 320/original.height)
                size = (max(1, round(original.width*factor)), max(1, round(original.height*factor)))
                annotated = _outlined(image, document, {key} if regions is None else None).crop(crop)
                title_lines = textwrap.wrap(f"{index+1}. {key}: {label} | ruler labels are ORIGINAL coordinates", 90)
                heading = 12 + 20*len(title_lines)
                pair = Image.new("RGB", (2*(384+MARGIN_X)+12, size[1]+MARGIN_Y+heading), "#101827")
                draw = ImageDraw.Draw(pair)
                for line_no, line in enumerate(title_lines):
                    draw.text((8, 4+20*line_no), line, font=_font(14), fill="white")
                for side, (name, content) in enumerate([("original", original), ("annotated", annotated)]):
                    x = side*(384+MARGIN_X+12)
                    panel = _ruler(content.resize(size, Image.Resampling.NEAREST), crop, name)
                    pair.paste(panel, (x, heading))
                    page_views.append(View(id=f"region-{index+1}-{name}", path=str(destination/filename), source_rect=crop,
                                           content_rect=(x+MARGIN_X, offset_y+heading+MARGIN_Y,
                                                         x+MARGIN_X+size[0], offset_y+heading+MARGIN_Y+size[1])))
                rows.append(pair)
                offset_y += pair.height
            sheet = Image.new("RGB", (max(row.width for row in rows), offset_y), "#101827")
            y = 0
            for row in rows:
                sheet.paste(row, (0,y))
                y += row.height
            sheet.save(temporary/filename)
            packet.views.extend(page_views)
            pages.append(str(destination/filename))
        (temporary/"packet.json").write_text(packet.model_dump_json(indent=2)+"\n")
        (temporary/"annotations.json").write_text(json.dumps(snapshot(document), indent=2)+"\n")
        (temporary/"schema.json").write_text(json.dumps(Batch.model_json_schema(), indent=2)+"\n")
        guide = (
            "You supply visual interpretation; LabelKit never runs inference.\n"
            "Open original.png first. Ruler numbers in grid/review PNGs are ORIGINAL EXIF-oriented pixel coordinates.\n"
            "x increases right; y increases down; bounds include width/height outer edges. Do not use resized viewer pixels.\n"
            "Edit annotations.json as a COMPLETE snapshot: omitted objects/classes are removed. Keep image_sha256 and base_revision.\n"
            "Each object needs a unique stable key (obj1, obj2...), label, and box [x1,y1,x2,y2] and/or polygon [[x,y],...].\n"
            "A polygon-only object gets its enclosing box automatically. Include both if the task requires a separately judged box.\n"
            "Trace ordered visible boundary vertices; no repeated first point, self-intersections, holes or disconnected parts.\n"
            "Objects never share identity just because labels match. Use note for uncertainty.\n"
            "Default coordinates are ORIGINAL pixels. Optional view_id selects a packet panel: then ALL that object's geometry\n"
            "uses actual PNG canvas pixels inside content_rect, not ruler numbers or a resized screenshot. --packet is required.\n"
            "Apply validates all objects together, preserving IDs for retained keys and rejecting stale revisions.\n"
            "Open overview and review pages after apply. Check missing/extra objects in the full image, clipped boundaries using\n"
            "the surrounding context, and class meaning. Correct a complete fresh snapshot with the returned revision.\n"
            "Use review --regions FILE for extra padded views: FILE is [{\"key\":\"detail1\",\"box\":[x1,y1,x2,y2]}].\n"
            "Do not read implementation source or write crop/CLI scripts; these commands supply those operations.\n"
            "Validation is geometric, not a claim of visual accuracy. Report unresolved uncertainty; do not claim pixel-perfect output.\n"
        )
        if short:
            guide = (
                "You supply visual interpretation; LabelKit never runs inference.\n"
                f"Open view.png: {image.width} x {image.height} EXIF-oriented source pixels, with no padding, grid or resizing.\n"
                "The top-left image corner is (0,0); x increases right and y down. Use source pixels, not resized viewer pixels.\n"
                "Submit task.json --file JSON_OR_- with classifications and objects as a COMPLETE snapshot.\n"
                "Each object needs a stable unique key, label, and box [x1,y1,x2,y2] and/or polygon [[x,y],...].\n"
                "A polygon alone gets its enclosing box. Trace ordered boundary vertices without repeating the first point.\n"
                "The task handle manages hashes and revisions; do not copy them into submissions.\n"
                "Open the returned view to review outlines in the same source coordinate frame. Keep all unchanged objects\n"
                "when correcting with the new task handle. If a response is lost, use task-status on the initial handle.\n"
                "Use note for uncertainty. Validation checks geometry, not visual accuracy.\n"
            )
        (temporary/"guide.txt").write_text(guide)
        for name, data in (extra_files or {}).items():
            if Path(name).name != name or (temporary/name).exists():
                raise ValueError("extra bundle files must use unique plain filenames")
            (temporary/name).write_text(json.dumps(data, indent=2)+"\n")
        if destination.exists() or destination.is_symlink():
            raise ValueError("view output appeared while rendering")
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {"output": str(destination), "packet": str(destination/"packet.json"),
            "original": str(destination/"original.png"), "overview": str(destination/"overview.png"),
            "grid": str(destination/"grid.png") if prepare else None, "review_pages": pages,
            "annotations_file": str(destination/"annotations.json"), "schema": str(destination/"schema.json"),
            "guide": str(destination/"guide.txt"), "revision": snapshot(document)["base_revision"],
            "image_sha256": document.image.sha256, "width": image.width, "height": image.height,
            "rules": rules.model_dump(mode="json"), "object_count": len(objects),
            "coordinates": "Original EXIF-oriented pixels by default. Read printed ruler values, not scaled viewer coordinates."}
