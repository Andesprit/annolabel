# Changelog

## 0.5.0

- Renamed the project and GitHub repository to AnnoLabel (`Andesprit/annolabel`).
- The PyPI package, CLI command, and Python module are now `annolabel`. The core facade is `AnnoLabel`.
- Update existing scripts and agent prompts from `labelkit` to `annolabel`. Install the new package with `uv tool install annolabel`; it is a separate distribution from `andesprit-labelkit`.
- Existing annotation sidecars, task handles, object IDs, and COCO exports remain compatible. Labeling behavior is unchanged.
- Moved local image loading behind `ImageServiceBase` / `LocalImageService`, wired by core facades and injected into export logic.
- Organized tests to mirror package folders, retaining real CLI coverage and adding direct module/schema/service tests and mocked I/O failure checks.
