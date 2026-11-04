"""Browser visibility/active-time evidence, scoped to one reader navigation."""

from pathlib import Path

import streamlit as st

from cmdl.reading import ESTIMATE_RULE, required_seconds

_component = st.components.v2.component(
    "cmdl_reading_progress",
    html='<div data-reading-end aria-hidden="true" style="height:2px"></div>',
    js=Path(__file__).with_name("reader_progress.js").read_text(encoding="utf-8"),
)


def render_progress(navigation, text):
    result = _component(
        key="reading-progress-" + navigation["navigation_id"],
        data={
            **{k: navigation[k] for k in ("navigation_id", "person_id", "content_id")},
            "required_seconds": required_seconds(text),
            "rule_version": ESTIMATE_RULE,
        },
        default={"completion": None},
        on_completion_change=lambda: None,
        height=2,
    )
    return result.completion
