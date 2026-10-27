"""Version-matched reviewer workflow; example decisions are explicitly ephemeral."""

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import streamlit as st

from cmdl.ui.client import ReviewerClient, ServiceError, examples

QUALITY_SCENARIOS = {
    "Warnings": "quality_available",
    "Partial assessment": "quality_partial",
    "Review history": "quality_review_history",
    "Quality unavailable": "quality_unavailable",
}


def render(api_url):
    client = ReviewerClient(api_url)
    demo = api_url is None
    st.header("Metadata review")
    st.write("Inspect the original record, assess warnings, and record your decision.")
    if demo:
        st.info("Illustrative data. Example decisions are stored only in this browser session.")
    try:
        report_fixture = "quality_report_partial_coverage"
        if demo:
            report_fixture = st.selectbox(
                "Collection example", ["quality_report_partial_coverage", "quality_report_no_run"]
            )
        with st.spinner("Loading collection summary…"):
            report = client.report(report_fixture)
        snapshot = report["context"]["snapshot_id"]
        columns = st.columns(3)
        for column, label, value in zip(
            columns,
            ("Catalog records", "Assessed records", "Unassessed records"),
            (report["catalog_records"], report["assessed_records"], report["unassessed_records"]),
            strict=True,
        ):
            column.metric(label, value)
        if report["status"] != "available":
            st.info(f"Collection quality unavailable · {report['reason']}")
        else:
            st.caption(
                f"{report['warning_count']} warnings · "
                "Review counts are decision events, not confirmed errors."
            )
            st.write("Saved decision events", report["review_decision_counts"])
            with st.expander("Collection dimension summaries"):
                for name, score in report["dimension_means"].items():
                    value = "Unassessed" if score is None else f"{score:.0%}"
                    st.write(
                        f"{name.title()}: {value} · "
                        f"{report['dimension_denominators'][name]} records"
                    )

        fixture = "quality_available"
        if demo:
            scenario = st.selectbox("Record example", list(QUALITY_SCENARIOS))
            fixture = QUALITY_SCENARIOS[scenario]
            version = examples()[fixture]["body"]["quality"]["metadata_version_id"]
            content_id = examples()["article"]["body"]["article"]["content_id"]
            if fixture == "quality_unavailable":
                st.info("Quality unavailable · not_run. No warning can be reviewed.")
                return
        else:
            content_id = st.text_input("Article ID")
            version = st.text_input("Metadata version ID")
            if not content_id or not version:
                st.caption("Enter the article and metadata version to inspect its warnings.")
                return
            if not re.fullmatch(r"-?\d+", content_id):
                st.error("Enter an exact numeric article ID, including its sign.")
                return
            try:
                UUID(version)
            except ValueError:
                st.error("Enter a valid metadata version UUID.")
                return
        with st.spinner("Loading matching record and quality…"):
            article = client.article(content_id, version, snapshot)
            quality = client.quality(version, snapshot, fixture)
        metadata = article.get("metadata") or {}
        st.subheader(metadata.get("title") or "Untitled article")
        with st.expander("Original metadata", expanded=True):
            st.write(metadata.get("text") or "No text supplied")
            st.json(metadata)
        if quality["status"] != "available":
            st.info(f"Quality unavailable · {quality.get('reason', 'not assessed')}")
            return
        if quality["overall_score"] is None:
            st.info("Partially assessed. Unassessed checks do not count as passing.")
        st.caption(
            "Warnings are inspection signals; review decisions do not edit source fields or scores."
        )
        if not quality["warnings"]:
            st.write("No warnings in this assessment.")
            return
        warnings = {warning["warning_id"]: warning for warning in quality["warnings"]}
        selected = st.selectbox(
            "Warning",
            list(warnings),
            format_func=lambda key: f"{warnings[key]['field']} · {warnings[key]['check_id']}",
        )
        warning = warnings[selected]
        st.warning(warning["reason"])
        st.caption(f"{warning['dimension']} · {warning['severity']}")
        scope = (demo, snapshot, version, quality["quality_run_id"], selected)
        if st.session_state.get("review_scope") != scope:
            st.session_state.pop("review_pending", None)
            st.session_state["review_scope"] = scope
        with st.form("review-decision"):
            label = st.text_input("Reviewer label", max_chars=100)
            decision = st.radio("Decision", ["confirm", "reject"], horizontal=True)
            comment = st.text_area("Comment (optional)", max_chars=1000)
            submitted = st.form_submit_button("Save example decision" if demo else "Save decision")
        history = list(warning.get("reviews", []))
        session_reviews = st.session_state.setdefault("example_reviews", {})
        if demo:
            history.extend(session_reviews.get(selected, []))
        if submitted:
            if not label.strip():
                st.error("Enter a nonblank reviewer label.")
            else:
                semantic = {
                    "snapshot_id": snapshot,
                    "quality_run_id": quality["quality_run_id"],
                    "metadata_version_id": version,
                    "reviewer_label": label.strip(),
                    "decision": decision,
                    "comment": comment or None,
                }
                pending = st.session_state.get("review_pending")
                if pending is None or pending["semantic"] != semantic:
                    pending = {"semantic": semantic, "key": str(uuid4())}
                    st.session_state["review_pending"] = pending
                payload = {"client_request_id": pending["key"], **semantic}
                if demo:
                    saved = next(
                        (r for r in history if r["client_request_id"] == pending["key"]), None
                    )
                    if saved is None:
                        saved = {
                            **payload,
                            "warning_id": selected,
                            "review_id": str(uuid4()),
                            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                        }
                        session_reviews.setdefault(selected, []).append(saved)
                        history.append(saved)
                    st.success("Example decision saved for this session only.")
                else:
                    saved = client.submit(selected, payload)
                    if not any(r["review_id"] == saved["review_id"] for r in history):
                        history.append(saved)
                    st.success("Decision saved by the service.")
        st.subheader("Decision history")
        if not history:
            st.write("No decisions recorded for this warning.")
        for review in sorted(history, key=lambda r: (r["created_at"], r["review_id"])):
            st.write(
                f"**{review['reviewer_label']}** · {review['decision']} · {review['created_at']}"
            )
            if review["comment"]:
                st.write(review["comment"])
        with st.expander("Assessment references"):
            st.json(
                {
                    "snapshot_id": snapshot,
                    "quality_run_id": quality["quality_run_id"],
                    "metadata_version_id": version,
                    "checks_version": quality["checks_version"],
                }
            )
    except ServiceError as exc:
        st.error(str(exc))
        if exc.retryable:
            st.caption(
                "Use Refresh review data to retry. A save retry retains the same request key."
            )
    st.button("Refresh review data")
