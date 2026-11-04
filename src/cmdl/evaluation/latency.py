"Real HTTP latency (including eligibility, DB and JSON), with explicit conditions."

import json
import platform
import time
from pathlib import Path

import httpx
import numpy as np


def benchmark(url, count, output_dir):
    durations = []
    with httpx.Client(base_url=url, timeout=60, trust_env=False) as client:
        accounts = client.get("/demo/accounts").raise_for_status().json()["accounts"]
        people = [r["person_id"] for r in accounts] + ["0"]
        for person in people:
            client.get("/recommend/" + person).raise_for_status()
        for index in range(count):
            start = time.perf_counter()
            response = client.get("/recommend/" + people[index % len(people)], params={"limit": 10})
            response.raise_for_status()
            assert response.json()["returned_count"] <= 10
            durations.append((time.perf_counter() - start) * 1000)
    result = {
        "requests": count,
        "boundary": (
            "full HTTP response including SQL, ranking, enrichment and JSON; warm model/cache"
        ),
        "concurrency": 1,
        "accounts": len(people),
        "client_platform": platform.platform(),
        "processor": platform.processor(),
        "api_url": url,
        "median_ms": float(np.median(durations)),
        "p95_ms": float(np.percentile(durations, 95)),
        "p99_ms": float(np.percentile(durations, 99)),
        "max_ms": max(durations),
        "all_under_300ms": all(d < 300 for d in durations),
        "p95_under_300ms": bool(np.percentile(durations, 95) < 300),
        "samples_ms": durations,
        "limitations": (
            "Single client on the local Docker network; not a "
            "production load or cold-start benchmark."
        ),
    }
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "latency.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {k: v for k, v in result.items() if k != "samples_ms"}
