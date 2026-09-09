"""Machine-readable CLI for annotations supplied directly by an agent."""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from annolabel.core.annolabel import AnnoLabel
from annolabel.core.export import export_dataset
from annolabel.core.task import Task, TaskError, start_task
from annolabel.core.workflow import Workflow


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Annotate images directly. No model calls. JSON results on stdout."
    )
    parser.add_argument("--version", action="version", version="annolabel 0.5.1")
    commands = parser.add_subparsers(dest="command", required=True)
    task = commands.add_parser(
        "task", help="Start a short annotation task with a coordinate view and saved handle"
    )
    task.add_argument("image")
    task.add_argument("--output", "-o", required=True)
    task.add_argument("--instructions")
    task.add_argument("--categories")
    task.add_argument("--geometry", choices=["boxes", "polygons", "both"], default="both")
    task.add_argument("--max-passes", type=int, choices=[1, 2], default=2)
    submit = commands.add_parser(
        "submit", help="Submit classifications and objects against a saved task handle"
    )
    submit.add_argument("task", help="task.json returned by task, submit, or task-status")
    submit.add_argument(
        "--file", required=True, help="JSON with classifications and objects only, or - for stdin"
    )
    status = commands.add_parser(
        "task-status", help="Recover the latest task handle and view after lost output"
    )
    status.add_argument("task")
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


def main() -> None:
    """Run one operation; failures exit with status 2."""
    args = _parser().parse_args()
    try:
        if args.command in {"task", "submit", "task-status"}:
            if args.command == "task":
                result = start_task(
                    args.image,
                    args.output,
                    instructions=args.instructions,
                    categories=args.categories,
                    geometry=args.geometry,
                    max_passes=args.max_passes,
                )
            elif args.command == "submit":
                payload = (
                    sys.stdin.read()
                    if args.file == "-"
                    else Path(args.file).expanduser().read_text()
                )
                result = Task(args.task).submit(payload)
            else:
                result = Task(args.task).status()
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
                payload = (
                    sys.stdin.read()
                    if args.file == "-"
                    else Path(args.file).expanduser().read_text()
                )
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
                    points = json.loads(Path(args.points_file).expanduser().read_text())
                else:
                    points = json.loads(args.points)
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
        result = {"error": str(error)}
        if args.command in {"task", "submit", "task-status"}:
            result["code"] = (
                error.code
                if isinstance(error, TaskError)
                else ("VALIDATION_ERROR" if isinstance(error, ValueError) else "IO_ERROR")
            )
            result["recovery"] = (
                error.recovery
                if isinstance(error, TaskError)
                else (
                    "Correct the submitted JSON or path and retry the same command. Do not inspect implementation or reconstruct hashes."
                )
            )
            if isinstance(error, ValidationError):
                result["error"] = "Invalid task or submission JSON."
                result["details"] = error.errors(
                    include_url=False, include_context=False, include_input=False
                )[:3]
        if args.command in {"prepare", "apply", "review"}:
            result["code"] = "VALIDATION_ERROR" if isinstance(error, ValueError) else "IO_ERROR"
            if isinstance(error, ValidationError):
                result["details"] = error.errors(
                    include_url=False, include_context=False, include_input=False
                )
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
