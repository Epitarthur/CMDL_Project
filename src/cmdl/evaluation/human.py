"Seeded diverse sample and genuinely independent two-reviewer label accounting."

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

from cmdl import serving as s

RULES = (
    [(f, "required") for f in ("title", "text", "lang", "url")]
    + [(f, "field-format") for f in ("title", "text", "lang", "url")]
    + [
        ("url", "url-structure"),
        ("lang", "iso-language"),
        ("lang", "language-agreement"),
        ("title", "title-text-overlap"),
        ("title", "possible-duplicate"),
        ("url", "possible-duplicate"),
        ("text", "text-length"),
        ("text", "repetitive-text"),
    ]
)


def sample(output_dir, seed):
    rng = random.Random(seed)
    bins = defaultdict(list)
    with s.connect() as connection:
        release, ranker = s.runtime(connection)
        states = s.overlay(connection)
        for item in ranker.ids:
            article = s.article_record(connection, ranker, item, states)
            if article["availability"] != "available":
                continue
            quality = s.quality(connection, article["metadata_version_id"], release["snapshot_id"])
            metadata = article["metadata"]
            key = (
                metadata["lang"],
                bool(quality.get("warnings")),
                len(metadata["text"].split()) < 150,
            )
            bins[key].append({"article": article, "quality": quality})
    for values in bins.values():
        rng.shuffle(values)
    chosen = []
    while len(chosen) < 200 and any(bins.values()):
        for key in sorted(bins):
            if bins[key] and len(chosen) < 200:
                chosen.append(bins[key].pop())
    rng.shuffle(chosen)
    for index, record in enumerate(chosen):
        record["subset"] = "calibration" if index < 50 else "heldout"
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "seed": seed,
        "selection": (
            "round robin across language, warning status and 150-word length strata; seeded shuffle"
        ),
        "population_claim": (
            "Varied inspection sample; unweighted estimates do not represent population accuracy."
        ),
        "snapshot_id": str(release["snapshot_id"]),
        "records": chosen,
    }
    (folder / "sample.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    fields = [
        "reviewer",
        "content_id",
        "metadata_version_id",
        "subset",
        "field",
        "check_id",
        "predicted_flag",
        "actual_flag",
        "comment",
    ]
    for reviewer in ("A", "B"):
        with (folder / f"reviewer-{reviewer}.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for record in chosen:
                flags = {(w["field"], w["check_id"]) for w in record["quality"].get("warnings", [])}
                for field, rule in RULES:
                    writer.writerow(
                        {
                            "reviewer": reviewer,
                            "content_id": record["article"]["content_id"],
                            "metadata_version_id": record["article"]["metadata_version_id"],
                            "subset": record["subset"],
                            "field": field,
                            "check_id": rule,
                            "predicted_flag": int((field, rule) in flags),
                            "actual_flag": "",
                            "comment": "",
                        }
                    )
    return {
        "sampled_records": len(chosen),
        "calibration": min(50, len(chosen)),
        "heldout": max(0, len(chosen) - 50),
        "output": str(folder),
        "status": "Awaiting independent human labels; leave unknown labels blank.",
    }


def metrics(labels_path, sample_path, output_dir):
    manifest = json.loads(sample_path.read_text(encoding="utf-8"))
    records = {r["article"]["metadata_version_id"]: r for r in manifest["records"]}
    labels = defaultdict(dict)
    with labels_path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            version = row["metadata_version_id"]
            if version not in records or row["subset"] != records[version]["subset"]:
                raise ValueError("A label is not linked to the sampled version/subset")
            if row["actual_flag"] == "":
                continue
            if row["actual_flag"] not in ("0", "1") or row["reviewer"] not in ("A", "B"):
                raise ValueError(
                    "Use independent reviewers A/B and actual_flag 0/1 (blank means unknown)"
                )
            key = (version, row["field"], row["check_id"])
            if row["reviewer"] in labels[key]:
                raise ValueError("Duplicate reviewer judgment")
            labels[key][row["reviewer"]] = int(row["actual_flag"])
    paired = {
        key: value
        for key, value in labels.items()
        if set(value) == {"A", "B"} and records[key[0]]["subset"] == "heldout"
    }
    count = len(paired)
    agreement = sum(v["A"] == v["B"] for v in paired.values()) / count if count else None
    pa = sum(v["A"] for v in paired.values()) / count if count else 0
    pb = sum(v["B"] for v in paired.values()) / count if count else 0
    expected = pa * pb + (1 - pa) * (1 - pb)
    kappa = (agreement - expected) / (1 - expected) if count and expected < 1 else None
    tallies = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0, "disagreements": 0})
    for (version, field, rule), value in paired.items():
        stat = tallies[field + ":" + rule]
        if value["A"] != value["B"]:
            stat["disagreements"] += 1
            continue
        flags = {
            (w["field"], w["check_id"]) for w in records[version]["quality"].get("warnings", [])
        }
        prediction = (field, rule) in flags
        truth = bool(value["A"])
        stat["tp" if prediction and truth else "fp" if prediction else "fn" if truth else "tn"] += 1
    for stat in tallies.values():
        stat["precision"] = (
            stat["tp"] / (stat["tp"] + stat["fp"]) if stat["tp"] + stat["fp"] else None
        )
        stat["recall"] = stat["tp"] / (stat["tp"] + stat["fn"]) if stat["tp"] + stat["fn"] else None
    result = {
        "status": "measured" if count else "awaiting_labels",
        "paired_heldout_judgments": count,
        "paired_records": len({k[0] for k in paired}),
        "agreement": agreement,
        "cohen_kappa": kappa,
        "per_rule_consensus_only": dict(tallies),
        "incomplete_judgments": sum(len(v) < 2 for v in labels.values()),
        "note": (
            "Calibration excluded. Missing labels remain missing; "
            "disagreements need adjudication and are excluded from "
            "precision/recall."
        ),
    }
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agreement.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
