# Architecture

Tests mirror the package's folders and module names. Core tests exercise the real CLI with local images: validation, source orientation, stable object identity, correction passes, lost-response recovery, stale edits, masks, and COCO exports. Module and schema tests cover their contracts directly. Service tests use actual image files; mocks inject read/write failures to check cleanup and preservation of existing data. Shared fixtures live in `tests/conftest.py`, and helpers in `tests/helpers.py`; test modules do not import each other.

pycocotools is a development dependency used to read exported datasets and decode segmentation masks; runtime annotation and export use Pillow and Pydantic. No environment variables or credentials are required for these tests.

```text
src/annolabel/
├── main.py          # CLI argument parsing and JSON responses
├── core/            # Facades and concrete service wiring
├── modules/         # Snapshot conversion, rendering, view bundles, COCO export
├── schemas/         # Pydantic sidecars, submissions, and packets
└── services/
    └── images/
        ├── base.py  # ImageServiceBase contract
        └── local.py # LocalImageService: file loading and EXIF orientation
tests/
├── test_main.py     # CLI entrypoint and standard input/output contracts
├── core/            # test_annolabel, test_export, test_task, test_workflow
├── modules/         # test_batch, test_coco, test_images, test_views
├── schemas/         # test_annotations, test_task, test_workflow
└── services/images/ # test_local
examples/            # Synthetic source image, instructions, and submission JSON
docs/                # Agent prompt and README image
```

Dependencies flow from `main` to `core`, then to `modules` and service contracts. Core instantiates `LocalImageService` and passes it to consumers as `ImageServiceBase`. Modules never import core or concrete services; services never import core or modules. Schemas define shared data contracts and do not depend on those layers. Rendering and snapshot operations remain plain functions; only service connections need a base class and implementation.

`AnnoLabel(..., image_service=...)` and `export_dataset(..., image_service=...)` accept an alternative reader or a mock implementing `ImageServiceBase`. New external connections belong under `services/<name>/base.py` plus an implementation, wired in core. Add `core/settings.py` and environment validation only when a connection actually needs configuration or credentials.

Keep changes focused, add tests for behavioral changes, and run the suite before submitting a pull request. When changing CLI behavior, update the examples and agent instructions together. The experimental API is versioned as `0.x`; pin a package version for reproducible installations.

