# Reusable labeling prompt

Replace every angle-bracket placeholder before giving this to your agent. Provide the research instructions in this prompt, even when also saving them with `--instructions`.

```text
Label <ABSOLUTE_IMAGE_PATH> with AnnoLabel installed at <ABSOLUTE_ANNOLABEL_DIR>.
Researcher instructions: <WHAT_TO_LABEL_AND_BOUNDARY_POLICY>.
Allowed object labels: <LABELS_OR_FREE_FORM>.

Use: uv run --project <ABSOLUTE_ANNOLABEL_DIR> annolabel
Create a task in <NEW_ABSOLUTE_TASK_DIR> with the appropriate --geometry mode.
Read the returned guide and open the returned view using your image tool.
Coordinates are original oriented pixels: (0,0) at top-left, x right, y down.
No padding or coordinate offset.

Edit the returned submission file as one complete snapshot of all
classifications and objects; it already holds any saved labels, so keep their
stable unique keys. Run the submit command named in next. Open the returned
view once. Check labels, omissions, excess background, clipped contours and
coordinate shifts. Only if needed, make one correction by editing the NEW
returned submission file and submitting it with the new task handle, retaining
every unchanged object and class. Then stop.

If output is lost, run task-status on the initial handle. Fix reported invalid
JSON once and retry the same unconsumed handle. Stop on TASK_CONFLICT or PASS_LIMIT.
Use your own vision; no detectors, inference models, other agents, implementation
inspection or crop/CLI helper scripts. Export COCO to <NEW_ABSOLUTE_DATASET_DIR>.
Report source count, object counts, annotation/review/export paths and uncertainty.
```

For a folder, replace `<ABSOLUTE_IMAGE_PATH>` with the folder path: `task FOLDER --output NEW_DIR` creates one task per image. Follow each `next` step, which names that image's guide, view, submission file and submit command, and run `task-status NEW_DIR/tasks.json` for progress and the export command. If the folder start fails on one source, rerun it with `--keep-going` into a new directory, label the healthy images, report the failed sources, and run `task-retry NEW_DIR/tasks.json` only after a named file is fixed. `status: attention` with exit 0 still means some sources need attention. Keep a single writer per image. An agent can open multiple images and group independent submission commands, subject to its own model and image-tool limits. Never reuse coordinates from this repository's synthetic example on another image.
