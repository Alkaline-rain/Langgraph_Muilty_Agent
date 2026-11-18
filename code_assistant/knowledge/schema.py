"""Separate metadata keeps historical migration 001 immutable."""
from sqlalchemy import MetaData, Table, Column, String, Text, JSON, Float, Integer, ForeignKey, UniqueConstraint
from pgvector.sqlalchemy import Vector

metadata = MetaData()
documents = Table("knowledge_documents", metadata,
    Column("id", String(64), primary_key=True), Column("source_uri", Text, nullable=False),
    Column("domain", String(40), nullable=False), Column("library", String(80), nullable=False),
    Column("title", Text), UniqueConstraint("source_uri", "domain"))
versions = Table("knowledge_versions", metadata,
    Column("id", String(64), primary_key=True),
    Column("document_id", ForeignKey("knowledge_documents.id"), nullable=False, index=True),
    Column("content_hash", String(64)), Column("source_revision", Text),
    Column("supported_versions", JSON), Column("splitter_version", String(40)),
    Column("license_note", Text), Column("created_at", Float))
chunks = Table("knowledge_chunks", metadata,
    Column("id", String(64), primary_key=True),
    Column("version_id", ForeignKey("knowledge_versions.id"), nullable=False, index=True),
    Column("parent_id", ForeignKey("knowledge_chunks.id"), index=True),
    Column("kind", String(8), nullable=False), Column("ordinal", Integer),
    Column("text", Text), Column("heading", Text), Column("start_line", Integer),
    Column("end_line", Integer), Column("content_hash", String(64)))
profiles = Table("knowledge_profiles", metadata,
    Column("id", String(64), primary_key=True), Column("model", String(200)),
    Column("dimensions", Integer, nullable=False))
# Profiles have immutable dimension checks; vectors are native pgvector, not JSON casts.
# Generic vector allows separate profiles without a runtime-dependent migration.
embeddings = Table("knowledge_embeddings", metadata,
    Column("child_id", ForeignKey("knowledge_chunks.id"), primary_key=True),
    Column("profile_id", ForeignKey("knowledge_profiles.id"), primary_key=True),
    Column("vector", Vector().with_variant(JSON(), "sqlite"), nullable=False))
snapshots = Table("knowledge_snapshots", metadata,
    Column("id", String(64), primary_key=True), Column("created_at", Float),
    Column("profile_id", ForeignKey("knowledge_profiles.id"), nullable=True))
members = Table("knowledge_snapshot_members", metadata,
    Column("snapshot_id", ForeignKey("knowledge_snapshots.id"), primary_key=True),
    Column("version_id", ForeignKey("knowledge_versions.id"), primary_key=True, index=True))
active = Table("knowledge_active", metadata,
    Column("domain", String(40), primary_key=True),
    Column("snapshot_id", ForeignKey("knowledge_snapshots.id"), nullable=False))
runs = Table("retrieval_runs", metadata,
    Column("id", String(64), primary_key=True), Column("task_id", String(36), index=True),
    Column("action_key", String(100)), Column("snapshot_id", String(64)),
    Column("query_hash", String(64)), Column("result", JSON), Column("created_at", Float),
    UniqueConstraint("task_id", "action_key"))
