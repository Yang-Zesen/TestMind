from pathlib import Path

from frontend.streamlit import graph_client
from streamlit.testing.v1 import AppTest


def test_operator_page_renders_authorized_projects_and_entities(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def projects(self):
            return [{"project_id": "p1", "role": "employee", "can_maintain": False}]

        def query(self, *args, **kwargs):
            return {"items": [{"entity_key": "key1", "entity_type": "requirement"}], "stale": False}

    monkeypatch.setattr(graph_client, "GraphApiClient", Client)
    path = Path(__file__).parents[2] / "frontend" / "streamlit" / "graph_app.py"
    app = AppTest.from_file(str(path)).run(timeout=20)
    assert not app.exception
    assert app.selectbox[0].value["project_id"] == "p1"
    assert len(app.dataframe) == 1
    assert "Maintenance" not in app.radio[0].options
