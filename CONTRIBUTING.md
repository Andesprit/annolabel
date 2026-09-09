# Contributing to AnnoLabel

Bug reports, documentation fixes, and focused pull requests are welcome. For a substantial feature, open an issue describing the researcher or agent workflow first. Maintainers review contributions as availability allows; there is no response-time guarantee.

## Set up

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), fork the repository, and clone your fork:

```sh
git clone https://github.com/YOUR-USERNAME/annolabel.git
cd annolabel
uv sync --locked
uv run annolabel --help
```

## Make and check a change

```sh
git switch -c describe-your-change
uv run ruff check .
uv run ruff format .
uv run pytest -q --cov --cov-report=term-missing
uv build
```

Keep each PR focused. Explain the problem, resulting behavior, and verification. Add a regression test for a behavior change; documentation-only changes do not need new tests. Update the changelog for user-visible changes and update agent instructions when their workflow changes. Do not commit credentials, personal datasets, annotation work directories, or model transcripts.

Tests mirror `src/annolabel/` under `tests/`. Shared fixtures belong in `tests/conftest.py` and helpers in `tests/helpers.py`. Mocks are welcome for failure paths; preserve the real CLI and image tests. See [architecture](docs/architecture.md) for module boundaries and [compatibility](docs/compatibility.md) before changing saved formats.

CI runs on pull requests. Keep credentials out of tests so forks can run the same checks. Use `uv add` or `uv add --dev` for dependencies and commit `uv.lock`. Dependency updates are reviewed individually; they are not automatically merged.

For changes to agent views or coordinate handling, run the [evaluation](benchmarks/README.md) and include before/after results. A passing geometry validator does not establish visual accuracy.

## License and conduct

By submitting a contribution, you agree to make it available under the project's [MIT license](LICENSE). Only submit assets you have permission to distribute and record their provenance in [ASSETS.md](ASSETS.md). No CLA is required. Follow the [code of conduct](CODE_OF_CONDUCT.md); report vulnerabilities through [SECURITY.md](SECURITY.md).
