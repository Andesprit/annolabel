"""Recompute archived fixture scores; never invoke a model or change predictions."""

import argparse
import hashlib
import json
from pathlib import Path

from annolabel.modules.evaluation import compare_documents
from annolabel.schemas.annotations import Document


def main() -> None:
    """Print the checked-in benchmark's reproducible geometry and label scores.

    :returns: None; emits JSON on stdout.
    """
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Verify stored scores without rewriting them"
    )
    args = parser.parse_args()
    manifest = json.loads((root / "manifest.json").read_text())
    if (
        hashlib.sha256((root / manifest["source"]).read_bytes()).hexdigest()
        != manifest["image_sha256"]
    ):
        raise ValueError("benchmark source bytes differ from the recorded image")
    reference = Document.model_validate_json((root / "reference.json").read_text())
    if reference.image.sha256 != manifest["image_sha256"]:
        raise ValueError("reference does not match the recorded image")
    aliases = json.loads((root / "aliases.json").read_text())
    results = {}
    for path in sorted((root / "predictions").glob("*.json")):
        if (
            hashlib.sha256(path.read_bytes()).hexdigest()
            != manifest["runs"][path.stem]["predictions_sha256"]
        ):
            raise ValueError(f"archived predictions changed: {path.stem}")
        candidate = Document.model_validate_json(path.read_text())
        results[path.stem] = compare_documents(reference, candidate, aliases)
    if set(results) != set(manifest["runs"]):
        raise ValueError("missing archived predictions")
    if args.check and results != json.loads((root / "results.json").read_text()):
        raise ValueError("computed scores differ from stored results")
    print(json.dumps(results, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
