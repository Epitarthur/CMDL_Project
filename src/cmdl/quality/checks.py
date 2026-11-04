"Deterministic, reviewable rules; warnings are hypotheses, never corrected source data."

import re
from collections import Counter
from functools import lru_cache
from statistics import mean
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import pycountry
from langdetect import DetectorFactory, LangDetectException, detect_langs

VERSION = "metadata-rules-v1"
DIMENSIONS = ("completeness", "validity", "consistency", "informativeness")
DetectorFactory.seed = 0


def valid_url(value):
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme in ("https", "http") and parsed.hostname and not re.search(r"\s", value)
        )
    except ValueError:
        return False


@lru_cache(maxsize=20000)
def language(text):
    if len(text.split()) < 30:
        return None
    try:
        result = detect_langs(text[:3000])[0]
        return result.lang if result.prob >= 0.9 else None
    except LangDetectException:
        return None


def duplicate_keys(records):
    return {
        field: Counter(str(r.get(field, "")).strip().casefold() for r in records if r.get(field))
        for field in ("title", "url")
    }


def assess(record, duplicates=None):
    version = str(record["metadata_version_id"])
    run = str(uuid5(NAMESPACE_URL, f"{VERSION}:{version}"))
    checks = {d: [] for d in DIMENSIONS}
    warnings = []

    def check(dimension, field, rule, passed, reason, suggestion):
        checks[dimension].append(passed)
        if passed is False:
            warnings.append(
                {
                    "warning_id": str(uuid5(NAMESPACE_URL, f"{run}:{field}:{rule}")),
                    "metadata_version_id": version,
                    "checks_version": VERSION,
                    "quality_run_id": run,
                    "field": field,
                    "check_id": rule,
                    "dimension": dimension,
                    "severity": "warning",
                    "reason": reason,
                    "suggested_next_step": suggestion,
                    "reviews": [],
                }
            )

    for field in ("title", "text", "lang", "url"):
        check(
            "completeness",
            field,
            "required",
            bool(record.get(field, "").strip()),
            "Required field is empty.",
            "Inspect the source and supply a new metadata version.",
        )
        value = record.get(field, "")
        check(
            "validity",
            field,
            "field-format",
            "\x00" not in value and isinstance(value, str),
            "Unexpected field format.",
            "Inspect the original export.",
        )
    label = record.get("lang", "").lower()
    check(
        "validity",
        "url",
        "url-structure",
        valid_url(record.get("url", "")),
        "URL is not an absolute HTTP(S) URL with a hostname.",
        "Verify the source link; reachability was not tested.",
    )
    check(
        "validity",
        "lang",
        "iso-language",
        bool(pycountry.languages.get(alpha_2=label)),
        "Language label is not an ISO 639-1 code.",
        "Review the language label.",
    )
    text = record.get("text", "")
    detected = language(text)
    check(
        "consistency",
        "lang",
        "language-agreement",
        detected == label if detected else None,
        f"Text detector suggests {detected}; stored label is {label}. This is uncertain.",
        "Have a fluent reader confirm or reject this signal.",
    )
    title_words = set(re.findall(r"\w{4,}", record.get("title", "").casefold()))
    text_words = re.findall(r"\w+", text.casefold())
    overlap = bool(title_words.intersection(text_words))
    check(
        "consistency",
        "title",
        "title-text-overlap",
        overlap if title_words and len(text_words) >= 30 else None,
        "No substantive title token appears in the stored text; this may be legitimate.",
        "Compare title and text manually; do not infer an error from this heuristic alone.",
    )
    for field in ("title", "url"):
        count = (duplicates or {}).get(field, {}).get(record.get(field, "").strip().casefold(), 0)
        check(
            "consistency",
            field,
            "possible-duplicate",
            count <= 1,
            "Another record has the same normalized field.",
            "Compare records before deciding whether they are duplicates.",
        )
    check(
        "informativeness",
        "text",
        "text-length",
        len(text_words) >= 50,
        "Text contains fewer than 50 words.",
        "Check for truncation or a naturally short article.",
    )
    check(
        "informativeness",
        "text",
        "repetitive-text",
        len(set(text_words)) / max(1, len(text_words)) >= 0.15,
        "Fewer than 15% of text tokens are distinct.",
        "Inspect boilerplate or repeated content.",
    )
    dimensions = {}
    for name, values in checks.items():
        assessed = [v for v in values if v is not None]
        dimensions[name] = {
            "score": sum(assessed) / len(assessed) if assessed else None,
            "assessed_checks": len(assessed),
            "passed_checks": sum(assessed),
            "unassessed_checks": values.count(None),
        }
    complete = all(
        d["score"] is not None and not d["unassessed_checks"] for d in dimensions.values()
    )
    # These models consume title/text, not the stored language label or URL.
    reliability = 0.3 * bool(record.get("title", "").strip()) + 0.7 * min(1, len(text_words) / 100)
    if any(w["check_id"] in ("repetitive-text", "title-text-overlap") for w in warnings):
        reliability *= 0.5
    return {
        "status": "available",
        "metadata_version_id": version,
        "quality_run_id": run,
        "checks_version": VERSION,
        "dimensions": dimensions,
        "overall_score": mean(d["score"] for d in dimensions.values()) if complete else None,
        "content_reliability": reliability,
        "warnings": warnings,
    }
