from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import AIClient
from app.constants import ERROR_TYPES, REVIEW_INTERVALS
from app.models import KnowledgePoint, Mistake, MistakeAnalysis, ReviewTask, Subject, utc_now

MISTAKE_EXPORT_FIELDS = [
    "id",
    "subject",
    "status",
    "mastery_status",
    "source_type",
    "source_name",
    "knowledge_path",
    "error_type",
    "difficulty",
    "confidence",
    "question_text",
    "answer_text",
    "user_error",
    "ai_summary",
    "ai_model_used",
    "ai_model_mode",
    "variant_question",
    "review_suggestion",
    "user_revision_note",
    "created_at",
    "updated_at",
]


@dataclass
class AnalysisDraft:
    knowledge_point: KnowledgePoint | None
    knowledge_path: str
    error_type: str
    difficulty: int
    confidence: float
    ai_summary: str
    variant_question: str
    review_suggestion: str
    ai_model_used: str = ""
    ai_model_mode: str = "auto"


def get_subject(db: Session, code: str | None) -> Subject:
    safe_code = code or "408"
    subject = db.scalar(select(Subject).where(Subject.code == safe_code))
    if subject is None:
        subject = db.scalar(select(Subject).where(Subject.code == "408"))
    if subject is None:
        raise RuntimeError("Seed data is missing. Run ensure_seed_data first.")
    return subject


def list_subjects(db: Session) -> list[Subject]:
    return list(db.scalars(select(Subject).order_by(Subject.id)))


def list_knowledge_points(db: Session, subject: Subject) -> list[KnowledgePoint]:
    return list(
        db.scalars(
            select(KnowledgePoint)
            .where(KnowledgePoint.subject_id == subject.id)
            .order_by(KnowledgePoint.level1, KnowledgePoint.level2, KnowledgePoint.level3)
        )
    )


def create_mistake(
    db: Session,
    *,
    subject: Subject,
    question_text: str,
    answer_text: str = "",
    user_error: str = "",
    source_type: str = "manual",
    source_name: str = "",
    source_path: str = "",
    analysis_model: str | None = None,
) -> Mistake:
    mistake = Mistake(
        subject_id=subject.id,
        question_text=question_text.strip() or "请在确认页补充题干。",
        answer_text=answer_text.strip(),
        user_error=user_error.strip(),
        source_type=source_type,
        source_name=source_name,
        source_path=source_path,
        status="pending",
        is_confirmed=False,
    )
    db.add(mistake)
    db.flush()
    draft = build_analysis_draft(db, subject, mistake, analysis_model=analysis_model)
    upsert_analysis(db, mistake, draft)
    db.commit()
    db.refresh(mistake)
    return mistake


def build_analysis_draft(
    db: Session,
    subject: Subject,
    mistake: Mistake,
    analysis_model: str | None = None,
) -> AnalysisDraft:
    knowledge_points = list_knowledge_points(db, subject)
    knowledge_paths = [point.path for point in knowledge_points]
    client = AIClient()
    ai_payload = client.analyze_mistake(
        subject_name=subject.name,
        question_text=mistake.question_text,
        answer_text=mistake.answer_text,
        user_error=mistake.user_error,
        knowledge_paths=knowledge_paths,
        error_types=ERROR_TYPES,
        model_override=analysis_model,
    )
    if ai_payload:
        return normalize_ai_payload(
            db,
            subject,
            ai_payload,
            ai_model_used=client.last_model or analysis_model or "",
            ai_model_mode="manual" if analysis_model else "auto",
        )
    if client.enabled:
        model_note = f"指定模型 {analysis_model} 调用失败" if analysis_model else "自动模型调用失败"
        return fallback_analysis(
            db,
            subject,
            mistake,
            ai_summary=f"{model_note}或返回格式异常，系统已使用关键词与规则完成初步归因，请人工重点确认。",
            confidence=0.45,
            ai_model_used=analysis_model or client.last_model or "自动 fallback",
            ai_model_mode="manual-fallback" if analysis_model else "auto-fallback",
        )
    return fallback_analysis(db, subject, mistake)


def normalize_ai_payload(
    db: Session,
    subject: Subject,
    payload: dict[str, Any],
    *,
    ai_model_used: str = "",
    ai_model_mode: str = "auto",
) -> AnalysisDraft:
    raw_path = str(payload.get("knowledge_path", "")).strip()
    knowledge_point = find_knowledge_point(db, subject, raw_path)
    if knowledge_point is None:
        knowledge_point = best_knowledge_match(db, subject, raw_path)
    knowledge_path = knowledge_point.path if knowledge_point else raw_path or "待确认知识点"

    error_type = str(payload.get("error_type", "概念不清")).strip()
    if error_type not in ERROR_TYPES:
        error_type = "其他"

    try:
        difficulty = int(payload.get("difficulty", 3))
    except (TypeError, ValueError):
        difficulty = 3
    difficulty = min(max(difficulty, 1), 5)

    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = min(max(confidence, 0.0), 1.0)

    return AnalysisDraft(
        knowledge_point=knowledge_point,
        knowledge_path=knowledge_path,
        error_type=error_type,
        difficulty=difficulty,
        confidence=confidence,
        ai_summary=str(payload.get("ai_summary", "")).strip() or "AI 已生成初步归因，请确认。",
        variant_question=str(payload.get("variant_question", "")).strip(),
        review_suggestion=str(payload.get("review_suggestion", "")).strip() or "先复盘原题，再做同知识点变式题。",
        ai_model_used=ai_model_used,
        ai_model_mode=ai_model_mode,
    )


def fallback_analysis(
    db: Session,
    subject: Subject,
    mistake: Mistake,
    ai_summary: str = "当前未启用 AI API，系统使用关键词与规则完成初步归因。",
    confidence: float = 0.58,
    ai_model_used: str = "规则兜底",
    ai_model_mode: str = "rules",
) -> AnalysisDraft:
    text = " ".join([mistake.question_text, mistake.answer_text, mistake.user_error])
    knowledge_point = best_knowledge_match(db, subject, text)
    knowledge_path = knowledge_point.path if knowledge_point else "待确认知识点"
    error_type = infer_error_type(text)
    difficulty = infer_difficulty(text)
    variant_question = build_variant_question(subject, knowledge_path)
    review_suggestion = (
        f"复盘 {knowledge_path} 的定义、典型题型和易错条件；"
        "再用 5 分钟写出本题的正确解题步骤。"
    )
    return AnalysisDraft(
        knowledge_point=knowledge_point,
        knowledge_path=knowledge_path,
        error_type=error_type,
        difficulty=difficulty,
        confidence=confidence,
        ai_summary=ai_summary,
        variant_question=variant_question,
        review_suggestion=review_suggestion,
        ai_model_used=ai_model_used,
        ai_model_mode=ai_model_mode,
    )


def upsert_analysis(db: Session, mistake: Mistake, draft: AnalysisDraft) -> MistakeAnalysis:
    analysis = mistake.analysis
    if analysis is None:
        analysis = MistakeAnalysis(mistake_id=mistake.id)
        db.add(analysis)
    analysis.knowledge_point_id = draft.knowledge_point.id if draft.knowledge_point else None
    analysis.knowledge_path = draft.knowledge_path
    analysis.error_type = draft.error_type
    analysis.difficulty = draft.difficulty
    analysis.confidence = draft.confidence
    analysis.ai_summary = draft.ai_summary
    analysis.variant_question = draft.variant_question
    analysis.review_suggestion = draft.review_suggestion
    analysis.ai_model_used = draft.ai_model_used
    analysis.ai_model_mode = draft.ai_model_mode
    return analysis


def confirm_mistake(
    db: Session,
    *,
    mistake: Mistake,
    question_text: str,
    answer_text: str,
    user_error: str,
    knowledge_path: str,
    error_type: str,
    difficulty: int,
    confidence: float,
    ai_summary: str,
    variant_question: str,
    review_suggestion: str,
    user_revision_note: str,
) -> None:
    mistake.question_text = question_text.strip()
    mistake.answer_text = answer_text.strip()
    mistake.user_error = user_error.strip()
    mistake.status = "confirmed"
    mistake.is_confirmed = True

    subject = mistake.subject
    knowledge_point = find_knowledge_point(db, subject, knowledge_path)
    analysis = mistake.analysis or MistakeAnalysis(mistake_id=mistake.id)
    db.add(analysis)
    analysis.knowledge_point_id = knowledge_point.id if knowledge_point else None
    analysis.knowledge_path = knowledge_point.path if knowledge_point else knowledge_path.strip() or "待确认知识点"
    analysis.error_type = error_type if error_type in ERROR_TYPES else "其他"
    analysis.difficulty = min(max(int(difficulty), 1), 5)
    analysis.confidence = min(max(float(confidence), 0.0), 1.0)
    analysis.ai_summary = ai_summary.strip()
    analysis.variant_question = variant_question.strip()
    analysis.review_suggestion = review_suggestion.strip()
    analysis.user_revision_note = user_revision_note.strip()

    create_review_tasks(db, mistake)
    db.commit()


def create_review_tasks(db: Session, mistake: Mistake) -> None:
    existing_count = db.scalar(select(func.count()).select_from(ReviewTask).where(ReviewTask.mistake_id == mistake.id))
    if existing_count:
        return
    today = date.today()
    for interval in REVIEW_INTERVALS:
        db.add(
            ReviewTask(
                mistake_id=mistake.id,
                subject_id=mistake.subject_id,
                due_date=today + timedelta(days=interval),
                interval_days=interval,
                task_type="复盘" if interval == 0 else "间隔复习",
                status="pending",
            )
        )


def complete_review_task(db: Session, task: ReviewTask) -> None:
    task.status = "completed"
    task.completed_at = utc_now()
    db.commit()


def find_knowledge_point(db: Session, subject: Subject, path: str) -> KnowledgePoint | None:
    normalized = path.strip()
    for point in list_knowledge_points(db, subject):
        if normalized == point.path or normalized == point.level3:
            return point
    return None


def best_knowledge_match(db: Session, subject: Subject, text: str) -> KnowledgePoint | None:
    lowered = text.lower()
    best_point: KnowledgePoint | None = None
    best_score = 0
    for point in list_knowledge_points(db, subject):
        tokens = [point.level1, point.level2, point.level3, *point.keywords.split(",")]
        score = sum(1 for token in tokens if token and token.lower() in lowered)
        if score > best_score:
            best_point = point
            best_score = score
    return best_point


def infer_error_type(text: str) -> str:
    lowered = text.lower()
    rules = [
        ("公式/定理遗忘", ["公式", "定理", "性质", "忘", "记错"]),
        ("审题错误", ["审题", "看错", "题意", "条件漏看"]),
        ("计算错误", ["计算", "算错", "符号", "化简", " arithmetic"]),
        ("边界条件遗漏", ["边界", "特殊", "极端", "空", "0", "初值"]),
        ("方法选择错误", ["方法", "思路", "不会", "选错", "模型"]),
        ("步骤不完整", ["步骤", "漏写", "过程", "证明不完整"]),
        ("时间压力失误", ["时间", "来不及", "紧张", "粗心"]),
    ]
    for error_type, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return error_type
    return "概念不清"


def infer_difficulty(text: str) -> int:
    length = len(text)
    if length > 600:
        return 5
    if length > 350:
        return 4
    if any(token in text for token in ["证明", "综合", "压轴", "复杂"]):
        return 4
    return 3


def build_variant_question(subject: Subject, knowledge_path: str) -> str:
    if subject.code == "408":
        return f"围绕「{knowledge_path}」重新设计一个小规模样例，手算关键步骤并解释每一步依据。"
    return f"围绕「{knowledge_path}」更换参数或条件，重新完成一遍推导并标出容易漏掉的限制。"


def dashboard_stats(db: Session, subject: Subject) -> dict[str, Any]:
    today = date.today()
    mistake_filter = (Mistake.subject_id == subject.id, Mistake.status != "archived")
    total = db.scalar(select(func.count()).select_from(Mistake).where(*mistake_filter)) or 0
    confirmed = db.scalar(select(func.count()).select_from(Mistake).where(*mistake_filter, Mistake.is_confirmed.is_(True))) or 0
    pending_confirm = total - confirmed
    today_tasks = db.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.subject_id == subject.id,
            ReviewTask.status == "pending",
            ReviewTask.due_date == today,
        )
    ) or 0
    overdue_tasks = db.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.subject_id == subject.id,
            ReviewTask.status == "pending",
            ReviewTask.due_date < today,
        )
    ) or 0
    completed_tasks = db.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.subject_id == subject.id,
            ReviewTask.status == "completed",
        )
    ) or 0
    total_review_tasks = db.scalar(
        select(func.count()).select_from(ReviewTask).where(ReviewTask.subject_id == subject.id)
    ) or 0
    review_completion_rate = round(completed_tasks / total_review_tasks * 100) if total_review_tasks else 0

    analyses = list(
        db.scalars(
            select(MistakeAnalysis)
            .join(Mistake)
            .where(Mistake.subject_id == subject.id, Mistake.status != "archived")
        )
    )
    weak_points = Counter(a.knowledge_path for a in analyses if a.knowledge_path).most_common(6)
    error_counts = Counter(a.error_type for a in analyses if a.error_type).most_common()
    error_trend = recent_error_trend(analyses)
    recent = list(
        db.scalars(
            select(Mistake)
            .where(Mistake.subject_id == subject.id, Mistake.status != "archived")
            .order_by(Mistake.created_at.desc())
            .limit(6)
        )
    )
    return {
        "total": total,
        "confirmed": confirmed,
        "pending_confirm": pending_confirm,
        "today_tasks": today_tasks,
        "overdue_tasks": overdue_tasks,
        "completed_tasks": completed_tasks,
        "total_review_tasks": total_review_tasks,
        "review_completion_rate": review_completion_rate,
        "weak_points": weak_points,
        "error_counts": error_counts,
        "error_trend": error_trend,
        "recent": recent,
    }


def knowledge_profile(db: Session, subject: Subject) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for point in list_knowledge_points(db, subject):
        rows[point.path] = {
            "path": point.path,
            "level1": point.level1,
            "level2": point.level2,
            "level3": point.level3,
            "total": 0,
            "confirmed": 0,
            "pending": 0,
            "unmastered": 0,
            "reviewing": 0,
            "mastered": 0,
            "completed_reviews": 0,
            "total_reviews": 0,
            "completion_rate": 0,
            "avg_confidence": 0,
            "last_seen": None,
            "_confidence_sum": 0.0,
            "_confidence_count": 0,
        }

    analyses = list(
        db.scalars(
            select(MistakeAnalysis)
            .join(Mistake)
            .where(Mistake.subject_id == subject.id, Mistake.status != "archived")
        )
    )
    for analysis in analyses:
        mistake = analysis.mistake
        path = analysis.knowledge_path or "待确认知识点"
        row = rows.setdefault(
            path,
            {
                "path": path,
                "level1": path.split(" / ")[0] if " / " in path else "待确认",
                "level2": "",
                "level3": path,
                "total": 0,
                "confirmed": 0,
                "pending": 0,
                "unmastered": 0,
                "reviewing": 0,
                "mastered": 0,
                "completed_reviews": 0,
                "total_reviews": 0,
                "completion_rate": 0,
                "avg_confidence": 0,
                "last_seen": None,
                "_confidence_sum": 0.0,
                "_confidence_count": 0,
            },
        )
        row["total"] += 1
        if mistake.is_confirmed:
            row["confirmed"] += 1
        else:
            row["pending"] += 1
        if mistake.mastery_status == "已掌握":
            row["mastered"] += 1
        elif mistake.mastery_status == "复习中":
            row["reviewing"] += 1
        else:
            row["unmastered"] += 1
        row["completed_reviews"] += sum(1 for task in mistake.review_tasks if task.status == "completed")
        row["total_reviews"] += len(mistake.review_tasks)
        row["_confidence_sum"] += analysis.confidence or 0
        row["_confidence_count"] += 1
        if row["last_seen"] is None or mistake.created_at > row["last_seen"]:
            row["last_seen"] = mistake.created_at

    result = []
    for row in rows.values():
        row["completion_rate"] = round(row["completed_reviews"] / row["total_reviews"] * 100) if row["total_reviews"] else 0
        row["avg_confidence"] = round(row["_confidence_sum"] / row["_confidence_count"], 2) if row["_confidence_count"] else 0
        row["risk_score"] = row["total"] * 3 + row["pending"] * 2 + row["reviewing"] - row["mastered"]
        row.pop("_confidence_sum", None)
        row.pop("_confidence_count", None)
        result.append(row)
    return sorted(result, key=lambda item: (item["total"] == 0, -item["risk_score"], item["path"]))


def recent_error_trend(analyses: list[MistakeAnalysis], days: int = 7) -> list[dict[str, Any]]:
    today = date.today()
    rows: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        counts = Counter(
            analysis.error_type
            for analysis in analyses
            if analysis.error_type and analysis.mistake.created_at.date() == day
        )
        rows.append(
            {
                "date": day,
                "label": day.strftime("%m-%d"),
                "total": sum(counts.values()),
                "top_items": counts.most_common(3),
            }
        )
    return rows


def source_file_summaries(db: Session, subject: Subject) -> list[dict[str, Any]]:
    mistakes = list(
        db.scalars(
            select(Mistake)
            .where(
                Mistake.subject_id == subject.id,
                Mistake.status != "archived",
                Mistake.source_path != "",
            )
            .order_by(Mistake.created_at.desc())
        )
    )
    summaries: dict[str, dict[str, Any]] = {}
    for mistake in mistakes:
        summary = summaries.setdefault(
            mistake.source_path,
            {
                "source_path": mistake.source_path,
                "source_name": mistake.source_name,
                "source_type": mistake.source_type,
                "created_at": mistake.created_at,
                "mistake_count": 0,
            },
        )
        summary["mistake_count"] += 1
        if mistake.created_at > summary["created_at"]:
            summary["created_at"] = mistake.created_at
            summary["source_name"] = mistake.source_name
            summary["source_type"] = mistake.source_type
    return sorted(summaries.values(), key=lambda item: item["created_at"], reverse=True)


def review_sections(db: Session, subject: Subject) -> dict[str, list[ReviewTask]]:
    today = date.today()
    tasks = list(
        db.scalars(
            select(ReviewTask)
            .where(ReviewTask.subject_id == subject.id)
            .order_by(ReviewTask.due_date.asc(), ReviewTask.id.asc())
        )
    )
    return {
        "overdue": [task for task in tasks if task.status == "pending" and task.due_date < today],
        "today": [task for task in tasks if task.status == "pending" and task.due_date == today],
        "upcoming": [task for task in tasks if task.status == "pending" and task.due_date > today],
        "completed": [task for task in tasks if task.status == "completed"],
    }


def tomorrow_review_markdown(db: Session, subject: Subject) -> str:
    tomorrow = date.today() + timedelta(days=1)
    tasks = list(
        db.scalars(
            select(ReviewTask)
            .where(
                ReviewTask.subject_id == subject.id,
                ReviewTask.status == "pending",
                ReviewTask.due_date == tomorrow,
            )
            .order_by(ReviewTask.id.asc())
        )
    )
    lines = [f"# {tomorrow.isoformat()} {subject.name} 复习清单", ""]
    if not tasks:
        lines.append("- 暂无待复习任务")
        return "\n".join(lines) + "\n"
    for task in tasks:
        mistake = task.mistake
        knowledge_path = mistake.analysis.knowledge_path if mistake.analysis else "待分析知识点"
        question = mistake.question_text.replace("\n", " ")[:100]
        lines.append(f"- [{task.task_type}] {knowledge_path}：{question}")
    return "\n".join(lines) + "\n"


def mistake_export_rows(db: Session, subject: Subject) -> list[dict[str, Any]]:
    mistakes = list(
        db.scalars(
            select(Mistake)
            .where(Mistake.subject_id == subject.id, Mistake.status != "archived")
            .order_by(Mistake.created_at.desc(), Mistake.id.desc())
        )
    )
    rows = []
    for mistake in mistakes:
        analysis = mistake.analysis
        rows.append(
            {
                "id": mistake.id,
                "subject": subject.code,
                "status": mistake.status,
                "mastery_status": mistake.mastery_status,
                "source_type": mistake.source_type,
                "source_name": mistake.source_name,
                "knowledge_path": analysis.knowledge_path if analysis else "",
                "error_type": analysis.error_type if analysis else "",
                "difficulty": analysis.difficulty if analysis else "",
                "confidence": analysis.confidence if analysis else "",
                "question_text": mistake.question_text,
                "answer_text": mistake.answer_text,
                "user_error": mistake.user_error,
                "ai_summary": analysis.ai_summary if analysis else "",
                "ai_model_used": analysis.ai_model_used if analysis else "",
                "ai_model_mode": analysis.ai_model_mode if analysis else "",
                "variant_question": analysis.variant_question if analysis else "",
                "review_suggestion": analysis.review_suggestion if analysis else "",
                "user_revision_note": analysis.user_revision_note if analysis else "",
                "created_at": mistake.created_at.isoformat(sep=" ", timespec="seconds"),
                "updated_at": mistake.updated_at.isoformat(sep=" ", timespec="seconds"),
            }
        )
    return rows


def export_mistakes_csv(db: Session, subject: Subject) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=MISTAKE_EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(mistake_export_rows(db, subject))
    return "\ufeff" + output.getvalue()


def weekly_report_markdown(db: Session, subject: Subject) -> str:
    stats = dashboard_stats(db, subject)
    sections = review_sections(db, subject)
    lines = [
        f"# {subject.name} 学习周报",
        "",
        f"- 错题总数：{stats['total']}",
        f"- 已确认错题：{stats['confirmed']}",
        f"- 待确认错题：{stats['pending_confirm']}",
        f"- 今日待复习：{stats['today_tasks']}",
        f"- 逾期复习：{stats['overdue_tasks']}",
        f"- 已完成复习任务：{stats['completed_tasks']}",
        f"- 复习完成率：{stats['review_completion_rate']}%",
        "",
        "## 高频知识点",
    ]
    if stats["weak_points"]:
        lines.extend([f"- {name}：{count} 次" for name, count in stats["weak_points"]])
    else:
        lines.append("- 暂无数据")

    lines.extend(["", "## 重复错因"])
    if stats["error_counts"]:
        lines.extend([f"- {name}：{count} 次" for name, count in stats["error_counts"]])
    else:
        lines.append("- 暂无数据")

    lines.extend(["", "## 下周建议"])
    if stats["weak_points"]:
        lines.append(f"- 优先复盘「{stats['weak_points'][0][0]}」，补齐概念和典型题。")
    if stats["error_counts"]:
        lines.append(f"- 针对「{stats['error_counts'][0][0]}」建立检查清单，做题后自查。")
    if sections["overdue"]:
        lines.append("- 先清理逾期复习任务，再新增错题。")
    if len(lines) and lines[-1] == "## 下周建议":
        lines.append("- 继续录入错题，形成可分析的样本量。")
    return "\n".join(lines) + "\n"
