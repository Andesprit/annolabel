# Using AnnoLabel

[Install and quickstart](../README.md) · [Compatibility](compatibility.md)

## Use with an agent

### Recommended workflow: task → submit → review → optional correction

1. Start a task for one image with `annolabel task`. The reply names the `task` handle, the `view`, the `guide`, the editable `submission` file and a `next` step to follow.
2. Read the `guide` (it holds the researcher instructions, allowed object labels and geometry rule) and open the `view` with the agent's image tool.
3. Edit the `submission` file into a complete snapshot of all scene labels and objects, then run the `submit` command named in `next`.
4. Open the returned review `view` and check labels, omissions, boxes, and silhouettes.
5. Only if necessary, edit the returned `submission` and submit one corrected complete snapshot using the **new task handle**. Report unresolved uncertainty.
6. Export the image or its source folder as COCO; after the last pass, `next` gives the export command.

Task views have the exact EXIF-oriented source dimensions: **no padding, rulers, grid, or resizing**. The original and reviewed images share one pixel coordinate frame. Thin annotation outlines are added for review; labels stay in JSON.

### Copyable agent prompt

Replace the paths and researcher instructions below. Supply the prompt to Codex, Claude Code, Gemini/Antigravity, or your preferred vision-capable agent:

```text
Use the installed AnnoLabel CLI to label
/absolute/path/to/images/photo.jpg.

Researcher task: classify the scene and label every prominent foreground
object with a tight bounding box and a polygon tracing its visible silhouette.
Exclude tiny background objects. Describe appearance and note uncertain identity.

Run commands with:
annolabel

Create a task in /absolute/path/to/work/photo-task with --geometry both.
Read the returned guide and open the returned view with your image tool. Use
its original pixel dimensions: (0,0) is the top-left, x increases right and
y increases down.

Edit the returned submission file into one complete snapshot of classifications
and objects, keeping any saved objects' keys, and run the submit command named
in next. Use stable, unique object keys. A polygon alone can derive its enclosing bounding box.
Open the returned view once and check missing objects, wrong labels, clipped
silhouettes, excess background, and coordinate shifts.

If needed, make at most one correction using the NEW returned task handle,
retaining every unchanged object and class. Stop after that correction and
report uncertainty. If output is lost, use task-status on the initial handle.

Use your own vision. Do not call detectors or segmentation models, inspect
implementation code, or write crop/CLI helper scripts. AnnoLabel supplies those
operations. Export COCO to /absolute/path/to/work/photo-coco and report paths.
```

For reusable instructions, point your agent at [AGENTS.md](../AGENTS.md) and [agent-prompt.md](agent-prompt.md). Explicitly ask it to read these files when working outside the checkout. An agent-specific integration or MCP server is not required.

### Supply research instructions and categories

```sh
annolabel task /data/images/photo.jpg \
  --output /data/work/photo-task \
  --instructions /data/labeling-instructions.txt \
  --categories /data/categories.json \
  --geometry both
```

`--instructions` reads a plain-text file and stores it in the task. The task's `guide.txt` repeats the instructions, the allowed object labels (or says that labels are unrestricted), the scene-classification distinction and the geometry rule, so an agent given only the task reply can find them. Free-form text cannot automatically enforce semantic choices such as which objects are prominent or how an occluded object should be labeled.

`--categories` reads a JSON array of allowed **object labels**, for example `["car", "person", "bicycle"]`. Scene classifications are independent. Category membership and geometry constraints are checked on submission.

## Submission format and coordinates

Each task directory contains `submission.json`, a ready-to-edit complete snapshot in this format, and `schema.json`, its JSON schema. It starts as the saved state at that checkpoint, so an image that already has labels starts with all its objects, keys, geometry, notes and scene labels; change what is wrong and keep the rest. It is a **working copy**: edits there are unsaved until `submit` succeeds, and editing it never changes saved annotations or the task's internal checkpoint. `task-status` reports saved counts and handles; it does not read or reset this file. Do not edit `annotations.json`: it is the task's internal checkpoint.

`submit` accepts a UTF-8 JSON file or stdin with `--file -`. Both top-level arrays are required, even when empty:

```json
{
  "classifications": [{"label": "outdoor", "note": null}],
  "objects": [
    {
      "key": "object-1",
      "label": "car",
      "box": [100, 80, 300, 220],
      "polygon": [[100, 140], [140, 80], [280, 80], [300, 220], [100, 220]],
      "note": "Identity is clear; far-side boundary is occluded."
    }
  ]
}
```

| Field | Meaning |
|---|---|
| `classifications` | Whole-image labels, each with `label` and optional `note`. |
| `objects` | All objects to retain in this image. |
| `key` | Stable, unique object identity across correction passes. |
| `label` | Object class or descriptive visual label. |
| `box` | `[x1, y1, x2, y2]` in original oriented pixels. |
| `polygon` | Ordered boundary vertices `[[x, y], ...]`; the closing edge is automatic. |
| `note` | Optional uncertainty or labeling rationale. |

Rules:

- Use finite pixel coordinates with `0 <= x <= width` and `0 <= y <= height`. Width and height represent outer edges; the last raster pixel index is one less.
- Boxes require `x1 < x2` and `y1 < y2`. A provided box must contain its polygon.
- Polygons require at least three distinct vertices, nonzero area, and no self-intersections. Do not repeat the first vertex at the end. Either winding direction is accepted.
- A polygon-only object automatically receives its enclosing box in the batch workflows.
- `--geometry both` and `--geometry polygons` require a polygon for each submitted object. `--geometry boxes` permits box-only objects; it does not forbid polygons.
- Classifications-only submissions use `objects: []`. The geometry mode does not require objects to exist.
- Keys identify individual objects. Two objects with the same label need different keys.
- Do not include hashes, revision strings, annotation IDs, or output paths in `submit` JSON. The task handle manages them.
- **A submission replaces the entire image snapshot.** Omitted objects and scene labels are removed. Retain unchanged objects/classes in every correction.

An image viewer may scale a large PNG. Always reason in the original dimensions returned by `task`; coordinates measured from a resized screenshot are not automatically corrected.

## Review and correct annotations

A successful submission returns a compact receipt resembling (paths shortened):

```json
{"status":"saved","task":"/data/work/photo-task/pass1/task.json","view":"/data/work/photo-task/pass1/view.png","objects":2,"passes_left":1,"changes":{"added":["car1","car2"],"changed":[],"removed":[]},"guide":".../pass1/guide.txt","submission":".../pass1/submission.json","next":"Open .../pass1/view.png to review. If a correction is needed, edit .../pass1/submission.json and run annolabel submit .../pass1/task.json --file .../pass1/submission.json; otherwise stop and run annolabel export ... --output NEW_DATASET."}
```

Open that `view`, then submit a corrected full snapshot only if needed. The new checkpoint's `submission` file already holds what was just saved:

```sh
annolabel submit /data/work/photo-task/pass1/task.json \
  --file /data/work/photo-task/pass1/submission.json
```

When `passes_left` is 0, `next` says to stop labeling, report uncertainty and export.

Use the returned handle, rather than guessing paths. A second submission returns `passes_left: 0`. By default each task allows two successful submissions. Use `--max-passes 1` when creating a task to permit only one. Invalid submissions do not consume a pass.

The `changes` field lists added, changed, and removed object keys compared with the submitted handle's saved snapshot. Changes include label, geometry, or note edits. Receipts also include `warnings` when a snapshot removes saved objects, listing their keys. Keep unchanged objects in a correction: omitted objects are deleted.

A snapshot that would remove **every** previously saved object fails with `OBJECTS_REMOVED`, without writing files or consuming a pass. If the empty result is intentional, retry the same handle and complete snapshot with `--allow-empty`:

```sh
annolabel submit /data/work/photo-task/pass1/task.json \
  --file /data/work/empty-labels.json --allow-empty
```

An empty first submission is allowed when the handle's snapshot has no objects. A task started on an already annotated image still protects those saved objects, even on its first submission. The flag does not bypass validation, revision checks, or the pass limit. Identical retries replay their saved receipt even if `--allow-empty` is omitted on the retry.

### Preview a risky correction (optional)

Before spending a pass on a correction that removes or reshapes objects, you can look at it first. `preview` runs the same checks as `submit` (rules, labels, geometry, current source state, removal guard), then draws the candidate. It never saves, never uses a pass and never creates a task handle:

```sh
annolabel preview /data/work/photo-task/pass1/task.json \
  --file /data/work/photo-task/pass1/submission.json \
  --output /data/work/preview-1
```

The reply has `status: "unsaved"`, the prospective `changes` and removal `warnings`, `passes_left` (unchanged), and:

- `view`: the candidate outlines on the source-sized view, as the saved view will look after submit. Compare it with `saved_view`.
- `changes_view`: only when objects would be removed. Dashed white outlines labelled `REMOVED` show saved objects the candidate deletes. They are a diagnostic, not kept annotations. It is `null` when nothing is removed.
- `candidate`: a read-only `candidate.json` holding exactly the validated snapshot shown, in original pixels.
- `submit`: the exact ordinary submit command for the same task handle and `candidate.json`, with `--allow-empty` when the candidate removes every object (preview needs the same flag for that case).

To save, run the `submit` command. Submit checks everything again and refuses with `TASK_CONFLICT` if the image or labels changed after the preview. To change the proposal, edit the task's `submission.json` and preview again to a NEW `--output`. A used handle returns `CHECKPOINT_USED` (run `task-status` for the latest handle) and a handle with no passes left returns `PASS_LIMIT`. The output must be new and outside any task, pass, folder-task, audit or preview directory (`OUTPUT_RESERVED`). Preview folders are marked by `preview.json` and are never counted as source images.

If a command response is lost, recover the latest committed handle, view, guide, working copy and next step:

```sh
annolabel task-status /data/work/photo-task/task.json
```

Task directories created before working copies existed have no `submission.json`; their replies omit `guide`, `submission` and `next`, and they remain readable and submittable as before. Old files are never rewritten. To get the new files for such a checkpoint, start a fresh task on the current source with the rules you want (`annolabel task IMAGE --output NEW_DIR ...`); it starts from the saved labels with a fresh pass budget.

An identical retry against the same checkpoint replays its saved receipt while its saved revision remains current. A different payload against a consumed checkpoint requires the next handle. If another writer has changed the source or labels, stop and reconcile the change before creating a new task.

The pass limit applies to this task chain. It is not a limit on agent tool calls and does not prevent an agent from creating another task. State the desired review budget in your prompt.

## Classifications, boxes, and polygons

### Whole-image classification

For a single class addition:

```sh
annolabel label /data/images/photo.jpg --label indoor \
  --note "Scene classification"
```

For complete snapshot replacement through a task, use:

```json
{"classifications":[{"label":"indoor"}],"objects":[]}
```

The latter removes any existing object annotations. Use the individual `label` command when you want to preserve them.

### Bounding boxes only

```sh
annolabel task /data/images/photo.jpg \
  --output /data/work/boxes-task --geometry boxes
```

Ask the agent to supply boxes without polygons:

```json
{"classifications":[],"objects":[{"key":"car-1","label":"car","box":[50,30,250,180]}]}
```

Boxes need fewer coordinate values than polygons. Actual labeling time and accuracy depend on the agent and image; AnnoLabel does not promise a fixed number of model calls.

### Segmentation polygons

Use the default task mode and submit ordered silhouette vertices. Transparent objects, occlusion, small objects, and fine boundaries need an explicit researcher policy. AnnoLabel stores the polygon you supply; it does not infer missing details.

Export one stored polygon as a binary PNG mask:

```sh
annolabel info /data/images/photo.jpg
# Copy a polygon's annotation id from the response, not its object_id.
annolabel mask /data/images/photo.jpg \
  --id POLYGON_ANNOTATION_ID --output /data/work/object-mask.png
```

Masks contain 0 for background and 255 for foreground. PNG masks use Pillow rasterization; edge pixels may differ from COCO's polygon rasterization convention.

## Label folders and batch agent work

Start a folder once; AnnoLabel creates an ordinary task for each image and a `tasks.json` index:

```sh
annolabel task /data/images --output /data/work/image-tasks
annolabel task-status /data/work/image-tasks/tasks.json
```

1. Follow `next`: it names the pending image's guide, view and working copy and the exact `submit` command for its per-image handle (`next_task`). For example: `annolabel submit /data/work/image-tasks/000001/task.json --file /data/work/image-tasks/000001/submission.json`. `annolabel task-status` on that per-image handle returns the same context.
2. Open the saved view and, if needed, correct it using the new handle returned by `submit`. Each image retains the usual pass limit, replay behavior and removal guards.
3. Run `task-status` on the folder index whenever you need progress or lose a reply. It follows each image's checkpoint chain, including corrections, and names the next pending handle.
4. When all images are saved, status returns the export command: `annolabel export /data/images --output NEW_DATASET`. Replace `NEW_DATASET` with a new output directory.

Status lists each source image with its latest `task`, `view`, `state` (`saved`, `pending`, or `error`), `objects` and `passes_left`, plus `totals` for images, saved and pending. Saved means a snapshot was submitted in this folder task, including an intentionally empty one. Pending totals include unresolved error entries. If a checkpoint is missing or damaged, its entry contains `error`, `code` and `recovery`; healthy entries remain available, and the overall status is `attention`. Resolve those errors before exporting. Passing the folder index to `submit` returns `IMAGE_TASK_REQUIRED` and directs you to a per-image handle.

### Keep going when a source image fails

By default, a folder start is all-or-nothing: if any source cannot be opened (for example a corrupt file or a sidecar that does not match its image), it returns that source's error, removes the new output directory and suggests `--keep-going`. Opt in to start the healthy images anyway:

```sh
annolabel task /data/images --output /data/work/image-tasks --keep-going
# label the healthy images by following next; fix or replace the named file at the same path, then:
annolabel task-retry /data/work/image-tasks/tasks.json
```

- Every discovered source keeps its numbered slot. A source that could not start appears in the start reply and in status with `state: error`, `error`, `code` and a `recovery` naming the `task-retry` command. It counts in `totals` (images and pending), never as saved.
- **Exit code 0 does not mean every image is ready.** The start and status replies use `status: attention` while any source failed; check `status` and the per-image `state` before treating the folder as complete. Status offers the export command only when every source is saved.
- While healthy images remain pending, `next` leads to the next image's guide and submit command and also names the unresolved sources. Once they are saved, `next` points to fixing and retrying, not to export.
- `task-retry` starts only the sources that failed at start, into their reserved slots, with the instructions, categories, geometry and pass limit frozen in `tasks.json` at start. Changing or deleting the original rule files later does not matter. It never restarts, overwrites or resets a started or saved task, and does not enroll files added to the folder later. A retry that starts nothing says so; a source that is still broken stays listed with its error.
- `task-retry` writes a small record under `retries/` before publishing a slot. If a retried task directory later goes missing, status reports `TASK_MISSING`: restore the directory instead of regenerating it. Only if `task-retry` was interrupted before it published the slot, delete the named record and run `task-retry` again.
- Rule-file mistakes (`--instructions`, `--categories`) and output-directory failures are not per-image problems: they stop the whole start once, before any output is created.

Folder start discovers files recursively by extension: `.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`, `.tif`, `.tiff` and `.gif`, case-insensitively. Multi-frame images remain unsupported. Sources are sorted by resolved path and symlink aliases are deduplicated. Task directories are numbered `000001`, `000002`, and so on; the index maps each number to its source, so nested files and equal stems cannot collide. The index fixes the image list at creation.

The new output directory and existing AnnoLabel bundles marked by `packet.json`, `tasks.json`, `audit.json`, `preview.json` or `annotations/instances_default.json` are excluded from discovery. **Keep unmarked loose previews and masks outside the source folder**: they look like source images. Folder export uses annotation sidecars, rather than image extensions, and includes only images with saved sidecars. An empty folder or one containing no supported source images returns `NO_IMAGES` with recovery instructions.

An agent may group two independent submissions into one shell call:

```sh
annolabel submit /data/work/image-tasks/000001/task.json --file /data/work/image-a-labels.json
annolabel submit /data/work/image-tasks/000002/task.json --file /data/work/image-b-labels.json
```

These are separate image operations, not one atomic multi-image transaction. Check each command's result: the final shell exit status alone does not establish that both succeeded. Keep **one writer per source image**; concurrent agents can work on different images. Avoid stitching images unless your calling system also handles the coordinate mapping back to each source. AnnoLabel does not implement LLM batching or image-stitching inference.

## Check a folder before export (optional)

```sh
annolabel audit /data/images --output /data/work/audit-1
```

`audit` is a read-only look at the whole source folder. It does not write sidecars, create tasks, spend passes or change what export includes. The reply gives `status` (`attention` when any image is unannotated, unreadable or has a bad sidecar), `totals`, `object_labels`, `scene_classes`, every `issue` with its recovery step, and the paths of `audit.json` and the overview pages. Open every page.

- **Every eligible source is listed once** (same discovery as `task FOLDER`). Its status is `saved`, `unannotated` or `error`. Error codes are `IMAGE_UNREADABLE`, `SIDECAR_INVALID` (the sidecar cannot be read) and `SIDECAR_STALE` (the image changed after labeling). Saved images with zero objects are shown as `SAVED, 0 objects`; that does not say whether the image is really empty.
- **Orphan sidecars** (`ORPHAN_SIDECAR`): a `.labels.json` whose image is missing or is not a supported source image. Folder export fails while one is present; the page header says when there are any.
- **Labels are counted literally.** `Rectangle` and `rectangle` stay separate; nothing is merged. A linked box and polygon count as one object. Scene classes are counted apart from object labels. Notes and IDs are kept per object in `audit.json`.
- **Pages** hold up to 12 sources (4 x 3 tiles, at most 1320 x 1071 pixels) and are written one at a time. Each tile shows a thumbnail, numbered object outlines with a `number=label` key, the status and the scene classes. When a caption is too long it points to `audit.json source #N`; `?` marks a character the page font cannot draw. Thumbnails help you notice mistakes; do not measure coordinates on them.
- **Actions** in `audit.json` are exact commands with quoted paths: `review` (detailed single-image review), `label` for an unannotated image and `correct` for a saved one. Both run `task`; `correct` starts a NEW task with fresh passes from the saved labels and does not resume an earlier handle. Replace `NEW_..._DIR` with a directory that does not exist.
- **Noted items** (`notes.json`, count in `noted_items`): every saved object and scene class with a nonblank note, in one short list. Each item has the literal note text, the source, the label and its IDs (`object_id`, `annotation_ids`). A linked box and polygon are one item, and the same note text is listed once. An object item has a quoted `review` action that enlarges its saved area (its region file is in `notes/`); open `review-001.png`, the crop titled with the item's region key. Other shapes inside that crop are drawn too. A scene class item reviews the whole image; open its `overview.png`. A note is just researcher text, maybe bookkeeping. It is not an uncertainty or review status, and items without a note are not reviewed or correct. Actions load the current labels; each item records `image_sha256` and `annotation_revision` from audit time, so audit again after changes.
- **Object vocabulary** (optional `--categories FILE`, a JSON array like for `task` and `export`): `audit.json` `vocabulary_mismatches` lists every saved object whose label is not in the list, once per object (a linked box and polygon is one), with its source, literal label, `object_id`, `annotation_ids`, `review` action and a `correct` action. Matching is case-sensitive. Scene classes are never checked. `[]` allows no object label; leaving out `--categories` checks nothing. The audit copies the exact list to `categories.json` in its output, and `correct` uses that copy with `--geometry boxes` (a box or a polygon is enough), so later edits to your own file do not change it. Any mismatch makes `status` `attention`. No mismatches does not mean the labels are correct or follow your written instructions.

The report describes the folder when it ran. `saved` does not mean reviewed, correct or ready for training. After fixing something, audit again to a new `--output`. Audit outputs are marked by `audit.json` and are never counted as source images, even inside the source folder. For one image, use `annolabel review IMAGE --output NEW_DIR` instead.

## Export COCO for training

```sh
annolabel export /data/images --output /data/datasets/labeled
```

COCO is currently the only built-in training format; `--format coco` is optional. Folder export discovers `*.labels.json` recursively and excludes images without sidecars. Exporting a single image without a sidecar fails with `NO_ANNOTATIONS`: run `task` and `submit` first. An existing sidecar with zero objects still exports, including any whole-image classifications; the JSON receipt includes a `warnings` entry naming each zero-object image. For an intentionally empty image, submit a snapshot with `"objects": []` before exporting.

```text
labeled/
├── annotations/
│   └── instances_default.json
├── images/
│   └── default/
│       ├── 000001.png
│       └── 000002.png
├── categories.json
├── classifications.csv
├── provenance.json
└── README.txt
```

- Point a COCO loader at `annotations/instances_default.json` and use **`images/default/` as its image root**. COCO `file_name` values are relative to that root.
- Each object exports as one record combining its box and optional polygon.
- COCO boxes are `[x, y, width, height]`; AnnoLabel converts them from its input `[x1, y1, x2, y2]` convention.
- Polygons export as COCO polygon segmentations with `iscrowd: 0`. Box-only records omit segmentation.
- Area is continuous polygon area when available, otherwise box area.
- Images export as PNGs in their EXIF-oriented annotation coordinate frame.
- Whole-image labels are stored in `classifications.csv`, since COCO instances do not represent scene classifications.
- `provenance.json` preserves original annotation IDs, object IDs, notes, hashes, and source paths. Account for those source paths when sharing an exported dataset.

### Keep category IDs stable across splits

Prepare a complete ordered vocabulary shared by all splits, such as `["car", "person", "bicycle"]`, then reuse it:

```sh
annolabel export /data/train --output /data/datasets/train \
  --categories /data/categories.json
annolabel export /data/validation --output /data/datasets/validation \
  --categories /data/categories.json
```

IDs start at 1 in the supplied order. Without a vocabulary, categories are sorted from the labels present in that export. You can reuse an emitted `categories.json`, provided it includes every label used by the later split. Missing labels are rejected. AnnoLabel does not invent train/validation/test splits.

For another format, use this COCO export as the interchange dataset for your training system or conversion tool. Conversion can only preserve information represented by its target format; whole-image classes and notes are separate files here.

## Detailed reviews and individual commands

For larger review budgets, `prepare` / `apply` / `review` expose the full packet, revision snapshot, coordinate grid, and paired original/annotated crops:

```sh
annolabel prepare /data/images/photo.jpg \
  --output /data/work/prepared --instructions /data/instructions.txt
# Open original.png and read guide.txt; edit the returned annotations.json.
# Keep image_sha256 and base_revision when using apply.
annolabel apply /data/images/photo.jpg \
  --file /data/work/prepared/annotations.json \
  --packet /data/work/prepared/packet.json --output /data/work/review-1
annolabel review /data/images/photo.jpg \
  --packet /data/work/review-1/packet.json \
  --output /data/work/extra-review --per-page 2 --padding 0.25
```

`apply` accepts the full snapshot including `image_sha256` and `base_revision`; `submit` accepts only classifications and objects. Do not interchange these two JSON contracts.

Detailed views have padding and original-coordinate rulers. Read the ruler values for source coordinates. An advanced object `view_id` can instead select actual PNG canvas coordinates for a specific packet panel; the packet maps its `content_rect` into its `source_rect`. This requires the matching packet. Do not mix ruler values, canvas positions, and resized screen coordinates.

For an arbitrary detail, write a region file such as `[{"key":"detail-1","box":[100,80,300,220]}]`, then:

```sh
annolabel review /data/images/photo.jpg \
  --regions /data/work/regions.json --output /data/work/detail-review
```

Small individual additions are also available:

```sh
annolabel box /data/images/photo.jpg --label car --xyxy 50 30 250 180
annolabel polygon /data/images/photo.jpg --label leaf \
  --points '[[25,40],[60,20],[90,45],[60,90]]'
annolabel render /data/images/photo.jpg --output /data/work/preview.png
```

These commands add annotations by default. To edit an existing one, use its `--id` with the complete replacement geometry. To attach a second geometry to the same object, use its `--object-id`; to link existing annotations, use `annolabel link IMAGE --ids BOX_ID POLYGON_ID`. Equal class names never imply equal object identity. Use `remove IMAGE --id ANNOTATION_ID` to remove one annotation. Render again and inspect your edit.

## Files, recovery, and limitations

Annotations live next to each source as `photo.jpg.labels.json`. Version 2 sidecars store the source hash, oriented dimensions, annotations, and object identity. Version 1 files can be read, but known legacy box/polygon pairs must be linked explicitly before export. The original image is not modified.

Task directories contain source/review PNGs, internal packets and snapshots, task handles, and saved receipts. They use absolute paths and are working state, not a portable training format. Keep the image, sidecar, and task directory in place while a task is active. Use COCO export to move a dataset between machines.

Failures, including argument-parsing errors, return one JSON object on stderr with exit code `2`; successful results remain JSON on stdout with exit code `0`. Every failure includes `error`, `code`, and a specific `recovery` step. Validation `details` are plain strings naming fields and object indexes/keys, such as `objects[0] (leaf1): box requires x1 < x2 and y1 < y2`. Fix all reported problems together and retry the same unconsumed handle with a complete snapshot. Failed validation does not save annotations or consume a pass. If there are no scene classes, include `"classifications": []`.

| Problem | What to do |
|---|---|
| `INVALID_ARGUMENTS` | Correct the named flag or argument; run `annolabel COMMAND --help` for allowed values. |
| `VALIDATION_ERROR` | Fix the reported JSON/geometry issue and retry the same unconsumed handle. |
| `CHECKPOINT_USED` | Run `task-status` on the initial handle; use the latest returned handle for a different snapshot. |
| `TASK_CONFLICT` | Stop and reconcile source/annotation changes; do not reconstruct hashes to force an overwrite. |
| `PASS_LIMIT` | Stop this task and report uncertainty. Export remains available. |
| `OBJECTS_REMOVED` | Keep the named saved objects in the complete snapshot, or retry the same handle with `--allow-empty` if removing all of them is intentional. No pass was used. |
| `OUTPUT_RESERVED` | Pass a preview `--output` outside task, pass, folder-task, audit and preview directories. Nothing was written. |
| `IO_ERROR` | Check paths and access. Use absolute paths when the agent's working directory is uncertain. |
| `OUTPUT_EXISTS` | Choose a new task, review, or export directory. `render` and `mask` can explicitly overwrite their PNGs with `--force`. |
| Annotation outlines are shifted | Recheck original oriented dimensions and whether coordinates came from a resized or padded image. |
| `NO_ANNOTATIONS` | Run `task` and `submit` for the source images first, then export the folder containing their sidecars. |
| `NO_IMAGES` | Pass a folder containing supported source images; keep generated views outside it and use a new task output directory. |
| `TASK_MISSING` | A task that `task-retry` started is missing: restore its directory. Delete the named `retries/` record only if `task-retry` was interrupted before publishing it. |
| `FOLDER_INDEX_REQUIRED` | Pass the folder `tasks.json` to `task-retry`; it does not accept per-image handles. |
| `IMAGE_TASK_REQUIRED` | Run `task-status` on the folder's `tasks.json`, then submit using the returned per-image `next_task`. |

Current scope:

- Single-frame raster images supported by Pillow; multi-frame images are rejected. No video labeling.
- Simple, single-component polygons. No holes, multipart instances, brushes, keypoints, or native RLE input.
- No inference engine, browser annotation editor, automatic model orchestration, dataset splitting, or built-in format conversion beyond COCO export.
- Revision checks protect against stale edits but are not a process lock. Serialize writes to the same image.
- Geometry validation does not verify object identity, completeness, class meaning, or pixel-perfect boundaries. Review the image and report uncertainty.

## Command reference

Run `annolabel COMMAND --help` for full arguments.

| Command | Purpose |
|---|---|
| `task IMAGE --output DIR` | Prepare a short task with a source-sized image view. |
| `submit TASK --file JSON_OR_-` | Replace a complete annotation snapshot through a saved task handle. |
| `task-status TASK` | Recover the latest task handle and view, or folder progress from `tasks.json`. |
| `task FOLDER --output DIR --keep-going` | Start healthy folder images even if some sources fail. |
| `task-retry TASKS_JSON` | Start only the folder sources that failed, with the original rules. |
| `info IMAGE` | Read source metadata and current annotations. |
| `label IMAGE --label CLASS` | Add or update a scene classification. |
| `box IMAGE --label CLASS --xyxy X1 Y1 X2 Y2` | Add or update a bounding box. |
| `polygon IMAGE --label CLASS --points JSON` | Add or update a polygon; `--points-file` also accepts a JSON file. |
| `link IMAGE --ids ID ID` | Join existing geometries into one object. |
| `remove IMAGE --id ID` | Remove one annotation. |
| `render IMAGE --output PNG` | Render labels and geometries; optional `--grid PIXELS`. |
| `mask IMAGE --id ID --output PNG` | Rasterize a stored polygon as a binary mask. |
| `prepare IMAGE --output DIR` | Prepare full metadata, editable snapshot, and detailed views. |
| `apply IMAGE --file JSON --packet PACKET --output DIR` | Apply a complete revision-aware snapshot and render detailed reviews. |
| `review IMAGE --output DIR` | Render detailed review pages or custom regions. |
| `preview TASK_JSON --file JSON --output DIR` | Optional: render an unsaved candidate and its removals before submit; uses no pass. |
| `audit FOLDER --output DIR [--categories FILE]` | Read-only folder check: per-source status, label counts, issues, overview pages and optional object-vocabulary mismatches. |
| `export IMAGE_OR_FOLDER --output DIR` | Export a COCO dataset. |
