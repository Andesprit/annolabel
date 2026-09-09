# Using LabelKit to annotate images

LabelKit supplies annotation operations. You, the vision-capable agent, supply the interpretation and geometry. Do not invoke inference models or external labeling services for this workflow.

## Recommended agent workflow

By default, prefer `task IMAGE --output NEW_DIR` followed by `submit TASK_JSON --file JSON_OR_-`. Open the returned coordinate view, submit all classifications/objects, open the returned view once, and optionally submit one corrected complete snapshot using the NEW task handle. Submit only `classifications` and `objects`; the handle manages hashes, revisions and output paths. The short view has exact oriented source dimensions with no padding, rulers, grid or resizing: image top-left is (0,0), x right, y down. Initial and review views share original pixel coordinates. Do not measure a resized viewer thumbnail. Check clipped contours, background inclusion, missed protrusions, object omissions and label meaning. Keep unchanged annotations in a correction. If output is lost, `task-status INITIAL_TASK_JSON` recovers the latest handle and view; do not reconstruct revisions or inspect implementation. Stop after the correction and report uncertainty. Each task allows at most two successful submissions; `--max-passes 1` permits only one. Export COCO normally.

## Detailed workflow when additional review is requested

Use this workflow for full packets and crop pages:

1. Run `uv run --project <LABELKIT_DIR> labelkit prepare <IMAGE> --output <NEW_PREP_DIR>`. Include `--instructions <RESEARCHER_TEXT_FILE>`, `--categories <OBJECT_LABELS_JSON>`, and `--geometry boxes|polygons|both` when supplied. The default is both; category restrictions concern object labels, not scene classifications.
2. Read the returned `guide` once and OPEN the `original` image with your image tool. Open `grid` only if useful. The packet includes oriented dimensions, absolute paths, rules, JSON schema, and an editable `annotations_file`. You do not need implementation source or custom crop/command scripts.
3. Edit the annotation JSON as a **complete snapshot**, retaining `image_sha256` and `base_revision`. Provide `classifications: [{"label":"scene","note":null}]` and `objects: [{"key":"obj1","label":"object","box":[x1,y1,x2,y2],"polygon":[[x,y],...],"note":null}]`. Choose stable unique object keys; LabelKit manages annotation IDs and links each pair automatically. Omitted objects/classes are removed. For segmentation, a polygon alone automatically gets its enclosing box. Do not equate equal labels with equal object identity.
4. Run `labelkit apply <IMAGE> --file <BATCH_JSON> --packet <PREP_DIR/packet.json> --output <NEW_REVIEW_DIR>`. All objects are validated and replaced together; no expand/update/shrink sequences. Output directories must be new. Failed edits preserve the sidecar. If the revision is stale, prepare the current state again and reconcile; do not blindly overwrite another edit.
5. OPEN the returned `overview` and each `review_pages` image. Review pages show padded original and annotated crops side by side, with labels outside the pixels. Check the full original/overview for missing or extra objects as well as boundaries and label meaning. Ruler numbers are **original EXIF-oriented pixels**, even in enlarged crops; do not measure a resized viewer thumbnail. For extra detail, use `review <IMAGE> --regions <REGIONS_JSON> --packet <PACKET> --output <NEW_DIR>`; regions are `[{"key":"detail1","box":[x1,y1,x2,y2]}]`. No custom Pillow scripts are needed.
6. Correct the complete snapshot in the latest returned `annotations_file` (it has the new revision), then apply again to a new review directory and inspect affected output. Prefer one deliberate correction pass; flag unresolved uncertainty instead of repeatedly redoing all crops. More passes may be appropriate when the research task specifically calls for them. Do not claim visual or pixel-perfect accuracy from validation alone.
7. Export with `labelkit export <IMAGE_OR_SOURCE_FOLDER> --output <NEW_DATASET_DIR>`; COCO remains the default. Report annotation, preview and export paths plus uncertainty.

Use original coordinates by default. Optional object `view_id` accepts actual PNG canvas coordinates for that exact packet view; supply its `--packet` and let LabelKit transform coordinates. Those coordinates must lie inside the view's `content_rect`, excluding rulers and padding. Do not mix ruler values with view-local coordinates. PNGs viewed at another scale require the original-coordinate rulers instead.

Keep a single writer per source image. Apply's revision check detects stale snapshots but is not a multi-process lock. Prepare/review do not annotate or infer boundaries, and task instructions cannot automatically verify visual correctness.

## Individual annotation commands

The existing low-level commands remain available for small edits and compatibility:

1. Run `uv run --project <LABELKIT_DIR> labelkit info <ABSOLUTE_IMAGE_PATH>`. Read the original oriented width/height and existing annotations.
2. Open and visually inspect the image with your image-viewing tool. If orientation or coordinate placement is unclear, run `render` with `--grid 100` into a separate previews directory and inspect that full-resolution file. The grid and image dimensions define the coordinate frame, even if your viewer scales the preview.
3. Apply the researcher’s labeling instructions. Use `label` for a whole-image class, `box --xyxy X1 Y1 X2 Y2` for a rectangle, or `polygon --points "[[x,y],...]"` for a traced segmentation boundary. All require `--label NAME`. Coordinates use the original EXIF-oriented pixels. Use `--note` for relevant uncertainty.
4. Read the returned JSON and retain both the annotation ID and object ID. When a box and polygon describe the same object, create the second geometry with `--object-id OBJECT_ID`; for existing pairs use `link --ids BOX_ID POLYGON_ID`. Each object has one label, at most one box and one polygon, and the box must enclose the polygon. Do not group objects merely because their labels match. Commands add annotations by default. To correct one, repeat its command with `--id ID` and the complete replacement geometry/label; use `remove --id ID` for an unwanted annotation. Do not duplicate already-correct annotations.
5. Run `render --output <PREVIEW.png>`, then OPEN that preview with your image-viewing tool. Check both label meaning and boundary placement. Correct errors and render again with `--force` until the result matches the instructions or flag what remains uncertain.
6. If requested, use `mask --id POLYGON_ID --output <MASK.png>` for a binary segmentation mask. It rasterizes your polygon; it does not infer a boundary.
7. Report the sidecar and preview paths and any uncertainties. Do not claim pixel-perfect accuracy from valid JSON alone.

For folders, enumerate original source images first and repeat per image. Keep previews and masks outside the source folder. Serialize edits to each image; do not run multiple writers against the same sidecar.

A basic example:

```sh
uv run --project <LABELKIT_DIR> labelkit box <IMAGE> --label car --xyxy 50 30 250 180
uv run --project <LABELKIT_DIR> labelkit polygon <IMAGE> --label leaf --points "[[25,40],[60,20],[90,45],[60,90]]"
uv run --project <LABELKIT_DIR> labelkit render <IMAGE> --output <PREVIEW.png>
```

Use `labelkit <COMMAND> --help` for full arguments. Inspect each image before choosing coordinates; example coordinates are not annotations for your image.

## Default training dataset export

Run `uv run --project <LABELKIT_DIR> labelkit export <IMAGE_OR_SOURCE_FOLDER> --output <NEW_DATASET_DIR>`. COCO is the default; no format flag is needed. Folder export discovers annotation sidecars recursively and excludes unannotated images without sidecars.

The export contains `annotations/instances_default.json` and oriented PNGs under `images/default/`. Each object has one record combining its box and polygon. Whole-image labels are preserved in `classifications.csv`, and original IDs and notes in `provenance.json`. Reuse the emitted `categories.json` via `--categories` when exporting separate splits, so category IDs match. Export does not invent a train/validation split.

Read legacy sidecars normally, but link known box/polygon pairs before export. Do not assume equal class names mean equal object identity.
