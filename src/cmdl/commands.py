"Application jobs, deliberately separate from service startup."

import argparse
import json
from pathlib import Path


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    train = sub.add_parser("train")
    train.add_argument("--import-id")
    train.add_argument(
        "--semantic", action="store_true", help="Download/run multilingual MiniLM and FAISS"
    )
    train.add_argument("--alpha", type=float, default=None)
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--output-dir", default="artifacts/models")
    sub.add_parser("quality")
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--import-id")
    evaluate.add_argument("--semantic", action="store_true")
    evaluate.add_argument("--experiments", action="store_true")
    evaluate.add_argument("--seed", type=int, default=42)
    evaluate.add_argument("--output-dir", default="artifacts/evaluation")
    sample = sub.add_parser("review-sample")
    sample.add_argument("--output-dir", default="artifacts/human-review")
    sample.add_argument("--seed", type=int, default=42)
    labels = sub.add_parser("review-metrics")
    labels.add_argument("--labels", type=Path, required=True)
    labels.add_argument("--sample", type=Path, default=Path("artifacts/human-review/sample.json"))
    labels.add_argument("--output-dir", default="artifacts/human-review")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--url", default="http://api:8000")
    benchmark.add_argument("--requests", type=int, default=200)
    benchmark.add_argument("--output-dir", default="artifacts/latency")
    args = parser.parse_args(argv)
    if args.command == "train":
        from cmdl.recommender.training import train_demo

        alpha = args.alpha
        if alpha is None:
            tuning = Path("artifacts/evaluation/tuning.json")
            alpha = json.loads(tuning.read_text())["selected_alpha"] if tuning.exists() else 0.5
        if not 0 <= alpha <= 1:
            parser.error("--alpha must be in [0,1]")
        result = train_demo(
            args.import_id,
            semantic=args.semantic,
            alpha=alpha,
            seed=args.seed,
            output_dir=args.output_dir,
        )
        result = {
            key: result[key]
            for key in (
                "release_id",
                "snapshot_id",
                "candidate_count",
                "cached_users",
                "alpha",
                "semantic",
            )
        }
    elif args.command == "quality":
        import psycopg

        from cmdl.quality.store import check_all

        with psycopg.connect() as connection:
            result = {"new_assessments": check_all(connection)}
    elif args.command == "evaluate":
        from cmdl.evaluation.runner import run

        result = run(args)
    elif args.command == "review-sample":
        from cmdl.evaluation.human import sample

        result = sample(args.output_dir, args.seed)
    elif args.command == "review-metrics":
        from cmdl.evaluation.human import metrics

        result = metrics(args.labels, args.sample, args.output_dir)
    else:
        from cmdl.evaluation.latency import benchmark

        result = benchmark(args.url, args.requests, args.output_dir)
    print(json.dumps(result, indent=2, default=str))
    return 0
