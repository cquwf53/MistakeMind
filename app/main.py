from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR, UPLOAD_DIR, get_settings
from app.constants import ERROR_TYPES, MASTERY_STATUSES
from app.database import Base, SessionLocal, engine, ensure_compatible_schema, get_db
from app.extractors import extract_text_units, save_upload
from app.models import Mistake, ReviewTask
from app.reports import markdown_to_pdf_bytes
from app.scheduler import start_scheduler, stop_scheduler
from app.seed import ensure_seed_data
from app.services import (
    build_analysis_draft,
    complete_review_task,
    confirm_mistake,
    create_mistake,
    dashboard_stats,
    export_mistakes_csv,
    get_subject,
    knowledge_profile,
    list_knowledge_points,
    list_subjects,
    mistake_export_rows,
    review_sections,
    source_file_summaries,
    tomorrow_review_markdown,
    upsert_analysis,
    weekly_report_markdown,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema()
    with SessionLocal() as db:
        ensure_seed_data(db)
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def split_configured_models(raw: str) -> list[str]:
    if raw.strip().lower() == "auto":
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def model_options(*, vision: bool = False) -> list[str]:
    settings = get_settings()
    return split_configured_models(settings.ai_vision_model if vision else settings.ai_model)


def clean_model_override(model: str | None, *, vision: bool = False) -> str | None:
    selected = (model or "").strip()
    if not selected:
        return None
    return selected if selected in model_options(vision=vision) else None


def render(request: Request, template: str, context: dict, db: Session, subject_code: str | None = None):
    subject = get_subject(db, subject_code)
    base_context = {
        "request": request,
        "subjects": list_subjects(db),
        "subject": subject,
        "error_types": ERROR_TYPES,
        "mastery_statuses": MASTERY_STATUSES,
        "ai_enabled": get_settings().ai_enabled,
        "text_model_options": model_options(),
        "vision_model_options": model_options(vision=True),
    }
    base_context.update(context)
    return templates.TemplateResponse(request, template, base_context)


@app.get("/")
def dashboard(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return render(
        request,
        "dashboard.html",
        {"stats": dashboard_stats(db, active_subject)},
        db,
        active_subject.code,
    )


@app.get("/upload")
def upload_page(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    return render(request, "upload.html", {}, db, subject)


@app.post("/upload/manual")
def upload_manual(
    subject_code: str = Form(...),
    question_text: str = Form(""),
    answer_text: str = Form(""),
    user_error: str = Form(""),
    analysis_model: str = Form(""),
    db: Session = Depends(get_db),
):
    subject = get_subject(db, subject_code)
    mistake = create_mistake(
        db,
        subject=subject,
        question_text=question_text,
        answer_text=answer_text,
        user_error=user_error,
        source_type="manual",
        source_name="手动录入",
        analysis_model=clean_model_override(analysis_model),
    )
    return RedirectResponse(f"/mistakes/{mistake.id}/confirm?subject={subject.code}", status_code=303)


@app.post("/upload/file")
async def upload_file(
    subject_code: str = Form(...),
    analysis_model: str = Form(""),
    ocr_model: str = Form(""),
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    subject = get_subject(db, subject_code)
    stored_path = await save_upload(upload)
    cleaned_analysis_model = clean_model_override(analysis_model)
    text_units = extract_text_units(
        stored_path,
        upload.filename or stored_path.name,
        vision_model=clean_model_override(ocr_model, vision=True),
        text_model=cleaned_analysis_model,
    )
    created: list[Mistake] = []
    for index, text in enumerate(text_units[:12], start=1):
        source_name = upload.filename or stored_path.name
        if len(text_units) > 1:
            source_name = f"{source_name} - 第 {index} 段"
        created.append(
            create_mistake(
                db,
                subject=subject,
                question_text=text,
                source_type=Path(upload.filename or stored_path.name).suffix.lower().lstrip(".") or "file",
                source_name=source_name,
                source_path=stored_path.name,
                analysis_model=cleaned_analysis_model,
            )
        )
    if not created:
        raise HTTPException(status_code=400, detail="未能从文件中提取错题内容")
    return RedirectResponse(f"/mistakes/{created[0].id}/confirm?subject={subject.code}", status_code=303)


@app.get("/mistakes")
def mistake_library(
    request: Request,
    subject: str | None = None,
    status: str = "all",
    error_type: str = "all",
    knowledge_path: str = "all",
    mastery_status: str = "all",
    keyword: str = "",
    db: Session = Depends(get_db),
):
    active_subject = get_subject(db, subject)
    knowledge_points = list_knowledge_points(db, active_subject)
    mistakes = list(
        db.scalars(
            select(Mistake)
            .where(Mistake.subject_id == active_subject.id, Mistake.status != "archived")
            .order_by(Mistake.created_at.desc())
        )
    )
    if status != "all":
        mistakes = [mistake for mistake in mistakes if mistake.status == status]
    if error_type != "all":
        mistakes = [mistake for mistake in mistakes if mistake.analysis and mistake.analysis.error_type == error_type]
    if knowledge_path != "all":
        mistakes = [mistake for mistake in mistakes if mistake.analysis and mistake.analysis.knowledge_path == knowledge_path]
    if mastery_status != "all":
        mistakes = [mistake for mistake in mistakes if mistake.mastery_status == mastery_status]
    if keyword.strip():
        needle = keyword.strip().lower()
        mistakes = [
            mistake
            for mistake in mistakes
            if needle in mistake.question_text.lower()
            or (mistake.analysis and needle in mistake.analysis.knowledge_path.lower())
        ]
    return render(
        request,
        "mistakes.html",
        {
            "mistakes": mistakes,
            "knowledge_points": knowledge_points,
            "filters": {
                "status": status,
                "error_type": error_type,
                "knowledge_path": knowledge_path,
                "mastery_status": mastery_status,
                "keyword": keyword,
            },
        },
        db,
        active_subject.code,
    )


@app.get("/mistakes/export.csv")
def export_mistakes_csv_route(subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    content = export_mistakes_csv(db, active_subject)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{active_subject.code}-mistakes.csv"'},
    )


@app.get("/mistakes/export.json")
def export_mistakes_json_route(subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    content = json.dumps(mistake_export_rows(db, active_subject), ensure_ascii=False, indent=2)
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{active_subject.code}-mistakes.json"'},
    )


@app.get("/mistakes/{mistake_id}/confirm")
def confirm_page(request: Request, mistake_id: int, subject: str | None = None, db: Session = Depends(get_db)):
    mistake = db.get(Mistake, mistake_id)
    if mistake is None or mistake.status == "archived":
        raise HTTPException(status_code=404, detail="Mistake not found")
    return render(
        request,
        "confirm.html",
        {
            "mistake": mistake,
            "knowledge_points": list_knowledge_points(db, mistake.subject),
        },
        db,
        subject or mistake.subject.code,
    )


@app.post("/mistakes/{mistake_id}/confirm")
def confirm_submit(
    mistake_id: int,
    subject_code: str = Form(...),
    question_text: str = Form(...),
    answer_text: str = Form(""),
    user_error: str = Form(""),
    knowledge_path: str = Form(""),
    error_type: str = Form("概念不清"),
    difficulty: int = Form(3),
    confidence: float = Form(0.5),
    ai_summary: str = Form(""),
    variant_question: str = Form(""),
    review_suggestion: str = Form(""),
    user_revision_note: str = Form(""),
    db: Session = Depends(get_db),
):
    mistake = db.get(Mistake, mistake_id)
    if mistake is None:
        raise HTTPException(status_code=404, detail="Mistake not found")
    confirm_mistake(
        db,
        mistake=mistake,
        question_text=question_text,
        answer_text=answer_text,
        user_error=user_error,
        knowledge_path=knowledge_path,
        error_type=error_type,
        difficulty=difficulty,
        confidence=confidence,
        ai_summary=ai_summary,
        variant_question=variant_question,
        review_suggestion=review_suggestion,
        user_revision_note=user_revision_note,
    )
    return RedirectResponse(f"/mistakes?subject={subject_code}", status_code=303)


@app.post("/mistakes/{mistake_id}/reanalyze")
def reanalyze_mistake(
    mistake_id: int,
    subject_code: str = Form(...),
    analysis_model: str = Form(""),
    db: Session = Depends(get_db),
):
    mistake = db.get(Mistake, mistake_id)
    if mistake is None:
        raise HTTPException(status_code=404, detail="Mistake not found")
    draft = build_analysis_draft(db, mistake.subject, mistake, analysis_model=clean_model_override(analysis_model))
    upsert_analysis(db, mistake, draft)
    db.commit()
    return RedirectResponse(f"/mistakes/{mistake.id}/confirm?subject={subject_code}", status_code=303)


@app.post("/mistakes/{mistake_id}/archive")
def archive_mistake(mistake_id: int, subject_code: str = Form(...), db: Session = Depends(get_db)):
    mistake = db.get(Mistake, mistake_id)
    if mistake is None:
        raise HTTPException(status_code=404, detail="Mistake not found")
    mistake.status = "archived"
    db.commit()
    return RedirectResponse(f"/mistakes?subject={subject_code}", status_code=303)


@app.get("/reviews")
def reviews(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return render(
        request,
        "reviews.html",
        {"sections": review_sections(db, active_subject)},
        db,
        active_subject.code,
    )


@app.get("/reviews/tomorrow.md", response_class=PlainTextResponse)
def tomorrow_reviews_export(subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return tomorrow_review_markdown(db, active_subject)


@app.post("/reviews/{task_id}/complete")
def complete_task(task_id: int, subject_code: str = Form(...), db: Session = Depends(get_db)):
    task = db.get(ReviewTask, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Review task not found")
    complete_review_task(db, task)
    return RedirectResponse(f"/reviews?subject={subject_code}", status_code=303)


@app.get("/knowledge")
def knowledge_page(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return render(
        request,
        "knowledge.html",
        {"profile": knowledge_profile(db, active_subject)},
        db,
        active_subject.code,
    )


@app.get("/sources")
def source_files(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return render(
        request,
        "sources.html",
        {"source_files": source_file_summaries(db, active_subject)},
        db,
        active_subject.code,
    )


@app.get("/sources/{filename}")
def download_source_file(filename: str):
    upload_root = UPLOAD_DIR.resolve()
    target = (UPLOAD_DIR / filename).resolve()
    if target.parent != upload_root or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")
    return FileResponse(target, filename=filename)


@app.get("/weekly-report")
def weekly_report(request: Request, subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    markdown = weekly_report_markdown(db, active_subject)
    return render(
        request,
        "weekly_report.html",
        {"markdown": markdown},
        db,
        active_subject.code,
    )


@app.get("/weekly-report.md", response_class=PlainTextResponse)
def weekly_report_markdown_export(subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    return weekly_report_markdown(db, active_subject)


@app.get("/weekly-report.pdf")
def weekly_report_pdf_export(subject: str | None = None, db: Session = Depends(get_db)):
    active_subject = get_subject(db, subject)
    markdown = weekly_report_markdown(db, active_subject)
    pdf = markdown_to_pdf_bytes(markdown)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{active_subject.code}-weekly-report.pdf"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
