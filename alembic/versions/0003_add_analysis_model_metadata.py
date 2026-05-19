"""add analysis model metadata

Revision ID: 0003_add_analysis_model_metadata
Revises: 0002_add_source_path
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0003_add_analysis_model_metadata"
down_revision = "0002_add_source_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("mistake_analyses")}
    if "ai_model_used" not in columns:
        op.add_column("mistake_analyses", sa.Column("ai_model_used", sa.String(length=120), nullable=False, server_default=""))
    if "ai_model_mode" not in columns:
        op.add_column("mistake_analyses", sa.Column("ai_model_mode", sa.String(length=30), nullable=False, server_default="auto"))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("mistake_analyses")}
    if "ai_model_mode" in columns:
        op.drop_column("mistake_analyses", "ai_model_mode")
    if "ai_model_used" in columns:
        op.drop_column("mistake_analyses", "ai_model_used")
