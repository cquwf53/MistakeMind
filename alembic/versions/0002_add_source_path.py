"""add source path

Revision ID: 0002_add_source_path
Revises: 0001_initial
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0002_add_source_path"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("mistakes")}
    if "source_path" not in columns:
        op.add_column("mistakes", sa.Column("source_path", sa.String(length=255), nullable=False, server_default=""))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("mistakes")}
    if "source_path" in columns:
        op.drop_column("mistakes", "source_path")
