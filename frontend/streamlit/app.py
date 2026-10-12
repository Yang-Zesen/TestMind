"""Streamlit operator view; all project scope comes from the API response."""

from __future__ import annotations

import os
from uuid import uuid4

import streamlit as st

from frontend.streamlit.api_client import KnowledgeApiClient, KnowledgeApiError


def main() -> None:
    st.set_page_config(page_title="TestMind Knowledge", layout="wide")
    st.title("Knowledge ingestion")
    with KnowledgeApiClient(os.getenv("TESTMIND_API_URL", "http://127.0.0.1:8000"), token=os.getenv("TESTMIND_API_TOKEN")) as client:
        try:
            bases = client.list_knowledge_bases()
        except KnowledgeApiError as exc:
            st.error(f"Unable to load authorized knowledge bases ({exc.status_code})")
            return
        if not bases:
            st.info("No authorized knowledge bases are available.")
            return
        selected = st.selectbox("Knowledge base", bases, format_func=lambda item: item.get("name", item.get("id", "unknown")))
        project_id, kb_id = selected["project_id"], selected["id"]
        upload = st.file_uploader("Upload PDF, Markdown, or text", type=["pdf", "md", "txt"])
        if upload is not None and st.button("Submit upload", type="primary"):
            try:
                result = client.upload_document(project_id, kb_id, upload.name, upload.getvalue(), idempotency_key=str(uuid4()))
                st.success(f"Queued job {result.get('job_id', 'unknown')}")
            except KnowledgeApiError as exc:
                st.error(f"Upload failed ({exc.status_code}): {exc}")
        documents = client.list_documents(project_id, kb_id)
        jobs = client.list_jobs(project_id, kb_id)
        left, right = st.columns(2)
        with left:
            st.subheader("Documents")
            st.dataframe(documents, use_container_width=True, hide_index=True)
        with right:
            st.subheader("Jobs")
            st.dataframe(jobs, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
