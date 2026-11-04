import csv
import json

from cmdl.evaluation.human import metrics


def test_independent_labels_missing_and_disagreement_are_not_fabricated(tmp_path):
    sample = tmp_path / "sample.json"
    sample.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "article": {"metadata_version_id": "v1"},
                        "subset": "heldout",
                        "quality": {"warnings": [{"field": "title", "check_id": "required"}]},
                    },
                    {
                        "article": {"metadata_version_id": "v2"},
                        "subset": "calibration",
                        "quality": {"warnings": []},
                    },
                ]
            }
        )
    )
    labels = tmp_path / "labels.csv"
    columns = ["reviewer", "metadata_version_id", "subset", "field", "check_id", "actual_flag"]

    def write(values):
        with labels.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(values)

    write([["A", "v1", "heldout", "title", "required", ""]])
    assert metrics(labels, sample, tmp_path)["status"] == "awaiting_labels"
    write(
        [
            ["A", "v1", "heldout", "title", "required", "1"],
            ["B", "v1", "heldout", "title", "required", "0"],
            ["A", "v2", "calibration", "title", "required", "1"],
            ["B", "v2", "calibration", "title", "required", "1"],
        ]
    )
    report = metrics(labels, sample, tmp_path)
    assert report["paired_heldout_judgments"] == 1 and report["agreement"] == 0
    assert report["per_rule_consensus_only"]["title:required"]["precision"] is None
