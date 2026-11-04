"Persistent reviewer operations, deliberately outside the reader workspace."

import json
from uuid import uuid4

import streamlit as st

from cmdl.ui.live_client import APIError, request


def render():
    st.header("Metadata quality review")
    st.caption(
        "Warnings are inspection signals. Confirming a warning does not change the source record."
    )
    try:
        report = request("GET", "/quality/report")
        columns = st.columns(3)
        for column, label, value in zip(
            columns,
            ("Catalog", "Assessed", "Warnings"),
            (report["catalog_records"], report["assessed_records"], report["warning_count"]),
            strict=True,
        ):
            column.metric(label, value)
        st.write("Saved decisions", report["review_decision_counts"])
        st.write("Dimension averages", report["dimension_means"])
        queue = request("GET", "/quality/queue")["articles"]
        if not queue:
            st.info("No warnings to review in the current catalog.")
            return
        selected = st.selectbox(
            "Article to inspect", queue, format_func=lambda r: r["title"] or r["content_id"]
        )
        article = request("GET", "/articles/" + selected["content_id"])["article"]
        quality = request("GET", "/quality/" + article["metadata_version_id"])["quality"]
        metadata = article["metadata"]
        st.subheader(metadata["title"] or "Untitled article")
        with st.expander("Stored source metadata"):
            st.json(metadata)
        st.caption("Record version: " + article["metadata_version_id"])
        if quality["status"] != "available":
            st.info("Quality results are unavailable for this exact version.")
            return
        st.json(quality["dimensions"], expanded=False)
        warnings = quality["warnings"]
        if warnings:
            warning = st.selectbox(
                "Warning", warnings, format_func=lambda w: w["field"] + " / " + w["check_id"]
            )
            st.warning(warning["reason"])
            st.write(warning["suggested_next_step"])
            with st.form("persistent-review"):
                reviewer = st.text_input("Reviewer label")
                decision = st.radio("Decision", ["confirm", "reject"], horizontal=True)
                comment = st.text_area("Comment (optional)")
                save = st.form_submit_button("Save decision", type="primary")
            if save:
                if not reviewer.strip():
                    st.error("Enter a reviewer label.")
                else:
                    payload = {
                        "snapshot_id": report["context"]["snapshot_id"],
                        "quality_run_id": quality["quality_run_id"],
                        "metadata_version_id": article["metadata_version_id"],
                        "reviewer_label": reviewer.strip(),
                        "decision": decision,
                        "comment": comment or None,
                    }
                    fingerprint = warning["warning_id"] + json.dumps(payload, sort_keys=True)
                    if st.session_state.get("review_fingerprint") != fingerprint:
                        st.session_state.review_fingerprint = fingerprint
                        st.session_state.review_request_id = str(uuid4())
                    request(
                        "POST",
                        "/quality/warnings/" + warning["warning_id"] + "/reviews",
                        json={**payload, "client_request_id": st.session_state.review_request_id},
                    )
                    st.success("Decision saved in PostgreSQL. Refresh to see the updated history.")
            st.subheader("Decision history")
            for review in warning["reviews"]:
                st.write(
                    f"{review['reviewer_label']} · {review['decision']} · {review['created_at']}"
                )
                if review.get("comment"):
                    st.write(review["comment"])
        with st.expander("Create a corrected record version"):
            st.caption(
                "Original metadata is preserved. New checks run immediately. "
                "Refresh content features with cmdl train after corrections."
            )
            with st.form("correction-" + article["metadata_version_id"]):
                editor = st.text_input("Editor label")
                fields = {
                    k: st.text_area(k.title(), value=metadata[k])
                    for k in ("title", "text", "url", "lang")
                }
                saved = st.form_submit_button("Create new version")
            if saved:
                if not editor.strip():
                    st.error("Enter an editor label.")
                else:
                    result = request(
                        "POST",
                        "/reviewer/articles/" + article["content_id"] + "/versions",
                        json={
                            **fields,
                            "content_type": metadata["content_type"],
                            "reviewer_label": editor.strip(),
                            "base_version_id": article["metadata_version_id"],
                            "client_request_id": str(uuid4()),
                        },
                    )
                    st.success(result["message"])
        if st.button("Refresh review data"):
            st.rerun()
    except APIError as exc:
        st.error(str(exc))
        if st.button("Retry review data"):
            st.rerun()
