from __future__ import annotations

import argparse
import base64
import struct
import tempfile
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.ai import AIClient
from app.database import Base, SessionLocal, engine, ensure_compatible_schema
from app.models import Mistake, MistakeAnalysis, ReviewTask, Subject
from app.seed import ensure_seed_data
from app.services import create_review_tasks, find_knowledge_point, get_subject

PROBE_FONT = {
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def write_probe_png(path: Path, text: str = "OCR 123") -> None:
    width, height = 320, 120
    pixels = bytearray([255] * width * height * 3)
    scale = 10
    x = 24
    y = 24
    for char in text:
        glyph = PROBE_FONT.get(char.upper(), PROBE_FONT[" "])
        for row_index, row in enumerate(glyph):
            for col_index, value in enumerate(row):
                if value != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        px = x + col_index * scale + dx
                        py = y + row_index * scale + dy
                        if 0 <= px < width and 0 <= py < height:
                            index = (py * width + px) * 3
                            pixels[index : index + 3] = b"\x00\x00\x00"
        x += 6 * scale

    raw = bytearray()
    stride = width * 3
    for row in range(height):
        raw.append(0)
        raw.extend(pixels[row * stride : (row + 1) * stride])

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def prepare_database() -> None:
    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema()
    with SessionLocal() as db:
        ensure_seed_data(db)


def generate_tomorrow_reviews(subject_code: str | None = None) -> str:
    prepare_database()
    tomorrow = datetime.now().date() + timedelta(days=1)
    lines = [f"# {tomorrow.isoformat()} 复习清单", ""]
    with SessionLocal() as db:
        subjects = [get_subject(db, subject_code)] if subject_code else list(db.scalars(select(Subject).order_by(Subject.id)))
        for subject in subjects:
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
            lines.extend([f"## {subject.name}", ""])
            if not tasks:
                lines.extend(["- 暂无待复习任务", ""])
                continue
            for task in tasks:
                mistake = task.mistake
                knowledge_path = mistake.analysis.knowledge_path if mistake.analysis else "待分析知识点"
                question = mistake.question_text.replace("\n", " ")[:80]
                lines.append(f"- [{task.task_type}] {knowledge_path}：{question}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def seed_demo_data() -> None:
    prepare_database()
    samples = [
        (
            "408",
            "TCP 三次握手中 SYN、ACK 状态变化和四次挥手的区别。",
            "服务端收到 SYN 后返回 SYN+ACK，客户端再返回 ACK。",
            "把连接建立和释放过程混淆，审题时没有区分题目问的是握手还是挥手。",
            "计算机网络 / 传输层 / TCP与UDP",
            "审题错误",
            3,
        ),
        (
            "408",
            "二叉树先序遍历和中序遍历如何唯一还原二叉树？",
            "先序首元素是根，在中序中划分左右子树后递归构造。",
            "递归边界没写清楚，导致右子树划分错误。",
            "数据结构 / 树与图 / 树与二叉树",
            "边界条件遗漏",
            4,
        ),
        (
            "408",
            "Cache 组相联映射下主存块号如何拆分 tag、index 和 offset？",
            "根据块大小、组数和地址位数拆分。",
            "公式记错，把组号位数和块内偏移位数混在一起。",
            "计算机组成原理 / 存储系统 / Cache与主存",
            "公式/定理遗忘",
            3,
        ),
        (
            "math1",
            "求极限 lim sin x / x 并说明使用的等价无穷小。",
            "结果为 1，可使用 sin x ~ x。",
            "忘记等价无穷小适用条件。",
            "高等数学 / 函数极限连续 / 极限与无穷小",
            "公式/定理遗忘",
            2,
        ),
        (
            "math1",
            "判断矩阵是否可对角化，并说明特征值和特征向量条件。",
            "需要有足够线性无关的特征向量。",
            "只看特征值个数，没有检查特征向量个数。",
            "线性代数 / 特征理论 / 特征值与特征向量",
            "概念不清",
            4,
        ),
        (
            "math1",
            "二重积分换元到极坐标时积分区域和雅可比因子如何变化？",
            "面积元 dxdy 变为 r dr dθ。",
            "漏乘 r，属于计算和步骤问题。",
            "高等数学 / 重积分 / 二重积分与三重积分",
            "步骤不完整",
            3,
        ),
    ]
    with SessionLocal() as db:
        for index, (subject_code, question, answer, user_error, path, error_type, difficulty) in enumerate(samples):
            subject = get_subject(db, subject_code)
            exists = db.scalar(select(Mistake).where(Mistake.subject_id == subject.id, Mistake.question_text == question))
            if exists is not None:
                continue
            mistake = Mistake(
                subject_id=subject.id,
                question_text=question,
                answer_text=answer,
                user_error=user_error,
                source_type="demo",
                source_name="演示数据",
                source_path="",
                status="confirmed" if index % 3 != 0 else "pending",
                is_confirmed=index % 3 != 0,
                created_at=datetime.now() - timedelta(days=index % 7),
            )
            db.add(mistake)
            db.flush()
            knowledge_point = find_knowledge_point(db, subject, path)
            db.add(
                MistakeAnalysis(
                    mistake_id=mistake.id,
                    knowledge_point_id=knowledge_point.id if knowledge_point else None,
                    knowledge_path=knowledge_point.path if knowledge_point else path,
                    error_type=error_type,
                    difficulty=difficulty,
                    confidence=0.82,
                    ai_summary="演示数据：用于展示错题归因、复习计划和学习画像。",
                    variant_question="围绕同一知识点更换条件后再做一遍，并写出关键步骤。",
                    review_suggestion="先复盘原题错误原因，再完成一题同知识点变式练习。",
                )
            )
            if mistake.is_confirmed:
                create_review_tasks(db, mistake)
        db.commit()


def check_ai() -> None:
    client = AIClient()
    if not client.enabled:
        print("AI_API_KEY 未配置。")
        return

    print(f"文本模型候选：{', '.join(client.resolve_models())}")
    print(f"视觉模型候选：{', '.join(client.resolve_models(vision=True))}")

    text_response = client._chat([{"role": "user", "content": "只回复 OK"}])
    print(f"文本对话：{'通过' if text_response else '失败'}")

    json_payload = client.analyze_mistake(
        subject_name="408 计算机学科专业基础",
        question_text="TCP 三次握手中 SYN 和 ACK 的状态变化为何？",
        answer_text="服务端收到 SYN 后返回 SYN+ACK。",
        user_error="把三次握手和四次挥手看混。",
        knowledge_paths=["计算机网络 / 传输层 / TCP与UDP"],
        error_types=["概念不清", "审题错误", "其他"],
    )
    print(f"结构化归因：{'通过' if json_payload else '失败'}")

    image_path = Path(tempfile.gettempdir()) / "mistakemind_vision_probe.png"
    write_probe_png(image_path)
    try:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        data_url = f"data:image/png;base64,{encoded}"
        messages = [
            {"role": "system", "content": "You are an OCR assistant."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read the text in this image. Reply only the text."},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        vision_response = client._chat(messages, vision=True, timeout=20, models=client.resolve_models(vision=True))
    finally:
        image_path.unlink(missing_ok=True)
    print(f"图片输入：{'通过' if vision_response else '未通过'}")
    if vision_response:
        print(f"图片识别结果：{vision_response[:120]}")
    elif client.last_error:
        print(f"图片错误摘要：{client.last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description="考研错题归因系统辅助命令")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tomorrow_parser = subparsers.add_parser("tomorrow-reviews", help="生成明日复习清单 Markdown")
    tomorrow_parser.add_argument("--subject", choices=["408", "math1"], default=None)
    tomorrow_parser.add_argument("--output", default="storage/tomorrow_reviews.md")

    subparsers.add_parser("seed-demo", help="写入复试展示用演示数据")
    model_parser = subparsers.add_parser("ai-models", help="列出 AI 模型并显示自动推荐")
    model_parser.add_argument("--limit", type=int, default=30)
    subparsers.add_parser("ai-check", help="检查 AI 文本、结构化归因和图片输入能力")

    args = parser.parse_args()
    if args.command == "tomorrow-reviews":
        markdown = generate_tomorrow_reviews(args.subject)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
        print(f"已生成复习清单：{output_path}")
    elif args.command == "seed-demo":
        seed_demo_data()
        print("已写入演示数据。")
    elif args.command == "ai-models":
        client = AIClient()
        if not client.enabled:
            print("AI_API_KEY 未配置。请先在 .env 中设置 AI_BASE_URL 和 AI_API_KEY。")
            return
        models = client.list_models()
        print(f"文本模型推荐：{client.resolve_model()}")
        print(f"视觉模型推荐：{client.resolve_model(vision=True)}")
        print("可用模型：")
        for item in models[: args.limit]:
            print(f"- {item['id']}")
    elif args.command == "ai-check":
        check_ai()


if __name__ == "__main__":
    main()
