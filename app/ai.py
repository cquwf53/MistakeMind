from __future__ import annotations

import base64
import json
import mimetypes
import re
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app.config import get_settings

TEXT_MODEL_PRIORITY = [
    "gpt-4o",
    "gpt-4-turbo",
    "claude-3-5-sonnet",
    "claude-3-opus",
    "gemini-1.5-pro",
    "qwen-max",
    "deepseek-chat",
]
VISION_MODEL_PRIORITY = [
    "gpt-4o",
    "gpt-4-vision",
    "claude-3-5-sonnet",
    "claude-3-opus",
    "gemini-1.5-pro",
    "qwen-vl",
    "glm-4v",
]
MODEL_EXCLUDES = ["embedding", "embed", "rerank", "whisper", "tts", "audio", "moderation"]


class AIClient:
    """Small OpenAI-compatible client using stdlib urllib to keep the app lightweight."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._models: list[dict[str, Any]] | None = None
        self._resolved_text_model: str | None = None
        self._resolved_vision_model: str | None = None
        self.last_error: str = ""
        self.last_model: str = ""

    @property
    def enabled(self) -> bool:
        return self.settings.ai_enabled

    def list_models(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        if self._models is not None:
            return self._models
        payload = self._request_json("GET", "models", timeout=20)
        data = payload.get("data", []) if payload else []
        self._models = [item for item in data if isinstance(item, dict) and item.get("id")]
        return self._models

    def resolve_model(self, *, vision: bool = False) -> str:
        return self.resolve_models(vision=vision)[0]

    def resolve_models(self, *, vision: bool = False) -> list[str]:
        configured = self.settings.ai_vision_model if vision else self.settings.ai_model
        if configured.lower() != "auto":
            models = [item.strip() for item in configured.split(",") if item.strip()]
            return models or ["gpt-4o-mini"]
        if vision and self._resolved_vision_model:
            return [self._resolved_vision_model]
        if not vision and self._resolved_text_model:
            return [self._resolved_text_model]
        model = self._select_preferred_model(vision=vision) or "gpt-4o-mini"
        if vision:
            self._resolved_vision_model = model
        else:
            self._resolved_text_model = model
        return [model]

    def analyze_mistake(
        self,
        *,
        subject_name: str,
        question_text: str,
        answer_text: str,
        user_error: str,
        knowledge_paths: list[str],
        error_types: list[str],
        model_override: str | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        prompt = {
            "subject": subject_name,
            "question_text": question_text,
            "answer_text": answer_text,
            "user_error": user_error,
            "knowledge_paths": knowledge_paths,
            "allowed_error_types": error_types,
            "required_json_keys": [
                "knowledge_path",
                "error_type",
                "difficulty",
                "confidence",
                "ai_summary",
                "variant_question",
                "review_suggestion",
            ],
        }
        content = (
            "你是考研错题归因助手。请只输出 JSON，不要 Markdown。"
            "knowledge_path 必须尽量从给定 knowledge_paths 中选择；"
            "error_type 必须从 allowed_error_types 中选择；"
            "difficulty 是 1-5 的整数，confidence 是 0-1 的小数。"
        )
        return self._chat_json(
            [
                {"role": "system", "content": content},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            models=[model_override] if model_override else None,
        )

    def extract_text_from_image(self, image_path: Path, model_override: str | None = None) -> str | None:
        if not self.enabled:
            return None

        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return self._extract_text_from_data_url(f"data:{mime_type};base64,{encoded}", model_override=model_override)

    def extract_text_from_image_bytes(
        self,
        image_bytes: bytes,
        *,
        mime_type: str = "image/png",
        model_override: str | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return self._extract_text_from_data_url(f"data:{mime_type};base64,{encoded}", model_override=model_override)

    def _extract_text_from_data_url(self, data_url: str, model_override: str | None = None) -> str | None:
        messages = [
            {
                "role": "system",
                "content": "你是错题图片 OCR 助手。请提取题干、选项、已知条件和学生标注，直接返回纯文本。",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别这张错题图片中的文字，保留公式和题号。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        response = self._chat(messages, vision=True, models=[model_override] if model_override else None)
        return response.strip() if response else None

    def polish_ocr_text(self, raw_text: str, model_override: str | None = None) -> str | None:
        """让文本模型把 OCR 噪声文本整理成规范题干。失败返回 None，由调用方决定是否回退到原文。"""
        if not self.enabled:
            return None
        cleaned = (raw_text or "").strip()
        if not cleaned:
            return None
        messages = [
            {
                "role": "system",
                "content": (
                    "你是考研错题 OCR 文本整理助手。"
                    "请把用户给的 OCR 原始文本整理成规范题干，修正错别字、标点和空格，"
                    "保留题号、选项编号、公式（用 LaTeX）和数学符号。"
                    "如果识别为多道题，用空行分隔。只输出整理后的纯文本，不要解释，不要 Markdown。"
                ),
            },
            {"role": "user", "content": cleaned},
        ]
        response = self._chat(messages, models=[model_override] if model_override else None)
        if not response:
            return None
        polished = response.strip()
        return polished or None

    def _select_preferred_model(self, *, vision: bool) -> str | None:
        models = self.list_models()
        model_ids = [str(item["id"]) for item in models]
        usable = [model_id for model_id in model_ids if not any(token in model_id.lower() for token in MODEL_EXCLUDES)]
        priorities = VISION_MODEL_PRIORITY if vision else TEXT_MODEL_PRIORITY
        for priority in priorities:
            matches = [model_id for model_id in usable if priority in model_id.lower()]
            if matches:
                return sorted(matches, key=lambda item: (("mini" in item.lower()), len(item)))[0]
        if models:
            newest = sorted(models, key=lambda item: int(item.get("created") or 0), reverse=True)
            for item in newest:
                model_id = str(item["id"])
                if model_id in usable:
                    return model_id
        return usable[0] if usable else None

    def _chat_json(self, messages: list[dict[str, Any]], models: list[str] | None = None) -> dict[str, Any] | None:
        response = self._chat(messages, models=models)
        if not response:
            return None
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", response, flags=re.S)
            if not match:
                return None
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

    def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        vision: bool = False,
        timeout: int = 45,
        models: list[str] | None = None,
    ) -> str | None:
        self.last_model = ""
        for model in models or self.resolve_models(vision=vision):
            body = {
                "model": model,
                "messages": messages,
            }
            payload = self._request_json("POST", "chat/completions", body=body, timeout=timeout)
            content = self._extract_content(payload)
            if content:
                self.last_model = model
                return content
        return None

    def _extract_content(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        content = payload.get("choices", [{}])[0].get("message", {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts) if parts else None
        return None

    def _candidate_urls(self, path: str) -> list[str]:
        base_url = self.settings.ai_base_url.rstrip("/")
        clean_path = path.lstrip("/")
        if base_url.endswith("/v1"):
            return [f"{base_url}/{clean_path}"]
        urls = [f"{base_url}/v1/{clean_path}", f"{base_url}/{clean_path}"]
        return list(dict.fromkeys(urls))

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: int,
    ) -> dict[str, Any] | None:
        self.last_error = ""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {self.settings.ai_api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        for url in self._candidate_urls(path):
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:300]
                self.last_error = f"{exc.code} {urllib.parse.urlparse(url).path} {detail}"
                continue
            except urllib.error.URLError as exc:
                self.last_error = f"URL error {urllib.parse.urlparse(url).path} {exc.reason}"
                continue
            except TimeoutError:
                self.last_error = f"Timeout {urllib.parse.urlparse(url).path}"
                continue
            except json.JSONDecodeError as exc:
                self.last_error = f"Invalid JSON {urllib.parse.urlparse(url).path} {exc}"
                continue
        return None
