# Changelog

## 0.5.1 — 2026-09-09

- Licensed AnnoLabel under MIT, with license and maintainer metadata in distributions.
- Added automatic PR checks, Python 3.11–3.14 coverage, and Linux/macOS/Windows tests.
- Added contribution and security guidance, issue/PR templates, compatibility policy, and weekly dependency update PRs.
- Shortened the README and made the example downloadable without cloning.
- Added reproducible synthetic annotation evaluation data, archived model predictions, scoring, and explicit measurement limitations.
- Applied consistent formatting and import ordering without changing annotation behavior.
- Automated GitHub release notes and tags after successful PyPI publication.

## 0.5.0 — 2026-09-09

- Renamed the project and GitHub repository to AnnoLabel (`Andesprit/annolabel`).
- The PyPI package, CLI command, and Python module are now `annolabel`. The core facade is `AnnoLabel`.
- Update existing scripts and agent prompts from `labelkit` to `annolabel`. Install the new package with `uv tool install annolabel`; it is a separate distribution from `andesprit-labelkit`.
- Existing annotation sidecars, task handles, object IDs, and COCO exports remain compatible. Labeling behavior is unchanged.
- Moved local image loading behind `ImageServiceBase` / `LocalImageService`, wired by core facades and injected into export logic.
- Organized tests to mirror package folders, retaining real CLI coverage and adding direct module/schema/service tests and mocked I/O failure checks.
