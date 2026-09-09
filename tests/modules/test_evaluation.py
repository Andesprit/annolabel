import pytest

from annolabel.modules.evaluation import compare_documents
from annolabel.schemas.annotations import Annotation, Document, ImageInfo


def _document(*annotations: Annotation, sha: str = "source") -> Document:
    return Document(
        image=ImageInfo(file="fixture.png", width=100, height=80, sha256=sha),
        annotations=list(annotations),
    )


def _polygon(key: str, label: str = "rectangle", offset: int = 0) -> Annotation:
    return Annotation(
        id=key,
        kind="polygon",
        label=label,
        points=[(10 + offset, 10), (30 + offset, 10), (30 + offset, 30), (10 + offset, 30)],
    )


def test_perfect_geometry_does_not_hide_wrong_label() -> None:
    result = compare_documents(_document(_polygon("ref")), _document(_polygon("pred", "square")))
    assert result["mean_box_iou_missing_zero"] == 1
    assert result["mean_mask_iou_missing_zero"] == 1
    assert result["label_errors"] == 1
    assert result["missed_objects"] == result["extra_objects"] == []


def test_missing_and_duplicate_objects_are_counted() -> None:
    reference = _document(_polygon("a"), _polygon("b", offset=50))
    candidate = _document(_polygon("x"), _polygon("y"))
    result = compare_documents(reference, candidate)
    assert result["matched_objects"] == 1
    assert result["missed_objects"] == ["b"]
    assert result["extra_objects"] == ["y"]
    assert result["mean_mask_iou_missing_zero"] == 0.5
    assert result["mean_box_iou_missing_zero"] == 0.5


def test_box_only_predictions_receive_zero_segmentation_score() -> None:
    prediction = Annotation(id="box", kind="box", label="rect", points=[(10, 10), (30, 30)])
    result = compare_documents(
        _document(_polygon("ref")), _document(prediction), {"rect": "rectangle"}
    )
    assert result["label_errors"] == 0
    assert result["mean_mask_iou_missing_zero"] == 0
    assert (
        compare_documents(_document(prediction), _document(prediction))[
            "mean_mask_iou_missing_zero"
        ]
        is None
    )


def test_no_predictions_and_disjoint_predictions_do_not_disappear() -> None:
    reference = _document(_polygon("ref"))
    for candidate in (_document(), _document(_polygon("far", offset=60))):
        result = compare_documents(reference, candidate)
        assert result["matched_objects"] == 0
        assert result["mean_box_iou_missing_zero"] == 0
        assert result["mean_mask_iou_missing_zero"] == 0
        assert result["missed_objects"] == ["ref"]


def test_shifted_predictions_and_incompatible_sources() -> None:
    reference = _document(_polygon("ref"))
    result = compare_documents(reference, _document(_polygon("pred", offset=2)))
    assert result["mean_box_iou_missing_zero"] == pytest.approx(18 / 22)
    assert result["mean_mask_iou_missing_zero"] == pytest.approx(19 / 23)
    with pytest.raises(ValueError, match="same source"):
        compare_documents(reference, _document(sha="different"))
    with pytest.raises(ValueError, match="at least one"):
        compare_documents(_document(), reference)
