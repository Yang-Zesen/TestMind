"""Idempotently migrate and seed the isolated local demonstration resources."""

import os

import psycopg
from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from pymilvus import DataType, MilvusClient


def main():
    load_dotenv("deploy/demo.env", override=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "true"
    for name in ("knowledge_alembic.ini", "graph_sync_alembic.ini", "task_alembic.ini"):
        command.upgrade(Config(name), "head")
    with psycopg.connect(os.environ["POSTGRES_DSN"]) as connection:
        for user, project, kb in (("demo-user", "demo-project", "demo-kb"), ("demo-other", "other-project", "other-kb")):
            connection.execute("INSERT INTO knowledge.knowledge_bases(id,project_id,name) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (kb, project, kb))
            connection.execute("INSERT INTO business.demo_users(user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user,))
            connection.execute("INSERT INTO business.demo_grants(user_id,project_id,kb_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (user, project, kb))
            connection.execute("""INSERT INTO graph_sync.project_memberships(project_id,user_id,role) VALUES (%s,%s,'employee') ON CONFLICT DO NOTHING""", (project, user))
    client = MilvusClient(os.environ["MILVUS_URI"])
    try:
        name = os.environ["MILVUS_COLLECTION"]
        if not client.has_collection(name):
            schema = client.create_schema(auto_id=False, enable_dynamic_field=True)
            schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
            for field in ("project_id", "knowledge_base_id", "document_id", "document_version", "index_version"):
                schema.add_field(field, DataType.VARCHAR, max_length=200)
            schema.add_field("content", DataType.VARCHAR, max_length=65535)
            schema.add_field("is_active", DataType.BOOL)
            schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=1024)
            schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
            indexes = client.prepare_index_params()
            indexes.add_index("dense_vector", index_type="AUTOINDEX", metric_type="COSINE")
            indexes.add_index("sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
            client.create_collection(name, schema=schema, index_params=indexes)
        print("TestMind demo migrations, two users, two project/KB grants and Milvus collection ready")
    finally:
        client.close()


if __name__ == "__main__":
    main()
