"Persistent article feed and nonblocking Up next reader flow."

from time import monotonic
from uuid import uuid4

import streamlit as st

from cmdl.quality.checks import valid_url
from cmdl.ui.feed_visibility import card_key, render_impressions
from cmdl.ui.live_client import APIError, matches, request
from cmdl.ui.reading_progress import render_progress

st.set_page_config(page_title="CMDL · Deskdrop", page_icon="📚", layout="wide")
st.title("Deskdrop")
st.caption(
    "Articles worth your next read · historical CI&T catalog with a separate live reading history"
)
state = st.session_state
if "reader_session" not in state:
    state.reader_session = str(uuid4())

with st.sidebar:
    workspace = st.radio("Workspace", ["Reader", "Reviewer"])
    st.caption(
        "Real dataset and persisted services. Original Deskdrop text is shown inside the app."
    )
if workspace == "Reviewer":
    from cmdl.ui.live_reviewer import render

    render()
    st.stop()

try:
    accounts = request("GET", "/demo/accounts")["accounts"]
except APIError as exc:
    st.error(str(exc))
    st.info("Setup: migrate, ingest, then train the model release. See README.md.")
    if st.button("Retry connection"):
        st.rerun()
    st.stop()

options = {"Anonymous": None, "New demo reader": "0"}
options.update(
    {
        f"Reader {n + 1} · {a['historical_articles']} historical articles": a["person_id"]
        for n, a in enumerate(accounts)
    }
)
with st.sidebar:
    label = st.selectbox("Demo account", list(options), index=2 if accounts else 0)
    person = options[label]
    st.caption(
        "Historical interactions and new views exclude articles from "
        "future suggestions. Reading progress is saved automatically."
    )
if state.get("active_person", "unset") != person:
    try:
        request(
            "POST",
            "/reading/reset",
            json={
                "session_id": state.reader_session,
                "person_id": person,
                "navigation_id": str(uuid4()),
            },
        )
    except APIError as exc:
        st.error(str(exc))
        st.stop()
    for key in (
        "opened",
        "navigation",
        "pending_open",
        "feed",
        "completion_key",
        "estimated",
        "estimate_evidence",
        "estimate_retry_at",
        "language_settings",
        "preference_update",
        "impressions",
    ):
        state.pop(key, None)
    state.active_person = person


with st.sidebar:
    try:
        if "language_settings" not in state:
            params = {"session_id": state.reader_session}
            if person is not None:
                params["person_id"] = person
            state.language_settings = request("GET", "/reader/preferences", params=params)
        settings = state.language_settings
        languages = {"Learn from my reading": "auto", "All languages": "all"}
        names = {"en": "English", "pt": "Português"}
        languages.update(
            {names.get(code, code.upper()): code for code in settings["available_languages"]}
        )
        selection = st.selectbox(
            "Article language",
            list(languages),
            index=list(languages.values()).index(settings["language"]),
            key="language-choice-" + str(person),
        )
        if languages[selection] != settings["language"]:
            pending = state.get("preference_update")
            if not pending or pending["language"] != languages[selection]:
                pending = {
                    "session_id": state.reader_session,
                    "person_id": person,
                    "language": languages[selection],
                    "client_request_id": str(uuid4()),
                }
                state.preference_update = pending
            request("PUT", "/reader/preferences", json=pending)
            settings["language"] = languages[selection]
            state.pop("feed", None)
            state.pop("preference_update", None)
            st.rerun()
    except APIError as exc:
        st.error(str(exc))


def open_item(content_id):
    pending = state.get("pending_open")
    if pending is None or pending["content_id"] != content_id or pending["person_id"] != person:
        pending = {
            "content_id": content_id,
            "person_id": person,
            "session_id": state.reader_session,
            "navigation_id": str(uuid4()),
            "client_request_id": str(uuid4()),
            "prefetch_id": str(uuid4()),
        }
        state.pending_open = pending
    opened = request("POST", "/reading/open", json=pending)
    state.opened = opened["article"]
    state.navigation = pending
    state.completion_key = str(uuid4())
    state.estimated = False
    state.pop("estimate_evidence", None)
    state.pop("estimate_retry_at", None)
    state.pop("feed", None)
    state.pop("pending_open", None)


def card(item, key, container_key=None):
    metadata = item["article"]["metadata"]
    with st.container(border=True, key=container_key):
        st.subheader(metadata["title"] or "Untitled article")
        st.caption(metadata["lang"].upper() + " · " + item["explanation"]["text"])
        st.write(
            metadata["text"][:300].replace("\n", " ") + ("…" if len(metadata["text"]) > 300 else "")
        )
        if st.button("Read article", key=key, type="primary"):
            try:
                open_item(item["content_id"])
                st.rerun()
            except APIError as exc:
                st.error(str(exc))
                st.caption("Click Read article again to retry safely.")


if "opened" not in state:
    st.header("Your article feed")
    if st.button("Refresh feed"):
        state.pop("feed", None)
    try:
        if "feed" not in state:
            params = {"session_id": state.reader_session, "limit": 10}
            state.feed = request(
                "GET", "/feed" if person is None else "/recommend/" + person, params=params
            )
        feed = state.feed
        st.caption(
            f"{feed['returned_count']} suggestions · catalog as of {feed['context']['as_of_at']}"
        )
        if not feed["items"]:
            st.info(
                "No eligible unread articles are available. Choose "
                "another language or account, or check again later."
            )
        for index, item in enumerate(feed["items"]):
            card(item, "feed-" + item["content_id"], card_key(feed["feed_id"], index))
        report = render_impressions(feed)
        if state.get("impressions", {}).get("feed_id") != feed["feed_id"]:
            state.impressions = {
                "feed_id": feed["feed_id"],
                "pending": set(),
                "saved": set(),
                "retry_at": 0,
            }
        if report and report.get("feed_id") == feed["feed_id"]:
            state.impressions["pending"].update(
                set(report["content_ids"]) - state.impressions["saved"]
            )

        @st.fragment(run_every="2s")
        def save_impressions():
            queue = state.get("impressions")
            if not queue or queue["feed_id"] != state.get("feed", {}).get("feed_id"):
                return
            if queue["pending"] and monotonic() >= queue["retry_at"]:
                try:
                    saved = request(
                        "POST",
                        "/feed/impressions",
                        json={
                            "session_id": state.reader_session,
                            "person_id": person,
                            "feed_id": queue["feed_id"],
                            "content_ids": sorted(queue["pending"]),
                            "visible_ms": 1000,
                        },
                    )
                    queue["saved"].update(saved["recorded"])
                    queue["pending"].difference_update(saved["recorded"])
                except APIError:
                    queue["retry_at"] = monotonic() + 5

        save_impressions()
    except APIError as exc:
        st.error(str(exc))
        if st.button("Retry feed"):
            state.pop("feed", None)
            st.rerun()
else:
    article = state.opened
    navigation = state.navigation
    if st.button("← Back to feed"):
        request(
            "POST",
            "/reading/reset",
            json={
                "session_id": state.reader_session,
                "person_id": person,
                "navigation_id": str(uuid4()),
            },
        )
        state.pop("opened", None)
        state.pop("navigation", None)
        st.rerun()
    metadata = article["metadata"]
    st.header(metadata["title"] or "Untitled article")
    st.caption(metadata["lang"].upper())
    if valid_url(metadata["url"]):
        st.link_button("Original source", metadata["url"])
    st.markdown(metadata["text"])
    evidence = render_progress(navigation, metadata["text"])
    if evidence and matches(evidence, navigation):
        state.estimate_evidence = evidence
    st.divider()

    @st.fragment(run_every="1s")
    def up_next():
        current = state.get("navigation")
        if not current or current["navigation_id"] != navigation["navigation_id"]:
            return
        evidence = state.get("estimate_evidence")
        if not state.estimated and evidence and matches(evidence, current):
            if monotonic() >= state.get("estimate_retry_at", 0):
                try:
                    request(
                        "POST",
                        "/reading/estimate",
                        json={
                            k: current[k]
                            for k in ("content_id", "person_id", "session_id", "navigation_id")
                        }
                        | {
                            k: evidence[k]
                            for k in ("active_seconds", "end_visible", "rule_version")
                        }
                        | {
                            "client_request_id": state.completion_key,
                            "metadata_version_id": article["metadata_version_id"],
                        },
                    )
                    state.estimated = True
                except APIError:
                    state.estimate_retry_at = monotonic() + 5
            if not state.estimated:
                st.caption("Reading progress could not be saved yet. Retrying automatically…")
        st.subheader("Up next")
        try:
            response = request(
                "GET",
                "/next/" + current["prefetch_id"],
                params={
                    "session_id": current["session_id"],
                    "navigation_id": current["navigation_id"],
                },
            )
            if not matches(response, current):
                return
            if response["status"] == "pending":
                st.info("Preparing your next article… You can keep reading.")
            elif response["status"] == "failed":
                st.error(response["error"])
            elif response["result"]["items"]:
                card(response["result"]["items"][0], "next-" + current["navigation_id"])
            else:
                st.info(
                    "You have reached the end of the eligible catalog. No unread articles remain."
                )
            if response["status"] in ("pending", "failed") and st.button("Retry next article"):
                request(
                    "POST",
                    "/next",
                    json={
                        k: current[k]
                        for k in (
                            "session_id",
                            "person_id",
                            "content_id",
                            "navigation_id",
                            "prefetch_id",
                        )
                    },
                )
                st.rerun(scope="fragment")
        except APIError as exc:
            st.error(str(exc))
            if st.button("Retry next recommendation"):
                request(
                    "POST",
                    "/next",
                    json={
                        k: current[k]
                        for k in (
                            "session_id",
                            "person_id",
                            "content_id",
                            "navigation_id",
                            "prefetch_id",
                        )
                    },
                )
                st.rerun(scope="fragment")

    up_next()
