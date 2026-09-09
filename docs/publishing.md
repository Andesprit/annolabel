# Publishing to PyPI

Distribution: `annolabel`. CLI and module: `annolabel`.

Version **0.5.0** is the first release under the AnnoLabel name. The earlier distribution, `andesprit-labelkit` 0.4.2, remains available under its original name. Historical version tags identify those original releases.

## One-time setup

Configure a pending trusted publisher on the PyPI account that will own the project:

| Setting | Value |
|---|---|
| PyPI project name | `annolabel` |
| GitHub owner | `Andesprit` |
| Repository | `annolabel` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The first successful upload creates the PyPI project. This does not require a long-lived API token. [PyPI's setup guide](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

## Validate and publish

The `Publish to PyPI` workflow is manually triggered. With `publish=false` it runs tests, builds a wheel and source distribution, checks metadata, and smoke-tests both. With `publish=true` on `main`, it also publishes the exact uploaded build artifacts using trusted publishing.

```sh
gh workflow run publish.yml --repo Andesprit/annolabel --ref main -f publish=false
# After trusted publishing is configured and the build checks pass:
gh workflow run publish.yml --repo Andesprit/annolabel --ref main -f publish=true
```

Keep the package version in `pyproject.toml` and the CLI `--version` output consistent. Update `uv.lock` when package metadata changes. Use a new version for every changed release; never replace a published artifact or move an existing version tag.

Validate locally:

```sh
uv sync --locked
uv run pytest -q
uv build --no-sources --out-dir dist/pypi
uvx twine check --strict dist/pypi/*
```

After publication, confirm the name and version on PyPI and install from the registry in a fresh tool environment:

```sh
uv tool install annolabel
annolabel --version
```

The PyPI description lives in `docs/PYPI.md`, with self-contained usage instructions. The source repository and published PyPI distributions are public.
