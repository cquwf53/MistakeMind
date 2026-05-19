from datetime import date, datetime, timezone
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Subject(Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))

    knowledge_points: Mapped[list["KnowledgePoint"]] = relationship(back_populates="subject")
    mistakes: Mapped[list["Mistake"]] = relationship(back_populates="subject")


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"
    __table_args__ = (UniqueConstraint("subject_id", "level1", "level2", "level3"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    level1: Mapped[str] = mapped_column(String(80))
    level2: Mapped[str] = mapped_column(String(80))
    level3: Mapped[str] = mapped_column(String(120))
    keywords: Mapped[str] = mapped_column(Text, default="")

    subject: Mapped[Subject] = relationship(back_populates="knowledge_points")
    analyses: Mapped[list["MistakeAnalysis"]] = relationship(back_populates="knowledge_point")

    @property
    def path(self) -> str:
        return f"{self.level1} / {self.level2} / {self.level3}"


class Mistake(Base):
    __tablename__ = "mistakes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    source_type: Mapped[str] = mapped_column(String(30), default="manual")
    source_name: Mapped[str] = mapped_column(String(255), default="")
    source_path: Mapped[str] = mapped_column(String(255), default="")
    question_text: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text, default="")
    user_error: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    subject: Mapped[Subject] = relationship(back_populates="mistakes")
    analysis: Mapped["MistakeAnalysis"] = relationship(back_populates="mistake", uselist=False, cascade="all, delete-orphan")
    review_tasks: Mapped[list["ReviewTask"]] = relationship(back_populates="mistake", cascade="all, delete-orphan")

    @property
    def mastery_status(self) -> str:
        if not self.is_confirmed:
            return "未掌握"
        if self.review_tasks and all(task.status == "completed" for task in self.review_tasks):
            return "已掌握"
        return "复习中"


class MistakeAnalysis(Base):
    __tablename__ = "mistake_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mistake_id: Mapped[int] = mapped_column(ForeignKey("mistakes.id"), unique=True, index=True)
    knowledge_point_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_points.id"), nullable=True)
    knowledge_path: Mapped[str] = mapped_column(String(255), default="")
    error_type: Mapped[str] = mapped_column(String(50), default="概念不清", index=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    ai_summary: Mapped[str] = mapped_column(Text, default="")
    variant_question: Mapped[str] = mapped_column(Text, default="")
    review_suggestion: Mapped[str] = mapped_column(Text, default="")
    user_revision_note: Mapped[str] = mapped_column(Text, default="")
    ai_model_used: Mapped[str] = mapped_column(String(120), default="")
    ai_model_mode: Mapped[str] = mapped_column(String(30), default="auto")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    mistake: Mapped[Mistake] = relationship(back_populates="analysis")
    knowledge_point: Mapped[KnowledgePoint | None] = relationship(back_populates="analyses")


class ReviewTask(Base):
    __tablename__ = "review_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mistake_id: Mapped[int] = mapped_column(ForeignKey("mistakes.id"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id"), index=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    task_type: Mapped[str] = mapped_column(String(40), default="复盘")
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    mistake: Mapped[Mistake] = relationship(back_populates="review_tasks")
