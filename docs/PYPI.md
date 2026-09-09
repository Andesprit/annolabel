# AnnoLabel by Andesprit

MIT-licensed software maintained by [Andesprit](https://github.com/Andesprit). See the [license](https://github.com/Andesprit/annolabel/blob/main/LICENSE), [asset provenance](https://github.com/Andesprit/annolabel/blob/main/ASSETS.md), and [compatibility policy](https://github.com/Andesprit/annolabel/blob/main/docs/compatibility.md).

A local image-labeling CLI for vision-capable agents. The agent inspects your image and chooses scene labels, bounding boxes, and segmentation polygons. AnnoLabel validates and saves annotations, renders review images, and exports **COCO datasets**.

The PyPI distribution, executable, and Python module are all `annolabel`.

## Install

```sh
uv tool install annolabel
annolabel --version
annolabel --help
```

Or install into an existing Python environment:

```sh
python -m pip install annolabel
```

Requires Python 3.11 or later. Runtime dependencies are Pillow and Pydantic. AnnoLabel itself does not call LLMs, download models, or need API keys. Your chosen agent needs an image-viewing tool and shell access, and uses its own model-provider authentication.

Previously published as `andesprit-labelkit`. When upgrading, replace `labelkit` commands and Python imports with `annolabel`; the core facade is now `AnnoLabel` in `annolabel.core.annolabel`. Existing annotation sidecars and task handles remain compatible. Keep active task directories in place.

## Label an image

```sh
annolabel task /data/photo.jpg --output /data/work/photo-task
```

Open the returned `view` using your agent's image tool. It has the source image's exact EXIF-oriented pixel dimensions with no padding, grid or resizing. The top-left corner is `(0,0)`; x increases right and y increases down.

Ask the agent to write a complete JSON snapshot such as this, replacing every label and coordinate with its own visual interpretation:

```json
{
  "classifications": [{"label": "outdoor"}],
  "objects": [
    {
      "key": "object-1",
      "label": "example object",
      "box": [50, 30, 250, 180],
      "polygon": [[50, 100], [120, 30], [250, 100], [200, 180], [50, 180]],
      "note": "Describe uncertain identity or boundary placement here."
    }
  ]
}
```

These are schema examples, not annotations for your image. Both `classifications` and `objects` arrays are required. Coordinates must stay within the original oriented image bounds. Polygons need at least three distinct vertices, no self-intersections, and no repeated closing vertex. The box must contain the polygon; omit `box` to derive it automatically from the polygon.

Submit all annotations together:

```sh
annolabel submit /data/work/photo-task/task.json --file /data/annotations.json
```

Open the returned review `view`. Check missing objects, class meaning, clipped contours, excess background, and coordinate shifts. If necessary, submit one corrected complete snapshot using the **new task handle**:

```sh
annolabel submit /data/work/photo-task/pass1/task.json --file /data/corrected.json
```

Retain stable object keys and every unchanged object/class. Each submission replaces the whole image snapshot; omitted objects and classes are removed. By default a task allows two successful submissions. Use `--max-passes 1` when creating a task to allow one.

If output is lost, recover the latest handle and view:

```sh
annolabel task-status /data/work/photo-task/task.json
```

Invalid submissions preserve the existing annotations. Stale external edits are rejected. Keep a single writer per source image; revision checks are not a multi-process lock.

## Choose the task

- Whole-image classification: `annolabel label IMAGE --label CLASS`.
- Boxes only: create a task with `--geometry boxes` and submit objects with `box` and no polygon.
- Segmentation: the default requires a polygon per object, with a derived or explicit bounding box.
- Restrict object labels: pass `--categories categories.json`, a JSON array such as `["car", "person"]`. Scene labels are independent.
- Save research instructions: pass `--instructions instructions.txt` when creating a task. Also supply those instructions directly to the agent; the compact response does not repeat them.

## Export for training

```sh
annolabel export /data/photo.jpg --output /data/dataset
# Or export every annotated source discovered recursively:
annolabel export /data/images --output /data/dataset-all
```

COCO is the default and currently the only built-in training format. Export produces:

```text
dataset/
  annotations/instances_default.json
  images/default/000001.png
  categories.json
  classifications.csv
  provenance.json
  README.txt
```

Use `images/default/` as your COCO loader's image root. Each object has one record combining its box and optional polygon. COCO boxes use `[x,y,width,height]`; the CLI accepts `[x1,y1,x2,y2]`. Scene classifications and original IDs/notes are preserved separately. Provenance includes original source paths.

Reuse an ordered category vocabulary through `export --categories categories.json` across splits. AnnoLabel does not invent a train/validation split. Folder export skips images without annotation sidecars.

## Give your agent this workflow

```text
Use AnnoLabel to annotate IMAGE_PATH. Research task: YOUR_LABELS_AND_BOUNDARY_POLICY.
Create a task in NEW_OUTPUT_DIRECTORY. Open its returned view and use original
oriented pixels. Submit all scene labels and objects together. Review the returned
view once and, if needed, submit one corrected full snapshot with the new task
handle. Retain unchanged objects. Use your own vision; no detectors, segmentation
models, crop scripts or implementation inspection. Export COCO and report paths
and remaining uncertainty. Use task-status if a command response is lost.
```

Works with Codex, Claude Code, Gemini/Antigravity, or another image-capable agent with shell access. Folder traversal and batching are handled by the calling agent, with one task per image.

## More tools and limitations

Run `annolabel COMMAND --help` for any of: `task`, `submit`, `task-status`, `info`, `label`, `box`, `polygon`, `link`, `remove`, `render`, `mask`, `prepare`, `apply`, `review`, and `export`.

`prepare` / `apply` / `review` support detailed packets and padded crop reviews when more inspection is needed. They use a full revision-aware snapshot instead of the short `submit` contract. Detailed ruler values are original coordinates, not PNG margin positions.

Annotations are stored beside the source as `IMAGE.labels.json`. Output directories must be new. Source images remain unchanged. Task handles contain absolute paths and should stay in place during active work; use COCO exports for portable datasets.

Supports single-frame raster images and simple single-component polygons. No polygon holes, multipart instances, keypoints, video, inference engine, or GUI. Geometry validation does not establish visual accuracy: the agent must review the pixels and report uncertainty.

Public source repository: [Andesprit/annolabel](https://github.com/Andesprit/annolabel).
