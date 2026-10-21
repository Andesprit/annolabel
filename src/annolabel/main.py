"""Machine-readable CLI for annotations supplied directly by an agent."""

import argparse
import json
import sys
from shlex import quote

from pydantic import ValidationError

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.export import export_dataset
from annolabel.core.inputs import parse_json, read_text
from annolabel.core.task import retry_tasks, start_task, submit_task, task_status
from annolabel.core.workflow import Workflow
from annolabel.modules.validation import InputError, validation_details


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        command = self.prog.partition(" ")[2]
        invocation = "annolabel" + (f" {command}" if command else "")
        raise InputError(
            "INVALID_ARGUMENTS",
            message,
            f"Run {invocation} --help and correct the named argument or flag.",
        )


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        description="Annotate images directly. No model calls. JSON results on stdout."
    )
    parser.add_argument("--version", action="version", version="annolabel 0.5.1")
    commands = parser.add_subparsers(dest="command", required=True)
    task = commands.add_parser(
        "task", help="Start a short annotation task with a coordinate view and saved handle"
    )
    task.add_argument("image", help="Source image or folder of source images")
    task.add_argument("--output", "-o", required=True)
    task.add_argument("--instructions")
    task.add_argument("--categories")
    task.add_argument("--geometry", choices=["boxes", "polygons", "both"], default="both")
    task.add_argument("--max-passes", type=int, choices=[1, 2], default=2)
    task.add_argument(
        "--keep-going",
        action="store_true",
        help="Folder only: start healthy images and record sources that fail for task-retry",
    )
    submit = commands.add_parser(
        "submit", help="Submit classifications and objects against a saved task handle"
    )
    submit.add_argument("task", help="Per-image task.json returned by task, submit, or task-status")
    submit.add_argument(
        "--file", required=True, help="JSON with classifications and objects only, or - for stdin"
    )
    submit.add_argument(
        "--allow-empty", action="store_true", help="Allow a snapshot to remove all saved objects"
    )
    status = commands.add_parser(
        "task-status", help="Recover an image task or report progress from a folder tasks.json"
    )
    status.add_argument("task", help="Image task.json or folder tasks.json")
    retry = commands.add_parser(
        "task-retry", help="Start only the folder sources that failed, with the original rules"
    )
    retry.add_argument("task", help="Folder tasks.json returned by task FOLDER --keep-going")
    prepare = commands.add_parser(
        "prepare", help="Create agent views, task rules, schema, and an editable snapshot"
    )
    prepare.add_argument("image")
    prepare.add_argument(
        "--output", "-o", required=True, help="New directory for images and task packet"
    )
    prepare.add_argument("--instructions", help="Researcher instructions in a text file")
    prepare.add_argument(
        "--categories", help="JSON array of allowed object labels (scene classes are separate)"
    )
    prepare.add_argument("--geometry", choices=["boxes", "polygons", "both"], default="both")
    apply = commands.add_parser(
        "apply", help="Atomically replace a complete annotation snapshot and create review views"
    )
    apply.add_argument("image")
    apply.add_argument(
        "--file",
        required=True,
        help="Complete batch JSON, or - for stdin; omitted objects are removed",
    )
    apply.add_argument(
        "--packet",
        help="Source-bound task packet; enforces categories/geometry and resolves view_id",
    )
    apply.add_argument(
        "--output", "-o", help="New review directory; defaults beside the batch file"
    )
    review = commands.add_parser(
        "review", help="Create padded original/annotated crop pages with original-coordinate rulers"
    )
    review.add_argument("image")
    review.add_argument("--output", "-o", required=True, help="New review directory")
    review.add_argument("--packet", help="Carry the prepared task rules forward")
    review.add_argument(
        "--regions", help="JSON array of {key, box:[x1,y1,x2,y2]} to inspect, even before labeling"
    )
    review.add_argument(
        "--per-page", type=int, default=2, help="Objects per paired review page (1..4)"
    )
    review.add_argument(
        "--padding",
        type=float,
        default=0.25,
        help="Surrounding context fraction (>0..2); minimum 16 pixels",
    )
    export = commands.add_parser("export", help="Export a training dataset (default: COCO)")
    export.add_argument("source", help="An image or folder containing annotation sidecars")
    export.add_argument("--output", "-o", required=True, help="New dataset directory")
    export.add_argument("--format", choices=["coco"], default="coco")
    export.add_argument(
        "--categories", help="Ordered JSON label list; reuse across exports for stable IDs"
    )
    for name, description in [
        ("info", "Read dimensions and current annotations"),
        ("label", "Add a whole-image classification"),
        ("box", "Add a bounding box in original pixel coordinates"),
        ("polygon", "Add a segmentation polygon in original pixel coordinates"),
        ("link", "Link existing box and polygon annotations into one object"),
        ("remove", "Remove an annotation by ID"),
        ("render", "Render a full-resolution annotated preview"),
        ("mask", "Export one polygon as a binary PNG mask"),
    ]:
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument("image", help="Path to the source image")
        if name in {"label", "box", "polygon"}:
            command.add_argument("--label", required=True)
            command.add_argument(
                "--id", help="Replace this existing annotation instead of adding one"
            )
            command.add_argument("--note", help="Optional rationale or uncertainty note")
        if name in {"box", "polygon"}:
            command.add_argument(
                "--object-id", help="Attach to an existing object ID returned by a shape command"
            )
        if name == "link":
            command.add_argument(
                "--ids",
                nargs="+",
                required=True,
                help="Annotation IDs to link; first object ID is retained",
            )
        if name == "box":
            command.add_argument(
                "--xyxy", nargs=4, type=float, required=True, metavar=("X1", "Y1", "X2", "Y2")
            )
        if name == "polygon":
            points = command.add_mutually_exclusive_group(required=True)
            points.add_argument("--points", help="JSON array: [[x,y],[x,y],[x,y],...]")
            points.add_argument("--points-file", help="File containing the JSON point array")
        if name in {"remove", "mask"}:
            command.add_argument("--id", required=True, help="Existing annotation ID")
        if name in {"render", "mask"}:
            command.add_argument("--output", "-o", required=True, help="PNG output path")
            command.add_argument(
                "--force", action="store_true", help="Replace an existing output file"
            )
        if name == "render":
            command.add_argument(
                "--grid",
                type=int,
                default=0,
                metavar="PIXELS",
                help="Draw coordinate grid at this spacing (0 disables)",
            )
    return parser


def _payload(args: argparse.Namespace) -> str:
    text = sys.stdin.read() if args.file == "-" else read_text(args.file, "--file")
    source = "--file - (stdin)" if args.file == "-" else f"--file {args.file!r}"
    parse_json(text, source)
    return text


def _recovery(args: argparse.Namespace) -> str:
    image = quote(getattr(args, "image", getattr(args, "source", "IMAGE")))
    info = f"Run annolabel info {image}"
    return {
        "task": "Correct the named task input and retry annolabel task with a new --output directory.",
        "submit": 'Correct all listed fields in --file and retry the same task handle with a complete snapshot; use "classifications": [] when there are none.',
        "task-retry": "Pass the folder tasks.json returned by annolabel task FOLDER --keep-going; fix the named source first.",
        "task-status": "Pass the task.json returned by annolabel task, submit, or task-status; restore any missing checkpoint files.",
        "prepare": "Correct the named input or rule and retry annolabel prepare with a new --output directory.",
        "apply": "Correct the complete snapshot in --file and retry; for a stale revision, run annolabel prepare IMAGE --output NEW_DIR and reconcile with its annotations.json.",
        "review": "Correct --regions, --packet, --per-page (1..4) or --padding (>0..2) as named, then retry with a new --output directory.",
        "export": "Correct the named source or --categories vocabulary, then retry annolabel export SOURCE --output NEW_DATASET; use a new output directory.",
        "info": "Check the image path and the named .labels.json sidecar; restore the original image or repair the sidecar before retrying annolabel info.",
        "label": f"{info} to read current annotations; pass a nonempty --label and an existing --id when replacing one.",
        "box": f"{info} for dimensions and IDs; correct --xyxy X1 Y1 X2 Y2 so 0 <= X1 < X2 <= width and 0 <= Y1 < Y2 <= height. Use an existing --id/--object-id when supplied.",
        "polygon": f"{info} for dimensions and IDs; correct --points or --points-file to a JSON array of at least three distinct boundary points inside the image; do not repeat the first point. Use an existing --id/--object-id when supplied.",
        "link": f"{info} and pass --ids with at least two distinct existing box/polygon annotation IDs sharing a label and enclosing geometry.",
        "remove": f"{info} and pass --id with the existing annotation ID to remove.",
        "render": "Pass a new .png --output path (or --force to replace a preview) and --grid >= 0; keep output separate from the source image and sidecar.",
        "mask": f"{info} and pass --id for an existing polygon; use a new .png --output path (or --force to replace a mask).",
    }[args.command]


def _failure(error: OSError | ValueError, args: argparse.Namespace | None) -> dict:
    if isinstance(error, InputError):
        result = {"error": str(error), "code": error.code, "recovery": error.recovery}
        if error.details is not None:
            result["details"] = error.details
        return result
    recovery = _recovery(args)
    source = getattr(args, "image", getattr(args, "source", getattr(args, "task", "input")))
    if isinstance(error, ValidationError):
        return {
            "error": f"Invalid {args.command} input for {source!r}.",
            "code": "VALIDATION_ERROR",
            "recovery": recovery,
            "details": validation_details(error),
        }
    if isinstance(error, FileExistsError):
        path = error.filename or getattr(args, "output", "output")
        return {
            "error": f"Output already exists: {path!r}.",
            "code": "OUTPUT_EXISTS",
            "recovery": "Pass a new --output path; use --force only when replacing an existing preview or mask.",
        }
    if isinstance(error, OSError):
        path = error.filename or source
        reason = error.strerror or (
            str(error) if error.errno is None else "file is unavailable or is not a readable image"
        )
        return {
            "error": f"Cannot access {path!r}: {reason}.",
            "code": "IO_ERROR",
            "recovery": f"Check that {path!r} exists and is readable/writable as needed. {recovery}",
        }
    return {
        "error": f"{args.command} for {source!r}: {error}",
        "code": "VALIDATION_ERROR",
        "recovery": recovery,
    }


def main() -> None:
    """Run one operation; failures exit with status 2."""
    args = None
    try:
        args = _parser().parse_args()
        if args.command in {"task", "submit", "task-status", "task-retry"}:
            if args.command == "task":
                result = start_task(
                    args.image,
                    args.output,
                    instructions=args.instructions,
                    categories=args.categories,
                    geometry=args.geometry,
                    max_passes=args.max_passes,
                    keep_going=args.keep_going,
                )
            elif args.command == "submit":
                payload = _payload(args)
                result = submit_task(args.task, payload, allow_empty=args.allow_empty)
            elif args.command == "task-retry":
                result = retry_tasks(args.task)
            else:
                result = task_status(args.task)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
            return
        if args.command in {"prepare", "apply", "review"}:
            workflow = Workflow(args.image)
            if args.command == "prepare":
                result = workflow.prepare(
                    args.output,
                    instructions=args.instructions,
                    categories=args.categories,
                    geometry=args.geometry,
                )
            elif args.command == "apply":
                payload = _payload(args)
                result = workflow.apply(
                    payload,
                    output=args.output or workflow.default_review_output(args.file),
                    packet_path=args.packet,
                )
            else:
                result = workflow.review(
                    args.output,
                    packet_path=args.packet,
                    regions=args.regions,
                    per_page=args.per_page,
                    padding=args.padding,
                )
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return
        if args.command == "export":
            result = export_dataset(args.source, args.output, args.categories)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return
        kit = AnnoLabel(args.image)
        if args.command == "info":
            result = kit.info()
        elif args.command in {"label", "box", "polygon"}:
            points = []
            if args.command == "box":
                points = [args.xyxy[:2], args.xyxy[2:]]
            elif args.command == "polygon":
                if args.points_file:
                    points = parse_json(
                        read_text(args.points_file, "--points-file"),
                        f"--points-file {args.points_file!r}",
                    )
                else:
                    points = parse_json(args.points, "--points")
            result = kit.annotate(
                args.command,
                args.label,
                points,
                args.id,
                args.note,
                getattr(args, "object_id", None),
            )
        elif args.command == "link":
            result = kit.link(args.ids)
        elif args.command == "remove":
            result = kit.remove(args.id)
        else:
            grid = getattr(args, "grid", 0)
            if grid < 0:
                raise ValueError("grid spacing must be nonnegative")
            result = kit.export_image(
                args.output,
                grid=grid,
                annotation_id=args.id if args.command == "mask" else None,
                force=args.force,
            )
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError) as error:
        print(json.dumps(_failure(error, args), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
