"""APScheduler 后台任务：每晚生成明日复习清单、每周生成学习周报。"""
from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import STORAGE_DIR, get_settings
from app.database import SessionLocal
from app.services import get_subject, list_subjects, tomorrow_review_markdown, weekly_report_markdown

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _generate_tomorrow_reviews() -> None:
    """生成所有学科的明日复习清单并写入 storage/。"""
    try:
        with SessionLocal() as db:
            for subject in list_subjects(db):
                markdown = tomorrow_review_markdown(db, subject)
                output = STORAGE_DIR / f"tomorrow_reviews_{subject.code}.md"
                output.write_text(markdown, encoding="utf-8")
        logger.info("已自动生成明日复习清单")
    except Exception:
        logger.exception("自动生成明日复习清单失败")


def _generate_weekly_report() -> None:
    """生成所有学科的周报并写入 storage/。"""
    try:
        with SessionLocal() as db:
            for subject in list_subjects(db):
                markdown = weekly_report_markdown(db, subject)
                output = STORAGE_DIR / f"weekly_report_{subject.code}.md"
                output.write_text(markdown, encoding="utf-8")
        logger.info("已自动生成学习周报")
    except Exception:
        logger.exception("自动生成学习周报失败")


def start_scheduler() -> None:
    """根据配置启动后台调度器。"""
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        logger.info("调度器已禁用 (APP_SCHEDULER_ENABLED != 1)")
        return
    if _scheduler is not None:
        return

    _scheduler = BackgroundScheduler(daemon=True)

    # 每晚生成明日复习清单
    tomorrow_time = settings.scheduler_tomorrow_time
    hour, minute = 22, 0
    try:
        parts = tomorrow_time.split(":")
        hour, minute = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        pass
    _scheduler.add_job(
        _generate_tomorrow_reviews,
        "cron",
        hour=hour,
        minute=minute,
        id="tomorrow_reviews",
        replace_existing=True,
    )

    # 每周生成周报
    weekly_time = settings.scheduler_weekly_time
    day_of_week = "mon"
    w_hour, w_minute = 9, 0
    try:
        parts = weekly_time.split()
        if len(parts) == 2:
            day_of_week = parts[0]
            time_parts = parts[1].split(":")
            w_hour, w_minute = int(time_parts[0]), int(time_parts[1])
    except (ValueError, IndexError):
        pass
    _scheduler.add_job(
        _generate_weekly_report,
        "cron",
        day_of_week=day_of_week,
        hour=w_hour,
        minute=w_minute,
        id="weekly_report",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        "调度器已启动：明日清单 %02d:%02d / 周报 %s %02d:%02d",
        hour, minute, day_of_week, w_hour, w_minute,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
