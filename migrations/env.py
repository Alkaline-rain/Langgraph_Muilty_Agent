from alembic import context
from sqlalchemy import create_engine
from backend.settings import database_url
from code_assistant.persistence.database import metadata

with create_engine(database_url(), connect_args={"connect_timeout": 5} if database_url().startswith("postgresql") else {}).connect() as connection:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()
