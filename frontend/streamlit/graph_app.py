"""Standalone graph operations view; no direct database connections."""

import os

import streamlit as st

from frontend.streamlit.api_client import KnowledgeApiError
from frontend.streamlit.graph_client import GraphApiClient


def main() -> None:
    st.set_page_config(page_title="TestMind Graph", layout="wide")
    st.title("Relationship graph")
    with GraphApiClient(os.getenv("TESTMIND_API_URL", "http://127.0.0.1:26104"),
                        token=os.getenv("TESTMIND_API_TOKEN")) as client:
        try:
            projects = client.projects()
            if not projects:
                st.info("No authorized projects")
                return
            project = st.selectbox("Project", projects, format_func=lambda item: item["project_id"])
            project_id = project["project_id"]
            if st.button("Refresh", icon=":material/refresh:"):
                st.rerun()
            views = ["Entities", "Relationships", "Metrics"]
            if project["can_maintain"]:
                views.append("Maintenance")
            view = st.radio("View", views, horizontal=True)
            if view in {"Entities", "Relationships"}:
                limit = st.number_input("Limit", min_value=1, max_value=200, value=50)
                page_key = f"graph_pages:{project_id}:{view}:{limit}"
                history = st.session_state.setdefault(page_key, [None])
                result = client.query(project_id, view.lower(), limit=int(limit), cursor=history[-1])
                if result["stale"]:
                    st.warning("Projection is behind the authoritative state")
                st.dataframe(result["items"], hide_index=True, use_container_width=True)
                previous, following = st.columns(2)
                if previous.button("Previous", icon=":material/arrow_back:", disabled=len(history) == 1):
                    history.pop()
                    st.rerun()
                if following.button("Next", icon=":material/arrow_forward:", disabled=not result.get("next_cursor")):
                    history.append(result["next_cursor"])
                    st.rerun()
            elif view == "Metrics":
                result = client.metrics(project_id)
                columns = st.columns(4)
                for column, (label, key) in zip(columns, (
                    ("Test cases", "test_cases"), ("Scripts", "scripts"),
                    ("Runs", "test_runs"), ("Bugs", "bugs"),
                ), strict=True):
                    column.metric(label, result[key])
                st.metric("Automation rate", "N/A" if result["automation_rate"] is None
                          else f"{result['automation_rate']:.1%}")
                if "sync_health" in result:
                    st.json(result["sync_health"])
            else:
                resource = st.selectbox("Resource", ["project", "member", "requirement", "test_case", "bug"])
                operations = st.columns(3)
                for column, operation in zip(operations, ("Pull", "Reconcile", "Rebuild"), strict=True):
                    if column.button(operation, key=operation):
                        result = client.pull(project_id, resource) if operation == "Pull" else client.maintain(project_id, operation.lower())
                        st.session_state[f"graph_job:{project_id}"] = result["job_id"]
                job_id = st.session_state.get(f"graph_job:{project_id}")
                if job_id:
                    st.json(client.job(job_id))
        except KnowledgeApiError as exc:
            st.error(f"Graph request failed ({exc.status_code}): {exc}")


if __name__ == "__main__":
    main()
