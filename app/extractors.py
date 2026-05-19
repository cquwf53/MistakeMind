from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

from docx import Document
from fastapi import UploadFile
from pypdf import PdfReader

from app.ai import AIClient
from app.config import UPLOAD_DIR


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv"}
PDF_RENDER_MAX_PAGES = 6
PDF_RENDER_SCALE = 2.0
PDF_TEXT_MIN_CHARS = 20  # 单页文本字符少于该值视为扫描页


async def save_upload(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "upload").suffix.lower()
    safe_name = f"{uuid4().hex}{suffix}"
    target = UPLOAD_DIR / safe_name
    target.write_bytes(await upload.read())
    return target


def extract_text_units(
    path: Path,
    original_name: str,
    vision_model: str | None = None,
    text_model: str | None = None,
) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path, vision_model=vision_model, text_model=text_model)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix in TEXT_SUFFIXES:
        return [read_text(path)]
    if suffix in IMAGE_SUFFIXES:
        return [extract_image(path, original_name, vision_model=vision_model, text_model=text_model)]
    return [read_text(path)]


def extract_image(
    path: Path,
    original_name: str,
    vision_model: str | None = None,
    text_model: str | None = None,
) -> str:
    client = AIClient()
    raw = client.extract_text_from_image(path, model_override=vision_model)
    if not raw:
        return (
            f"图片 {original_name} 已保存，但当前未配置可用的视觉 AI API。"
            "请在确认页手动补充题干。"
        )
    polished = client.polish_ocr_text(raw, model_override=text_model)
    return polished or raw


def extract_pdf(
    path: Path,
    vision_model: str | None = None,
    text_model: str | None = None,
) -> list[str]:
    """优先用 pypdf 抽文本；扫描页（文本极少）退回到 pypdfium2 渲染 + 视觉 OCR。"""
    reader = PdfReader(str(path))
    pages: list[str] = []
    scanned_indices: list[int] = []
    raw_pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        raw_pages.append(text)
        if len(text) < PDF_TEXT_MIN_CHARS:
            scanned_indices.append(index)

    if scanned_indices and len(scanned_indices) >= max(1, len(reader.pages) // 2):
        ocr_pages = render_and_ocr_pdf(
            path,
            page_numbers=scanned_indices[:PDF_RENDER_MAX_PAGES],
            vision_model=vision_model,
            text_model=text_model,
        )
    else:
        ocr_pages = {}

    for index, text in enumerate(raw_pages, start=1):
        if index in ocr_pages:
            pages.append(f"[PDF 第 {index} 页 · 视觉OCR]\n{ocr_pages[index]}")
        elif text:
            pages.append(f"[PDF 第 {index} 页]\n{text}")

    return pages or ["PDF 未提取到可用文字，请在确认页手动补充题干。"]


def render_and_ocr_pdf(
    path: Path,
    *,
    page_numbers: list[int],
    vision_model: str | None,
    text_model: str | None,
) -> dict[int, str]:
    """用 pypdfium2 把指定页渲染成 PNG，再走视觉 OCR 和文本修正。失败时返回空 dict。"""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return {}

    client = AIClient()
    if not client.enabled:
        return {}

    results: dict[int, str] = {}
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception:  # pragma: no cover - 渲染失败时静默回退
        return {}

    try:
        for page_number in page_numbers:
            zero_based = page_number - 1
            if zero_based < 0 or zero_based >= len(document):
                continue
            try:
                page = document[zero_based]
                pil_image = page.render(scale=PDF_RENDER_SCALE).to_pil()
            except Exception:
                continue
            buffer = io.BytesIO()
            pil_image.save(buffer, format="PNG")
            raw = client.extract_text_from_image_bytes(
                buffer.getvalue(),
                mime_type="image/png",
                model_override=vision_model,
            )
            if not raw:
                continue
            polished = client.polish_ocr_text(raw, model_override=text_model)
            results[page_number] = polished or raw
    finally:
        try:
            document.close()
        except Exception:
            pass
    return results


def extract_docx(path: Path) -> list[str]:
    document = Document(str(path))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()).strip()
    return [text or "Word 文档未提取到可用文字，请在确认页手动补充题干。"]


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return "文件无法按文本解析，请在确认页手动补充题干。"
