"""Versioned, heuristic reading estimate policy; never a confirmed read."""

import math
import re

ESTIMATE_RULE = "active-dwell-bottom-v1"


def required_seconds(text):
    """Half the reading time at 240 words/minute, bounded to 10–120 seconds."""
    words = len(re.findall(r"\w+", text))
    return max(10, min(120, math.ceil(words / 8)))
