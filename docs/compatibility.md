# Compatibility and support

## Python and operating systems

Supported Python versions are CPython 3.11–3.14. CI tests every listed version on Linux, and the oldest and newest on macOS and Windows. Intermediate Python versions on macOS and Windows are supported but not separately tested in the matrix. Other operating systems, PyPy, and future Python versions are unverified. The package's `>=3.11` metadata allows newer Python versions to attempt installation; it is not a claim that those versions have been tested.

The development checkout defaults to Python 3.14 via `.python-version`; `uv` can install the interpreter. User installation only needs `uv tool install annolabel`. Shell examples use POSIX syntax; on PowerShell, put multiline commands on one line and use `Copy-Item` for `cp` if needed.

## Versioning and saved data

AnnoLabel is beta software using `0.minor.patch` versions. Patch releases preserve documented CLI commands, coordinate conventions, and readable sidecar/task formats. Behavior changes beyond fixes require a minor release with migration notes. Python imports and internal packet layouts are experimental; rely on the CLI rather than internal modules.

JSON clients must tolerate additional fields. Existing documented success fields and error codes are preserved within a minor series. Most operational errors use JSON on stderr and exit code 2; argparse usage errors remain text. Successful commands emit JSON on stdout with exit code 0, except `--help` and `--version`.

Version 2 sidecars are the current annotation format. Version 1 sidecars remain readable; legacy box/polygon pairs may need explicit linking. Task handles preserve absolute paths and require their image, sidecar, and task directory to remain in place. They are not a portable dataset format or a concurrency lock. Export COCO to transfer datasets between machines. Keep a backup before adopting a new minor version.

The LabelKit-to-AnnoLabel rename preserved saved object identities and task compatibility; see [migration](migration.md). Pin a published version for reproducible research:

```sh
uv tool install annolabel==0.5.1
```

## Scope and maintenance

Support covers single-frame images, scene labels, boxes, and simple polygons. Holes, multipart instances, video, keypoints, and inference models are outside the current scope. Visual accuracy depends on the agent and data; see [evaluation limitations](../benchmarks/README.md).

Maintainers prioritize data preservation, coordinate correctness, COCO interoperability, and concise agent workflows. Open an issue before proposing a new annotation format or integration. Security maintenance is described in [SECURITY.md](../SECURITY.md).
