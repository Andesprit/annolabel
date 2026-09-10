# AnnoLabel

[![CI](https://github.com/Andesprit/annolabel/actions/workflows/ci.yml/badge.svg)](https://github.com/Andesprit/annolabel/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/annolabel)](https://pypi.org/project/annolabel/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**An image-labeling CLI for vision-capable agents.**

Give Codex, Claude Code, Gemini, or another vision-capable agent an image and a labeling task. The agent supplies the scene labels, bounding boxes, and segmentation polygons. AnnoLabel validates and stores them, renders a review image, and exports **COCO** for training.

AnnoLabel runs locally. It does not call models or infer boundaries. Your agent needs to be able to open images and run shell commands.

![Astra's bounding boxes and segmentation outlines on objects suspended in water](docs/images/astra-photo-annotated.png)

Astra labeled the 12 prominent objects in this image using AnnoLabel, with one annotation pass and one visual review. Boundaries remain approximate. [View the original image](docs/images/astra-photo-original.png).

## Install

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```sh
uv tool install annolabel
annolabel --help
```

Installs from [PyPI](https://pypi.org/project/annolabel/). No Git or cloning required. Supports Python 3.11–3.14 on Linux, macOS, and Windows; see [compatibility](docs/compatibility.md). If the command is not found, run `uv tool update-shell` and restart your shell.

## Example prompts

Paste one of these into your agent and replace the image path.

### Classify the whole image

```text
Use AnnoLabel to classify /path/to/photo.jpg as indoor or outdoor.
```

### Draw bounding boxes

```text
Use AnnoLabel to draw tight bounding boxes around every person in
/path/to/photo.jpg. Review the boxes and export the labels as COCO.
```

### Segment with polygons

```text
Use AnnoLabel to trace each leaf in /path/to/photo.jpg with a polygon
along its visible outline. Review the polygons and export the labels as COCO.
```

Replace the classes and objects with whatever you want to label.

[Detailed usage guide](docs/usage.md) · [Reusable agent instructions](docs/agent-prompt.md).

## Try an example without an LLM

Download these four files into a new folder using your browser:

- [shapes.png](https://raw.githubusercontent.com/Andesprit/annolabel/main/examples/shapes.png)
- [shapes-task.txt](https://raw.githubusercontent.com/Andesprit/annolabel/main/examples/shapes-task.txt)
- [shapes-categories.json](https://raw.githubusercontent.com/Andesprit/annolabel/main/examples/shapes-categories.json)
- [shapes-submission.json](https://raw.githubusercontent.com/Andesprit/annolabel/main/examples/shapes-submission.json)

Open a terminal in that folder and run:

```sh
annolabel task shapes.png --output task --instructions shapes-task.txt --categories shapes-categories.json
annolabel submit task/task.json --file shapes-submission.json
annolabel export shapes.png --output dataset
```

Open `task/pass1/view.png` to review the result. The export contains `annotations/instances_default.json`, oriented images, scene classifications, and provenance. The supplied coordinates belong only to this example. Output directories must be new; use fresh names when repeating it.

## Learn more

- [Commands, coordinates, review, recovery, and COCO export](docs/usage.md)
- [Compatibility and limitations](docs/compatibility.md)
- [Evaluation data, method, and model results](benchmarks/README.md)
- [Migrating from LabelKit](docs/migration.md)
- [Contributing and running tests](CONTRIBUTING.md) · [Architecture](docs/architecture.md)
- [Changelog](CHANGELOG.md) · [Publishing releases](docs/publishing.md)
- [Report a bug](https://github.com/Andesprit/annolabel/issues/new/choose) · [Security](SECURITY.md)

Simple polygons are supported; holes, multipart instances, video, and keypoints are not. Geometry validation does not establish visual accuracy. Agent review remains necessary.

## License

[MIT](LICENSE). See [asset provenance](ASSETS.md) for image licenses and attribution.
