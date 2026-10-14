"""Task migrations use their own version table."""

import os

from alembic import context
from sqlalchemy import create_engine

dsn = os.environ["POSTGRES_DSN"]
if dsn.startswith("postgresql://"):
    dsn = dsn.replace("postgresql://", "postgresql+psycopg://", 1)
if context.is_offline_mode():
    context.configure(url=dsn, literal_binds=True, version_table="task_alembic_version")
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(dsn).connect() as connection:
        context.configure(connection=connection, version_table="task_alembic_version")
        with context.begin_transaction():
            context.run_migrations()
