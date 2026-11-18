"""Platform tasks, fenced actions, artifacts, events and verified episodes."""
from alembic import op
from code_assistant.persistence.database import metadata

revision = "001_platform"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(connection)


def downgrade():
    raise RuntimeError("任务含用户数据；不提供自动破坏性降级")
