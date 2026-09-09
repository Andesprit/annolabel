# Reusable labeling prompt

Replace every angle-bracket placeholder before giving this to your agent. Provide the research instructions in this prompt, even when also saving them with `--instructions`.

```text
Label <ABSOLUTE_IMAGE_PATH> with AnnoLabel installed at <ABSOLUTE_ANNOLABEL_DIR>.
Researcher instructions: <WHAT_TO_LABEL_AND_BOUNDARY_POLICY>.
Allowed object labels: <LABELS_OR_FREE_FORM>.

Use: uv run --project <ABSOLUTE_ANNOLABEL_DIR> annolabel
Create a task in <NEW_ABSOLUTE_TASK_DIR> with the appropriate --geometry mode.
Open its returned view using your image tool. Coordinates are original oriented
pixels: (0,0) at top-left, x right, y down. No padding or coordinate offset.

Submit all classifications and objects together as JSON, retaining stable unique
keys. Open the returned view once. Check labels, omissions, excess background,
clipped contours and coordinate shifts. Make at most one correction using the
new task handle, retaining every unchanged object and class. Then stop.

If output is lost, run task-status on the initial handle. Fix reported invalid
JSON once and retry the same unconsumed handle. Stop on TASK_CONFLICT or PASS_LIMIT.
Use your own vision; no detectors, inference models, other agents, implementation
inspection or crop/CLI helper scripts. Export COCO to <NEW_ABSOLUTE_DATASET_DIR>.
Report source count, object counts, annotation/review/export paths and uncertainty.
```

For a group of images, create a separate task for each source and keep a single writer per image. An agent can open multiple images and group independent submission commands, subject to its own model and image-tool limits. Never reuse coordinates from this repository's synthetic example on another image.
