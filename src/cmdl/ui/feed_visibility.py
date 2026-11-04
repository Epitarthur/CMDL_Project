"""Visibility receipts for the actual feed card containers, not page generation."""

from pathlib import Path

import streamlit as st

_component = st.components.v2.component(
    "cmdl_feed_visibility",
    js=Path(__file__).with_name("feed_visibility.js").read_text(encoding="utf-8"),
)


def card_key(feed_id, index):
    return f"feedcard_{feed_id.replace('-', '')}_{index}"


def render_impressions(feed):
    result = _component(
        key="feed-visibility-" + feed["feed_id"],
        data={
            "feed_id": feed["feed_id"],
            "cards": [
                {
                    "content_id": item["content_id"],
                    "selector": ".st-key-" + card_key(feed["feed_id"], index),
                }
                for index, item in enumerate(feed["items"])
            ],
        },
        default={"report": None},
        on_report_change=lambda: None,
        height=1,
    )
    return result.report
