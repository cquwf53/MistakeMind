"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_subjects_code"), "subjects", ["code"], unique=True)

    op.create_table(
        "knowledge_points",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("level1", sa.String(length=80), nullable=False),
        sa.Column("level2", sa.String(length=80), nullable=False),
        sa.Column("level3", sa.String(length=120), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "level1", "level2", "level3"),
    )
    op.create_index(op.f("ix_knowledge_points_subject_id"), "knowledge_points", ["subject_id"], unique=False)

    op.create_table(
        "mistakes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("user_error", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_mistakes_is_confirmed"), "mistakes", ["is_confirmed"], unique=False)
    op.create_index(op.f("ix_mistakes_status"), "mistakes", ["status"], unique=False)
    op.create_index(op.f("ix_mistakes_subject_id"), "mistakes", ["subject_id"], unique=False)

    op.create_table(
        "mistake_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mistake_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_point_id", sa.Integer(), nullable=True),
        sa.Column("knowledge_path", sa.String(length=255), nullable=False),
        sa.Column("error_type", sa.String(length=50), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=False),
        sa.Column("variant_question", sa.Text(), nullable=False),
        sa.Column("review_suggestion", sa.Text(), nullable=False),
        sa.Column("user_revision_note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["knowledge_point_id"], ["knowledge_points.id"]),
        sa.ForeignKeyConstraint(["mistake_id"], ["mistakes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mistake_id"),
    )
    op.create_index(op.f("ix_mistake_analyses_error_type"), "mistake_analyses", ["error_type"], unique=False)
    op.create_index(op.f("ix_mistake_analyses_mistake_id"), "mistake_analyses", ["mistake_id"], unique=True)

    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("mistake_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("task_type", sa.String(length=40), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["mistake_id"], ["mistakes.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_review_tasks_due_date"), "review_tasks", ["due_date"], unique=False)
    op.create_index(op.f("ix_review_tasks_mistake_id"), "review_tasks", ["mistake_id"], unique=False)
    op.create_index(op.f("ix_review_tasks_status"), "review_tasks", ["status"], unique=False)
    op.create_index(op.f("ix_review_tasks_subject_id"), "review_tasks", ["subject_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_review_tasks_subject_id"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_status"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_mistake_id"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_due_date"), table_name="review_tasks")
    op.drop_table("review_tasks")
    op.drop_index(op.f("ix_mistake_analyses_mistake_id"), table_name="mistake_analyses")
    op.drop_index(op.f("ix_mistake_analyses_error_type"), table_name="mistake_analyses")
    op.drop_table("mistake_analyses")
    op.drop_index(op.f("ix_mistakes_subject_id"), table_name="mistakes")
    op.drop_index(op.f("ix_mistakes_status"), table_name="mistakes")
    op.drop_index(op.f("ix_mistakes_is_confirmed"), table_name="mistakes")
    op.drop_table("mistakes")
    op.drop_index(op.f("ix_knowledge_points_subject_id"), table_name="knowledge_points")
    op.drop_table("knowledge_points")
    op.drop_index(op.f("ix_subjects_code"), table_name="subjects")
    op.drop_table("subjects")
