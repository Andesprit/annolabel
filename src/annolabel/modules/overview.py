"""Bounded contact-sheet pages for dataset audits; one page in memory at a time."""

from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from annolabel.schemas.annotations import Document
from annolabel.schemas.audit import AuditSource

COLS, ROWS = 4, 3
THUMB_W, THUMB_H = 320, 240
LINE, LINES = 17, 5
TILE_H = THUMB_H + LINES * LINE + 8
GAP, HEADER = 8, 48
PAGE_W = COLS * THUMB_W + (COLS + 1) * GAP
PAGE_MAX_H = HEADER + ROWS * (TILE_H + GAP)
COLORS = ["#f43f5e", "#00a6ff", "#f59e0b", "#22c55e", "#a855f7", "#06b6d4"]
STATUS = {"saved": "#4ade80", "empty": "#facc15", "unannotated": "#94a3b8", "error": "#f87171"}
FONT = ImageFont.load_default(size=13)


def _glyph(char: str) -> bytes:
    image = Image.new("L", (24, 24))
    ImageDraw.Draw(image).text((2, 2), char, font=FONT, fill=255)
    return image.tobytes()


@cache
def _drawable(char: str) -> bool:
    return _glyph(char) != _glyph("\U0010ffff")


def _plain(text: str) -> str:
    """Replace characters the built-in font cannot draw with '?' rather than blank boxes."""
    return "".join(c if c.isascii() or _drawable(c) else "?" for c in text)


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, *, left: bool = False) -> str:
    """Shorten with an ellipsis (from the left for paths) until it fits."""
    text = _plain(text)
    if draw.textlength(text, font=FONT) <= width:
        return text
    while text and draw.textlength("…" + text, font=FONT) > width:
        text = text[1:] if left else text[:-1]
    return "…" + text if left else text + "…"


def _wrap(draw: ImageDraw.ImageDraw, words: list[str], width: int, sep: str = "  ") -> list[str]:
    lines = [""]
    for word in map(_plain, words):
        joined = f"{lines[-1]}{sep}{word}" if lines[-1] else word
        if draw.textlength(joined, font=FONT) <= width or not lines[-1]:
            lines[-1] = joined
        else:
            lines.append(word)
    return lines


def _caption(draw: ImageDraw.ImageDraw, source: AuditSource) -> list[tuple[str, str]]:
    width = THUMB_W - 12
    more = f"see audit.json source #{source.id}"
    lines = [(_fit(draw, f"#{source.id} {source.image}", width, left=True), "white")]
    if source.status == "error":
        lines.append((source.code or "ERROR", STATUS["error"]))
        detail = _wrap(draw, " ".join(source.details or [source.error or ""]).split(), width, " ")
        lines += [(_fit(draw, line, width), "#fecaca") for line in detail[:2]]
        if len(detail) > 2:
            lines.append((more, "#cbd5e1"))
        return lines
    if source.status == "unannotated":
        return [*lines, ("NOT ANNOTATED (no sidecar)", STATUS["unannotated"])]
    count = len(source.objects)
    lines.append(
        (f"SAVED, {count} object{'s' * (count != 1)}", STATUS["saved" if count else "empty"])
    )
    keys = _wrap(draw, [f"{o.tag}={o.label}" for o in source.objects], width)
    if len(keys) > 2:
        keys = [*keys[:1], _fit(draw, f"+{count} total; {more}", width)]
    lines += [(_fit(draw, line, width), "#e2e8f0") for line in keys if line]
    scene = ", ".join(c.label for c in source.classifications)
    if scene:
        text = f"scene: {scene}"
        fitted = _fit(draw, text, width)
        lines.append((fitted if fitted == text else _fit(draw, f"scene: {more}", width), "#cbd5e1"))
    return lines[:LINES]


def _tile(source: AuditSource, image: Image.Image | None, document: Document | None) -> Image.Image:
    tile = Image.new("RGB", (THUMB_W, TILE_H), "#101827")
    draw = ImageDraw.Draw(tile)
    if image is not None:
        thumb = image.copy()
        thumb.thumbnail((THUMB_W - 8, THUMB_H - 8), reducing_gap=2.0)
        scale = thumb.width / image.width
        ox, oy = (THUMB_W - thumb.width) // 2, (THUMB_H - thumb.height) // 2
        tile.paste(thumb, (ox, oy))
        tags = {o.object_id: o.tag for o in source.objects}
        corners = {}
        for a in document.annotations if document else []:
            if a.object_id not in tags:
                continue
            color = COLORS[(tags[a.object_id] - 1) % len(COLORS)]
            points = [(ox + x * scale, oy + y * scale) for x, y in a.points]
            if a.kind == "box":
                draw.rectangle(points, outline=color, width=2)
            else:
                draw.line(points + points[:1], fill=color, width=2)
            corner = (min(x for x, _ in points), min(y for _, y in points))
            corners[a.object_id] = min(corners.get(a.object_id, corner), corner)
        for object_id, (x, y) in corners.items():
            tag = str(tags[object_id])
            box = draw.textbbox((0, 0), tag, font=FONT)
            x = max(0, min(x, THUMB_W - box[2] - 6))
            y = max(0, min(y - box[3] - 4, THUMB_H - box[3] - 4))
            draw.rectangle(
                (x, y, x + box[2] + 5, y + box[3] + 4), fill=COLORS[(int(tag) - 1) % len(COLORS)]
            )
            draw.text((x + 3, y + 1), tag, font=FONT, fill="white")
    else:
        draw.text(
            (12, THUMB_H // 2 - 8), "image could not be read", font=FONT, fill=STATUS["error"]
        )
    for row, (text, color) in enumerate(_caption(draw, source)):
        draw.text((6, THUMB_H + 4 + row * LINE), text, font=FONT, fill=color)
    status = "empty" if source.status == "saved" and not source.objects else source.status
    draw.rectangle((0, 0, THUMB_W - 1, TILE_H - 1), outline=STATUS[status], width=3)
    return tile


class OverviewPages:
    """Stream tiles into numbered PNG pages, keeping only the current page in memory.

    :param directory: Existing staging directory for page files.
    :param total: Number of sources that will be added.
    :param notice: Second header line repeated on every page (e.g. outstanding issues).
    """

    per_page = COLS * ROWS

    def __init__(self, directory: Path, total: int, notice: str) -> None:
        self.directory, self.total, self.notice = directory, total, notice
        self.page: Image.Image | None = None
        self.count = 0

    @staticmethod
    def name(number: int) -> str:
        """Page filename holding the 1-based source number."""
        return f"page-{(number - 1) // OverviewPages.per_page + 1:03d}.png"

    def add(
        self, source: AuditSource, image: Image.Image | None, document: Document | None
    ) -> None:
        """Draw one source into the current page and flush it when full."""
        slot = self.count % self.per_page
        if slot == 0:
            first, rows = self.count + 1, -(-min(self.per_page, self.total - self.count) // COLS)
            last = first + min(self.per_page, self.total - self.count) - 1
            pages = -(-self.total // self.per_page)
            self.page = Image.new("RGB", (PAGE_W, HEADER + rows * (TILE_H + GAP)), "#0b1020")
            draw = ImageDraw.Draw(self.page)
            draw.text(
                (GAP, 6),
                f"AnnoLabel audit page {(first - 1) // self.per_page + 1}/{pages}: sources #{first}-#{last} "
                f"of {self.total}. Thumbnails help spot problems; do not measure coordinates here. "
                "'?' marks a character this font cannot draw.",
                font=FONT,
                fill="white",
            )
            draw.text(
                (GAP, 26), _fit(draw, self.notice, PAGE_W - 2 * GAP), font=FONT, fill="#fde68a"
            )
        x = GAP + (slot % COLS) * (THUMB_W + GAP)
        y = HEADER + (slot // COLS) * (TILE_H + GAP)
        self.page.paste(_tile(source, image, document), (x, y))
        self.count += 1
        if self.count % self.per_page == 0 or self.count == self.total:
            self.page.save(self.directory / self.name(self.count))
            self.page = None
