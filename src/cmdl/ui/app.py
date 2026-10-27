"""Deskdrop reader interface, with explicit examples until product services exist."""

import os
import re
from urllib.parse import urlsplit

import streamlit as st

from cmdl.ui.client import SCENARIOS, ReaderClient, ServiceError, examples

st.set_page_config(page_title="Deskdrop · Reader", page_icon="📚", layout="wide")
st.title("Deskdrop")
st.write("Discover articles, understand recommendations, and inspect metadata quality.")

with st.sidebar:
    st.header("Reader")
    mode = st.radio("Data source", ["Examples", "Live API"])
    scenario = st.selectbox("Example scenario", list(SCENARIOS), disabled=mode != "Examples")
    if mode == "Examples":
        st.info("Illustrative examples. These are not trained recommendations or measured quality.")
    else:
        st.caption("Live recommendations require the product API and published model artifacts.")

client = ReaderClient(
    os.environ.get("API_BASE_URL", "http://localhost:8000") if mode == "Live API" else None,
    scenario,
)
fixture = examples()[SCENARIOS[scenario]]["body"]
default_user = fixture.get("person_id", "-8845298781299428018")
with st.form("recommendations"):
    person = st.text_input("User ID", value=default_user, key=f"user-{scenario}")
    limit = st.number_input("Maximum articles", min_value=1, max_value=10, value=10)
    submitted = st.form_submit_button("Find articles", type="primary")

# Any input/context change hides old success immediately, including unsent form edits.
identity = (mode, scenario, person, limit)
if st.session_state.get("identity") != identity:
    st.session_state.pop("ranking", None)
    st.session_state.pop("failure", None)
    st.session_state["identity"] = identity

if submitted:
    st.session_state.pop("ranking", None)
    st.session_state.pop("failure", None)
    if not re.fullmatch(r"-?\d+", person):
        st.session_state["failure"] = ("Enter an exact numeric user ID, including its sign.", False)
    else:
        try:
            with st.spinner("Finding articles…"):
                st.session_state["ranking"] = client.recommend(person, int(limit))
        except ServiceError as exc:
            st.session_state["failure"] = (str(exc), exc.retryable)

if failure := st.session_state.get("failure"):
    st.error(failure[0])
    if failure[1]:
        st.caption(
            "You can retry using Find articles. In examples, choose another scenario to recover."
        )


def show_quality(quality):
    st.subheader("Metadata quality")
    if quality["status"] != "available":
        st.info(f"Quality unavailable · {quality.get('reason', 'not assessed')}")
        return
    score = quality["overall_score"]
    st.write("Overall: partially assessed" if score is None else f"Overall: {score:.0%}")
    st.caption(
        "Scores summarize rule checks; they are not probabilities that an article is correct."
    )
    for name, dimension in quality["dimensions"].items():
        value = dimension["score"]
        label = "Unassessed" if value is None else f"{value:.0%}"
        st.write(f"**{name.title()}** · {label}")
        st.caption(
            f"{dimension['passed_checks']}/{dimension['assessed_checks']} assessed checks passed; "
            f"{dimension['unassessed_checks']} unassessed"
        )
    for warning in quality.get("warnings", []):
        st.warning(f"{warning['field']} · {warning['reason']}")


if ranking := st.session_state.get("ranking"):
    st.subheader("Your reading list")
    st.caption(f"Historical catalog as of {ranking['context']['as_of_at']}")
    if mode == "Examples":
        st.caption("Example user and result limit are fixed by the selected approved scenario.")
    if ranking["fallback_reasons"]:
        st.info("Popularity fallback · " + ", ".join(ranking["fallback_reasons"]))
    if ranking["short_result"]:
        st.info(f"Only {ranking['returned_count']} eligible articles are available in this result.")
    if not ranking["items"]:
        st.write("No eligible articles to show. Try another user or return later.")
    for index, item in enumerate(ranking["items"]):
        article = item["article"]
        metadata = article.get("metadata") or {}
        with st.container(border=True):
            st.subheader(f"{item['rank']}. {metadata.get('title') or 'Untitled article'}")
            st.caption(f"{metadata.get('lang', 'Unknown language')} · {item['source']}")
            st.write(item["explanation"]["text"])
            url = metadata.get("url", "")
            if urlsplit(url).scheme in ("http", "https"):
                st.link_button("Open article", url)
            with st.expander("Inspect article and quality"):
                if st.button("Load details", key=f"inspect-{index}"):
                    try:
                        with st.spinner("Loading article details…"):
                            detail, quality = client.inspect(item, ranking["context"])
                        st.write((detail.get("metadata") or {}).get("text", "No text available."))
                        show_quality(quality)
                        with st.expander("Record references"):
                            st.json(
                                {
                                    "article": detail,
                                    "snapshot_id": ranking["context"]["snapshot_id"],
                                }
                            )
                    except ServiceError as exc:
                        st.error(str(exc))
                        if exc.retryable:
                            st.caption("Use Load details to retry.")
