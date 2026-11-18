"""Versioned domain knowledge, native pgvector and retrieval evidence."""
from alembic import op
from code_assistant.knowledge.schema import metadata

revision = "002_knowledge"
down_revision = "001_platform"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    metadata.create_all(connection)
    if connection.dialect.name == "postgresql":
        op.execute("""
        CREATE FUNCTION knowledge_check_vector_dimension() RETURNS trigger AS $$
        BEGIN
          IF vector_dims(NEW.vector) <> (SELECT dimensions FROM knowledge_profiles WHERE id=NEW.profile_id)
          THEN RAISE EXCEPTION 'embedding dimension mismatch'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql
        """)
        op.execute("""CREATE TRIGGER knowledge_vector_dimension BEFORE INSERT OR UPDATE
            ON knowledge_embeddings FOR EACH ROW EXECUTE FUNCTION knowledge_check_vector_dimension()""")


def downgrade():
    raise RuntimeError("Knowledge snapshots may be referenced by tasks; automatic deletion disabled")
