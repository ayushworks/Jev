import json
from pathlib import Path

import pytest

from judge_compare.review import prepare_g0_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_frozen_g0_package_contains_approved_proposal() -> None:
    with (ROOT / "reviews" / "G0-package.json").open(
        "r", encoding="utf-8"
    ) as handle:
        manifest = json.load(handle)

    paths = {entry["path"] for entry in manifest["artifacts"]}
    assert manifest["status"] == "pending_owner_decision"
    assert manifest["model_calls_made"] == 0
    assert manifest["spend_authorized"] is False
    assert "configs/g0-gpt54.yaml" in paths
    assert "configs/g0-astra.yaml" not in paths


def test_approved_g0_package_cannot_be_regenerated(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="G0 is already approved"):
        prepare_g0_manifest(ROOT, tmp_path / "G0-package.json")
