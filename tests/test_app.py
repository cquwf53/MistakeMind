import os
import re
from datetime import date, timedelta
from pathlib import Path

os.environ["APP_DATABASE_URL"] = "sqlite:///./storage/test_mistakes.db"
os.environ["APP_SKIP_ENV_FILE"] = "1"
os.environ["AI_API_KEY"] = ""
Path("storage/test_mistakes.db").unlink(missing_ok=True)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Mistake, ReviewTask  # noqa: E402
from app.cli import generate_tomorrow_reviews, seed_demo_data  # noqa: E402
from app.services import best_knowledge_match, get_subject  # noqa: E402


def mistake_id_from_location(location: str) -> int:
    match = re.search(r"/mistakes/(\d+)/confirm", location)
    assert match, location
    return int(match.group(1))


def test_manual_flow_creates_analysis_and_review_tasks():
    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "408",
                "question_text": "TCP 三次握手中 SYN 和 ACK 的状态变化为何？",
                "answer_text": "服务端收到 SYN 后返回 SYN+ACK。",
                "user_error": "把四次挥手和三次握手的状态看错，属于审题错误。",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        mistake_id = mistake_id_from_location(response.headers["location"])

        confirm_page = client.get(response.headers["location"])
        assert "确认归因" in confirm_page.text
        assert "计算机网络" in confirm_page.text

        response = client.post(
            f"/mistakes/{mistake_id}/confirm",
            data={
                "subject_code": "408",
                "question_text": "TCP 三次握手中 SYN 和 ACK 的状态变化为何？",
                "answer_text": "服务端收到 SYN 后返回 SYN+ACK。",
                "user_error": "看错题意。",
                "knowledge_path": "计算机网络 / 传输层 / TCP与UDP",
                "error_type": "审题错误",
                "difficulty": 3,
                "confidence": 0.9,
                "ai_summary": "误把连接建立和连接释放过程混在一起。",
                "variant_question": "画出 TCP 三次握手状态转移。",
                "review_suggestion": "对比三次握手和四次挥手。",
                "user_revision_note": "人工确认。",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

        with SessionLocal() as db:
            mistake = db.get(Mistake, mistake_id)
            assert mistake is not None
            assert mistake.is_confirmed is True
            assert mistake.analysis.error_type == "审题错误"
            assert mistake.mastery_status == "复习中"
            tasks = list(db.scalars(select(ReviewTask).where(ReviewTask.mistake_id == mistake_id)))
            assert [task.interval_days for task in tasks] == [0, 1, 3, 7, 14]


def test_subject_data_stays_isolated_and_exports_report():
    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "math1",
                "question_text": "求极限 lim sin x / x。",
                "answer_text": "1",
                "user_error": "忘记等价无穷小。",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        mistake_id = mistake_id_from_location(response.headers["location"])
        client.post(
            f"/mistakes/{mistake_id}/confirm",
            data={
                "subject_code": "math1",
                "question_text": "求极限 lim sin x / x。",
                "answer_text": "1",
                "user_error": "忘记等价无穷小。",
                "knowledge_path": "高等数学 / 函数极限连续 / 极限与无穷小",
                "error_type": "公式/定理遗忘",
                "difficulty": 2,
                "confidence": 0.86,
                "ai_summary": "等价无穷小使用不熟。",
                "variant_question": "求 lim tan x / x。",
                "review_suggestion": "整理常用等价无穷小。",
                "user_revision_note": "",
            },
        )

        math_page = client.get("/mistakes?subject=math1")
        cs_page = client.get("/mistakes?subject=408")
        assert "极限与无穷小" in math_page.text
        assert "极限与无穷小" not in cs_page.text

        filtered_by_point = client.get(
            "/mistakes",
            params={
                "subject": "math1",
                "knowledge_path": "高等数学 / 函数极限连续 / 极限与无穷小",
            },
        )
        assert "求极限 lim sin x / x。" in filtered_by_point.text

        filtered_by_mastery = client.get("/mistakes", params={"subject": "math1", "mastery_status": "复习中"})
        assert "求极限 lim sin x / x。" in filtered_by_mastery.text

        markdown = client.get("/weekly-report.md?subject=math1")
        assert markdown.status_code == 200
        assert "数一 数学一 学习周报" in markdown.text
        assert "复习完成率" in markdown.text

        pdf = client.get("/weekly-report.pdf?subject=math1")
        assert pdf.status_code == 200
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF")


def test_text_file_upload_creates_confirmable_mistake():
    with TestClient(app) as client:
        response = client.post(
            "/upload/file",
            data={"subject_code": "408"},
            files={"upload": ("mistake.txt", "二叉树先序遍历和中序遍历还原二叉树。".encode("utf-8"), "text/plain")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "二叉树" in page.text
        assert "确认归因" in page.text

        sources = client.get("/sources?subject=408")
        assert sources.status_code == 200
        assert "mistake.txt" in sources.text
        assert "下载原文件" in sources.text


def test_review_completion_rate_and_mastery_status_update_after_tasks_completed():
    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "408",
                "question_text": "HTTP 状态码 304 和缓存机制。",
                "answer_text": "304 表示协商缓存命中。",
                "user_error": "概念不清。",
            },
            follow_redirects=False,
        )
        mistake_id = mistake_id_from_location(response.headers["location"])
        client.post(
            f"/mistakes/{mistake_id}/confirm",
            data={
                "subject_code": "408",
                "question_text": "HTTP 状态码 304 和缓存机制。",
                "answer_text": "304 表示协商缓存命中。",
                "user_error": "概念不清。",
                "knowledge_path": "计算机网络 / 应用层 / HTTP与DNS",
                "error_type": "概念不清",
                "difficulty": 3,
                "confidence": 0.8,
                "ai_summary": "缓存状态码概念混淆。",
                "variant_question": "解释 200、304 与缓存控制字段。",
                "review_suggestion": "整理 HTTP 缓存流程。",
                "user_revision_note": "",
            },
        )

        with SessionLocal() as db:
            tasks = list(db.scalars(select(ReviewTask).where(ReviewTask.mistake_id == mistake_id)))
            for task in tasks:
                task.status = "completed"
            db.commit()
            mistake = db.get(Mistake, mistake_id)
            assert mistake is not None
            assert mistake.mastery_status == "已掌握"

        dashboard = client.get("/?subject=408")
        assert "复习完成率" in dashboard.text
        assert "最近错因趋势" in dashboard.text
        mastered = client.get("/mistakes", params={"subject": "408", "mastery_status": "已掌握"})
        assert "HTTP 状态码 304 和缓存机制。" in mastered.text


def test_unknown_text_does_not_force_first_knowledge_point():
    with TestClient(app):
        with SessionLocal() as db:
            subject = get_subject(db, "408")
            assert best_knowledge_match(db, subject, "这是一段没有领域关键词的普通描述") is None


def test_cli_generates_demo_data_and_tomorrow_review_list():
    seed_demo_data()
    markdown = generate_tomorrow_reviews("408")
    assert "复习清单" in markdown
    assert "408 计算机学科专业基础" in markdown

    with SessionLocal() as db:
        subject = get_subject(db, "408")
        demo_mistake = db.scalar(
            select(Mistake).where(
                Mistake.subject_id == subject.id,
                Mistake.source_type == "demo",
            )
        )
        assert demo_mistake is not None


def test_knowledge_profile_and_data_exports_are_available():
    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "408",
                "question_text": "Cache 组相联映射下主存块号如何拆分？",
                "answer_text": "拆成 tag、组号和块内偏移。",
                "user_error": "把组号和偏移位数混淆。",
            },
            follow_redirects=False,
        )
        mistake_id = mistake_id_from_location(response.headers["location"])
        client.post(
            f"/mistakes/{mistake_id}/confirm",
            data={
                "subject_code": "408",
                "question_text": "Cache 组相联映射下主存块号如何拆分？",
                "answer_text": "拆成 tag、组号和块内偏移。",
                "user_error": "把组号和偏移位数混淆。",
                "knowledge_path": "计算机组成原理 / 存储系统 / Cache与主存",
                "error_type": "公式/定理遗忘",
                "difficulty": 3,
                "confidence": 0.81,
                "ai_summary": "地址字段拆分公式不熟。",
                "variant_question": "给定地址位数、块大小和组数，拆分地址字段。",
                "review_suggestion": "整理 Cache 地址拆分模板。",
                "user_revision_note": "",
            },
        )

        knowledge = client.get("/knowledge?subject=408")
        assert knowledge.status_code == 200
        assert "知识点掌握画像" in knowledge.text
        assert "Cache与主存" in knowledge.text
        assert "风险分" in knowledge.text

        csv_response = client.get("/mistakes/export.csv?subject=408")
        assert csv_response.status_code == 200
        assert "text/csv" in csv_response.headers["content-type"]
        assert "Cache 组相联映射" in csv_response.text
        assert "mastery_status" in csv_response.text

        json_response = client.get("/mistakes/export.json?subject=408")
        assert json_response.status_code == 200
        assert json_response.headers["content-type"].startswith("application/json")
        assert json_response.json()[0]["subject"] == "408"


def test_web_tomorrow_review_list_export():
    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "math1",
                "question_text": "二重积分换元到极坐标时为什么要乘 r？",
                "answer_text": "雅可比行列式为 r。",
                "user_error": "漏乘雅可比因子。",
            },
            follow_redirects=False,
        )
        mistake_id = mistake_id_from_location(response.headers["location"])
        client.post(
            f"/mistakes/{mistake_id}/confirm",
            data={
                "subject_code": "math1",
                "question_text": "二重积分换元到极坐标时为什么要乘 r？",
                "answer_text": "雅可比行列式为 r。",
                "user_error": "漏乘雅可比因子。",
                "knowledge_path": "高等数学 / 重积分 / 二重积分与三重积分",
                "error_type": "步骤不完整",
                "difficulty": 3,
                "confidence": 0.82,
                "ai_summary": "换元步骤遗漏面积元变化。",
                "variant_question": "用极坐标计算圆域上的二重积分。",
                "review_suggestion": "复盘常见坐标变换的雅可比。",
                "user_revision_note": "",
            },
        )
        with SessionLocal() as db:
            task = db.scalar(
                select(ReviewTask)
                .join(Mistake)
                .where(
                    Mistake.id == mistake_id,
                    ReviewTask.interval_days == 1,
                )
            )
            assert task is not None
            task.due_date = date.today() + timedelta(days=1)
            db.commit()

        review_page = client.get("/reviews?subject=math1")
        assert "导出明日清单" in review_page.text

        markdown = client.get("/reviews/tomorrow.md?subject=math1")
        assert markdown.status_code == 200
        assert "复习清单" in markdown.text
        assert "二重积分换元" in markdown.text


def test_manual_model_override_is_used_and_recorded(monkeypatch):
    import app.main as main_module
    import app.services as services_module

    captured = {}

    def fake_analyze(self, **kwargs):
        captured.update(kwargs)
        self.last_model = kwargs["model_override"]
        return {
            "knowledge_path": "计算机网络 / 传输层 / TCP与UDP",
            "error_type": "概念不清",
            "difficulty": 3,
            "confidence": 0.91,
            "ai_summary": "手动模型完成归因。",
            "variant_question": "解释 TCP 与 UDP 的差异。",
            "review_suggestion": "复盘传输层核心协议。",
        }

    monkeypatch.setattr(main_module, "model_options", lambda *, vision=False: ["manual-test-model"] if not vision else [])
    monkeypatch.setattr(services_module.AIClient, "analyze_mistake", fake_analyze)

    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "408",
                "question_text": "TCP 和 UDP 的区别是什么？",
                "answer_text": "TCP 面向连接，UDP 无连接。",
                "user_error": "概念混淆。",
                "analysis_model": "manual-test-model",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        mistake_id = mistake_id_from_location(response.headers["location"])

    assert captured["model_override"] == "manual-test-model"
    with SessionLocal() as db:
        mistake = db.get(Mistake, mistake_id)
        assert mistake is not None
        assert mistake.analysis.ai_model_used == "manual-test-model"
        assert mistake.analysis.ai_model_mode == "manual"


# ===== 新增测试：PDF / DOCX / 图片入口 + AI 低置信度兜底 + OCR 修正 =====


def test_pdf_upload_creates_confirmable_mistake():
    """PDF 文件上传能生成待确认错题。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    import io

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()
    from reportlab.lib.styles import ParagraphStyle
    cn = ParagraphStyle("cn", parent=styles["Normal"], fontName="STSong-Light", fontSize=12)
    doc.build([Paragraph("快速排序的平均时间复杂度是多少？", cn)])
    pdf_bytes = buf.getvalue()

    with TestClient(app) as client:
        response = client.post(
            "/upload/file",
            data={"subject_code": "408"},
            files={"upload": ("quiz.pdf", pdf_bytes, "application/pdf")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "确认归因" in page.text
        assert "快速排序" in page.text


def test_docx_upload_creates_confirmable_mistake():
    """Word 文档上传能生成待确认错题。"""
    from docx import Document as DocxDocument
    import io

    doc = DocxDocument()
    doc.add_paragraph("判断矩阵是否可对角化的充要条件是什么？")
    buf = io.BytesIO()
    doc.save(buf)
    docx_bytes = buf.getvalue()

    with TestClient(app) as client:
        response = client.post(
            "/upload/file",
            data={"subject_code": "math1"},
            files={"upload": ("hw.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "确认归因" in page.text
        assert "对角化" in page.text


def test_image_upload_without_ai_shows_manual_prompt():
    """图片上传在无 AI 时提示手动补充题干。"""
    # 生成一个最小 PNG (1x1 白色像素)
    import struct, zlib

    def minimal_png() -> bytes:
        raw = b"\x00\xff\xff\xff"
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    with TestClient(app) as client:
        response = client.post(
            "/upload/file",
            data={"subject_code": "408"},
            files={"upload": ("photo.png", minimal_png(), "image/png")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "确认归因" in page.text
        assert "手动补充题干" in page.text


def test_ai_low_confidence_shows_warning_on_confirm_page(monkeypatch):
    """AI 返回低置信度时确认页显示人工复核提示。"""
    import app.services as services_module

    def fake_analyze(self, **kwargs):
        self.last_model = "test-model"
        return {
            "knowledge_path": "数据结构 / 查找与排序 / 排序",
            "error_type": "概念不清",
            "difficulty": 2,
            "confidence": 0.35,
            "ai_summary": "AI 置信度较低，建议人工确认。",
            "variant_question": "",
            "review_suggestion": "",
        }

    monkeypatch.setattr(services_module.AIClient, "analyze_mistake", fake_analyze)

    with TestClient(app) as client:
        response = client.post(
            "/upload/manual",
            data={
                "subject_code": "408",
                "question_text": "堆排序的时间复杂度？",
                "answer_text": "O(n log n)",
                "user_error": "不确定。",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "需要人工复核" in page.text


def test_ocr_polish_is_applied_to_image(monkeypatch):
    """图片上传时 OCR 原始文本经过文本模型修正。"""
    import app.extractors as extractors_module
    from app.ai import AIClient

    call_log = []

    def fake_extract(self, path, model_override=None):
        call_log.append("ocr")
        return "OCR 原始：快速排 序的时间复杂度 是O(nlogn)吗？"

    def fake_polish(self, raw_text, model_override=None):
        call_log.append("polish")
        return "快速排序的时间复杂度是 O(n log n) 吗？"

    monkeypatch.setattr(AIClient, "extract_text_from_image", fake_extract)
    monkeypatch.setattr(AIClient, "polish_ocr_text", fake_polish)

    import struct, zlib
    def minimal_png() -> bytes:
        raw = b"\x00\xff\xff\xff"
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

    with TestClient(app) as client:
        response = client.post(
            "/upload/file",
            data={"subject_code": "408"},
            files={"upload": ("shot.png", minimal_png(), "image/png")},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get(response.headers["location"])
        assert "快速排序的时间复杂度是 O(n log n) 吗？" in page.text
        assert "ocr" in call_log
        assert "polish" in call_log
