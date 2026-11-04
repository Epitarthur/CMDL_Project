"""Serving-only language and repeated-exposure adjustments; no model fitting."""

from collections import Counter

VERSION = "reader-feedback-v1"


def language_strength(reads):
    """One strongest signal per article, decaying with a seven-day half-life."""
    strengths = Counter()
    for row in reads:
        if row["age_seconds"] <= 30 * 86400 and row["lang"]:
            strengths[row["lang"].strip().lower()] += row["weight"] * 0.5 ** (
                max(0, row["age_seconds"]) / 604800
            )
    return dict(strengths)


def exposure_strength(impressions):
    """Ignore one exposure; repeated exposures cool off with a one-day half-life."""
    strengths = Counter()
    for row in impressions:
        if row["age_seconds"] <= 7 * 86400:
            strengths[row["content_id"]] += 0.5 ** (max(0, row["age_seconds"]) / 86400)
    return dict(strengths)


def adjust(ranked, catalog, mode, languages, exposures):
    best = max(languages.values(), default=0)
    result = []
    for item in ranked:
        lang = catalog[item["content_id"]].get("lang", "").strip().lower()
        if mode not in ("auto", "all") and lang != mode:
            continue
        language_factor = (
            0.25 + 0.75 * (1 + languages.get(lang, 0)) / (1 + best)
            if mode == "auto" and best
            else 1.0
        )
        seen = exposures.get(item["content_id"], 0)
        # Two recent impressions start a penalty; it expires rather than hiding an item.
        exposure_factor = max(0.15, 1 / (1 + 1.5 * max(0, seen - 1)))
        base = item.get("score", 0)
        score = max(base, 1e-12) * language_factor * exposure_factor
        explanation = dict(item["explanation"])
        if mode == "auto" and best and languages.get(lang, 0) == best:
            explanation["text"] += " Your recent reading favors this language."
        elif mode not in ("auto", "all"):
            explanation["text"] += " Matches your selected language."
        result.append(
            {
                **item,
                "explanation": explanation,
                "signals": {
                    **item["signals"],
                    "base_score": base,
                    "language_multiplier": language_factor,
                    "exposure_multiplier": exposure_factor,
                    "recent_exposure_strength": seen,
                    "serving_score": score,
                },
            }
        )
    return sorted(result, key=lambda item: -item["signals"]["serving_score"])
