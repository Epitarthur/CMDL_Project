"Chronological, paired evaluation. Live tables are never queried by this module."

import hashlib
import importlib.metadata
import json
import math
import platform
import random
import zipfile
from collections import defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg

from cmdl.data.snapshots import _inputs, load_snapshot, prepare_benchmark
from cmdl.data.temporal import PERIODS
from cmdl.recommender.models import MODELS, PROTOCOL, Ranker
from cmdl.recommender.training import code_fingerprint
from cmdl.runs import create_run


def metrics(ranked, relevant, k=10):
    top = ranked[:k]
    hits = [int(i in relevant) for i in top]
    recall = sum(hits) / len(relevant) if relevant else 0.0
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    dcg = sum(hit / math.log2(i + 2) for i, hit in enumerate(hits))
    return {"recall_at_10": recall, "ndcg_at_10": dcg / ideal if ideal else 0.0}


def holdout(connection, import_id, snapshot, period):
    start, end = PERIODS[period]
    _, _, timeline, events = _inputs(connection, import_id, "original", end)
    catalog = {a.content_id for a in snapshot.candidates()}
    labels = defaultdict(set)
    all_users = set()
    for event in events:
        if start <= event.event_at < end:
            all_users.add(event.person_id)
            if (
                event.duplicate_of_interaction_id is None
                and timeline.exclusion(event) is None
                and event.content_id in catalog
                and (event.person_id, event.content_id) not in snapshot.consumed_pairs
            ):
                labels[event.person_id].add(event.content_id)
    return dict(labels), len(all_users - labels.keys())


def evaluate(ranker, labels, modes):
    report = {}
    for mode in modes:
        rows, union = [], set()
        language_rows = {lang: [] for lang in ("en", "pt")}
        warm, cold = [], []
        for person, positives in sorted(labels.items()):
            ranked = [r["content_id"] for r in ranker.rank(person, mode=mode, limit=10)]
            result = {"person_id": person, "relevant": len(positives), **metrics(ranked, positives)}
            rows.append(result)
            union.update(ranked)
            (warm if person in ranker.warm_users else cold).append(result)
            for lang in language_rows:
                relevant = {
                    i
                    for i in positives
                    if ranker.catalog[i].get("original_lang", ranker.catalog[i]["lang"]) == lang
                }
                if relevant:
                    language_rows[lang].append(metrics(ranked, relevant))

        def aggregate(values):
            return {
                "users": len(values),
                **{
                    key: sum(r[key] for r in values) / len(values) if values else None
                    for key in ("recall_at_10", "ndcg_at_10")
                },
            }

        report[mode] = {
            **aggregate(rows),
            "catalog_coverage": len(union) / len(ranker.ids) if ranker.ids else 0,
            "warm": aggregate(warm),
            "fallback": aggregate(cold),
            "languages": {lang: aggregate(values) for lang, values in language_rows.items()},
            "per_user": rows,
        }
    return report


def corrupt(records, field, rate, seed):
    items = sorted({r["content_id"] for r in records.values()})
    random.Random(seed).shuffle(items)
    selected = set(items[: int(len(items) * rate)])
    result = {}
    for key, record in records.items():
        r = {**record, "original_lang": record["lang"]}
        if record["content_id"] in selected:
            r["base_version_id"] = record["metadata_version_id"]
            r["metadata_version_id"] = str(
                uuid5(NAMESPACE_URL, f"damage-v1:{key}:{field}:{rate}:{seed}")
            )
            if field == "title":
                r["title"] = ""
            elif field == "text":
                r["text"] = " ".join(r["text"].split()[:20])
            elif field == "lang":
                r["lang"] = "pt" if r["lang"] == "en" else "en"
            else:
                raise ValueError("Unknown corruption field")
        result[key] = r
    return result, sorted(selected)


def run(args):
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    # Retain the actual development sources, not just a hash of an uncommitted tree.
    archive = output / "source.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        paths = list(Path("src").rglob("*.py")) + [Path("pyproject.toml"), Path("uv.lock")]
        for path in sorted(paths):
            if path.exists():
                entry = zipfile.ZipInfo(path.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(entry, path.read_bytes())
    provenance = {
        "code_sha256": code_fingerprint(),
        "source_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dependencies": sorted(
            f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()
        ),
        "working_tree": "development capture; exact source archive retained",
        "lock_sha256": hashlib.sha256(Path("uv.lock").read_bytes()).hexdigest(),
    }
    with psycopg.connect() as connection:
        if args.import_id:
            import_id = UUID(args.import_id)
        else:
            imports = connection.execute(
                "SELECT import_id FROM source_imports ORDER BY import_id"
            ).fetchall()
            if len(imports) != 1:
                raise ValueError("Provide --import-id when the database has multiple or no imports")
            import_id = imports[0][0]
        prepared, _ = prepare_benchmark(connection, import_id, "original")
        rows = connection.execute(
            "SELECT snapshot_id,as_of_at FROM snapshots WHERE "
            "status='published' AND metadata_variant_id='original' "
            "ORDER BY as_of_at"
        ).fetchall()
        snapshots = {}
        labels = {}
        empty_users = {}
        descriptors = {}
        for period, (start, _) in PERIODS.items():
            candidates = [row[0] for row in rows if row[1] == start]
            candidates = [
                i
                for i in candidates
                if connection.execute(
                    (
                        "SELECT source_manifest->'descriptor'->>'import_id' FROM"
                        " snapshots WHERE snapshot_id=%s"
                    ),
                    (i,),
                ).fetchone()[0]
                == str(import_id)
            ]
            if len(candidates) != 1:
                raise ValueError("Expected exactly one original snapshot per benchmark cutoff")
            snapshots[period] = load_snapshot(connection, candidates[0])
            labels[period], empty_users[period] = holdout(
                connection, import_id, snapshots[period], period
            )
            descriptors[period] = connection.execute(
                "SELECT source_manifest FROM snapshots WHERE snapshot_id=%s", (candidates[0],)
            ).fetchone()[0]
    print("Fitting validation models using pre-January data only.", flush=True)
    validation = Ranker(snapshots["validation"], seed=args.seed, semantic=args.semantic)
    trials = []
    for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
        validation.alpha = alpha
        result = evaluate(validation, labels["validation"], ["fixed_hybrid"])["fixed_hybrid"]
        trials.append({"alpha": alpha, "ndcg_at_10": result["ndcg_at_10"] or 0})
    best = max(trials, key=lambda r: (r["ndcg_at_10"], -abs(r["alpha"] - 0.5)))["alpha"]
    tuning = {
        "selected_alpha": best,
        "selection_metric": "validation mean NDCG@10; fixed hybrid",
        "validation_only": True,
        "seed": args.seed,
        "trials": trials,
        "semantic": args.semantic,
    }
    (output / "tuning.json").write_text(json.dumps(tuning, indent=2), encoding="utf-8")
    validation.alpha = best
    modes = list(MODELS) if args.semantic else [m for m in MODELS if m != "semantic"]
    results = {"validation": evaluate(validation, labels["validation"], modes)}
    print("Refitting on pre-February data with frozen validation-selected weight.", flush=True)
    test = Ranker(snapshots["test"], seed=args.seed, alpha=best, semantic=args.semantic)
    results["test"] = evaluate(test, labels["test"], modes)
    experiments = [{"field": "original", "rate": 0.0, "metrics": results["test"]}]
    if args.experiments:
        for field in ("title", "text", "lang"):
            for rate in (0.1, 0.3, 0.5):
                print(f"Metadata experiment: {field} at {rate:.0%}", flush=True)
                records, selected = corrupt(test.records, field, rate, args.seed)
                changed = test.rebuild_content(records, semantic=args.semantic)
                comparisons = evaluate(changed, labels["test"], modes)
                for model, stats in comparisons.items():
                    original = {r["person_id"]: r for r in results["test"][model]["per_user"]}
                    stats["paired_ndcg_deltas"] = [
                        {
                            "person_id": r["person_id"],
                            "delta": r["ndcg_at_10"] - original[r["person_id"]]["ndcg_at_10"],
                        }
                        for r in stats["per_user"]
                    ]
                record_file = output / f"damage-{field}-{int(rate * 100)}.json"
                record_file.write_text(
                    json.dumps(records, sort_keys=True, default=str), encoding="utf-8"
                )
                experiments.append(
                    {
                        "field": field,
                        "rate": rate,
                        "selected_ids": selected,
                        "records_sha256": hashlib.sha256(record_file.read_bytes()).hexdigest(),
                        "metrics": comparisons,
                    }
                )
    report = {
        "training_support": {"validation": validation.support_counts, "test": test.support_counts},
        "protocol": PROTOCOL,
        "seed": args.seed,
        "tuning": tuning,
        "source_snapshots": descriptors,
        "semantic": test.semantic_manifest,
        "models_run": modes,
        "users_without_eligible_positives": empty_users,
        "population": {
            p: {
                "evaluated_users": len(labels[p]),
                "warm_training_users": len(r.warm_users),
                "als_items": len(r.als_items),
                "catalog": len(r.ids),
            }
            for p, r in (("validation", validation), ("test", test))
        },
        "results": results,
        "experiments": experiments,
        "human_review": "Awaiting two independent reviewers; no agreement/accuracy claim.",
        "notes": [
            (
                "Global exclusive UTC cutoffs; original historical "
                "metadata only; live tables never read."
            ),
            (
                "All models use the same eligible catalog, heldout "
                "positives and users, including a reported fallback "
                "slice."
            ),
            (
                "Language-label alteration is a negative control: "
                "title/text features do not consume labels."
            ),
            "Rates select nested seeded article samples; shortening retains the first 20 words.",
            (
                "Controlled metadata variants are local research "
                "artifacts, never promoted into the demo database."
            ),
        ],
    }
    target = output / "results.json"
    target.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        "# Measured chronological evaluation",
        "",
        f"Protocol: `{PROTOCOL}`; seed {args.seed}; selected alpha {best}.",
        "",
        "| Split | Model | Users | Recall@10 | NDCG@10 | Coverage |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for period, models in results.items():
        for model, stat in models.items():
            lines.append(
                f"| {period} | {model} | {stat['users']} | "
                f"{stat['recall_at_10'] or 0:.5f} | {stat['ndcg_at_10'] or 0:.5f} | "
                f"{stat['catalog_coverage']:.5f} |"
            )
    lines += [
        "",
        "## Metadata damage (test)",
        "",
        "| Field | Rate | Fixed NDCG | Quality-aware NDCG |",
        "|---|---:|---:|---:|",
    ]
    for experiment in experiments:
        stats = experiment["metrics"]
        lines.append(
            f"| {experiment['field']} | {experiment['rate']:.0%} | "
            f"{stats['fixed_hybrid']['ndcg_at_10'] or 0:.5f} | "
            f"{stats['quality_hybrid']['ndcg_at_10'] or 0:.5f} |"
        )
    lines += [
        "",
        (
            "Human reviewer labels are pending. Full language "
            "slices, paired user outputs, corruption IDs and source "
            "descriptors: results.json."
        ),
    ]
    (output / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_dir = create_run(
        output / "runs",
        provenance=provenance,
        snapshot={"snapshot_id": test.snapshot_id, "descriptor": descriptors["test"]["descriptor"]},
        config={
            "model": "chronological-comparison-v1",
            "transformations": [PROTOCOL, "damage-v1"],
            "parameters": tuning,
            "seeds": {"model": args.seed},
            "numeric_tolerance": 1e-6,
            "artifacts": {
                "results.json": str(target),
                "tuning.json": str(output / "tuning.json"),
                "source.zip": str(archive),
            },
        },
    )
    return {
        "report": str(target),
        "tracking_run": str(run_dir),
        "models_run": modes,
        "experiment_conditions": len(experiments),
        "selected_alpha": best,
    }
