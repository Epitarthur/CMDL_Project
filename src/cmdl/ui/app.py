"""Development status screen. Reader/reviewer screens follow in T22/T23."""

import os

import httpx
import streamlit as st

st.set_page_config(page_title="CMDL development", page_icon="📚")
st.title("Deskdrop · development")
st.write("Recommendation and metadata quality evaluation")
st.button("Refresh status")
api_url = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
try:
    with httpx.Client(base_url=api_url, timeout=5) as client:
        health = client.get("/health")
        health.raise_for_status()
        if health.json().get("database") != "connected":
            raise ValueError("Unexpected health response")
        st.success("Streamlit → API → PostgreSQL connected")
        ready = client.get("/ready")
        if ready.status_code == 503:
            st.info("Application data is not ready. Snapshots and rankings will be added later.")
        else:
            ready.raise_for_status()
            st.success("Application data is ready")
except (httpx.HTTPError, ValueError):
    st.error("Could not verify service connectivity. Check the API and database, then refresh.")
st.caption("T05 environment preview · Reader and reviewer screens are planned in T22/T23.")
