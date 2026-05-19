# 考研/课程错题自动归因系统

一个面向复试展示的单用户 Web 系统：支持 `408` 与 `数一` 两个独立知识域，完成错题录入、AI/规则归因、人工确认、间隔复习、学习画像和周报导出。

## 功能闭环

- 录入方式：手动录入、图片/截图、PDF、Word、文本文件。
- 归因分析：知识点、错因类型、难度、置信度、归因摘要、变式题、复习建议。
- 学科隔离：`408` 与 `数一` 的错题库、统计、复习计划完全分离。
- 自动化复习：确认错题后自动生成当天、1 天、3 天、7 天、14 天复习任务。
- 学习画像：展示薄弱知识点、错因分布饼图、最近错因趋势折线图、复习完成率和最近错题。
- 来源管理：文件上传后保留来源记录，可按学科查看并下载原文件。
- 周报导出：按学科导出 Markdown / PDF。
- 离线兜底：未配置 AI API 时使用关键词和规则完成初步归因，系统仍可完整演示。
- 模型选择：默认按候选模型自动 fallback，也可以在录入或重新分析时手动指定本次模型。
- OCR 二阶段：图片/扫描 PDF 先走视觉模型抽取原始文本，再由文本模型修正 OCR 噪声。
- 扫描 PDF 支持：文本极少的 PDF 页面自动渲染为图片并走视觉 OCR。
- 自动调度：APScheduler 每晚自动生成明日复习清单、每周一自动生成学习周报。

## 技术栈

- FastAPI
- Jinja2 + HTMX 渐进增强
- SQLite + SQLAlchemy + Alembic
- pypdf / pypdfium2 / python-docx / reportlab
- APScheduler（后台定时任务）
- OpenAI-compatible Chat API
- SVG 数据可视化（饼图 + 折线图，离线可用）

## 快速启动

### PowerShell（推荐）

```powershell
.\start.ps1
```

### 手动

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Docker

```bash
docker build -t mistake-mind .
docker run -p 8000:8000 --env-file .env mistake-mind
```

打开 `http://127.0.0.1:8000`。

## AI 配置

复制 `.env.example` 为 `.env`，或直接设置环境变量：

```powershell
$env:AI_BASE_URL="https://api.openai.com/v1"
$env:AI_API_KEY="你的 API Key"
$env:AI_MODEL="auto"
$env:AI_VISION_MODEL="auto"
```

兼容大多数 OpenAI-style `/chat/completions` 服务。未配置 key 时，页面右上角显示"规则兜底"。

图片 OCR 和 AI 结构化归因需要配置可用的视觉/文本模型 API；未配置时图片会进入人工补充题干流程。

如果供应商只给了域名和明确端点，例如 `https://example.com` + `/v1/chat/completions`，建议把 `AI_BASE_URL` 写成 `https://example.com/v1`，避免请求先命中非 `/v1` 路径。

`AI_MODEL` 和 `AI_VISION_MODEL` 可以写成逗号分隔的候选列表。页面默认使用自动 fallback；如果在录入页或确认页选择了某个模型，本次分析只使用该模型，失败后转入规则兜底并保留人工确认流程。

## 自动调度

系统启动后 APScheduler 自动运行：

- **每晚 22:00**：为所有学科生成明日复习清单 → `storage/tomorrow_reviews_{subject}.md`
- **每周一 09:00**：为所有学科生成学习周报 → `storage/weekly_report_{subject}.md`

可通过环境变量调整：

```
APP_SCHEDULER_ENABLED=1
APP_SCHEDULER_TOMORROW_TIME=22:00
APP_SCHEDULER_WEEKLY_TIME=mon 09:00
```

设置 `APP_SCHEDULER_ENABLED=0` 可禁用调度。

## 数据库

应用启动时会自动创建 SQLite 表并写入内置知识点树。也可以使用 Alembic：

```powershell
.\.venv\Scripts\python -m alembic upgrade head
```

## 演示数据和复习清单

写入复试展示用演示数据：

```powershell
.\.venv\Scripts\python -m app.cli seed-demo
```

生成明日复习清单：

```powershell
.\.venv\Scripts\python -m app.cli tomorrow-reviews --output storage/tomorrow_reviews.md
```

也可以只生成某个学科：

```powershell
.\.venv\Scripts\python -m app.cli tomorrow-reviews --subject 408
.\.venv\Scripts\python -m app.cli tomorrow-reviews --subject math1
```

## 测试

```powershell
.\.venv\Scripts\python -m pytest
```

测试覆盖：

- 手动录入 -> 分析 -> 确认 -> 复习任务生成。
- `408` 与 `数一` 数据隔离。
- 周报 Markdown/PDF 导出。
- 文本文件导入。
- PDF 文件导入。
- Word 文档导入。
- 图片上传（无 AI 时提示手动补充）。
- AI 低置信度时确认页显示人工复核提示。
- OCR 文本修正二阶段验证。
- 错题库按知识点/掌握状态筛选。
- 来源文件列表。
- 演示数据和明日复习清单生成。
- 手动模型指定和记录。

## 复试展示建议

演示顺序建议固定为：生成演示数据 -> 切换学科 -> 上传错题 -> 查看 AI 建议 -> 人工修正 -> 确认生成复习计划 -> Dashboard 查看薄弱知识点/错因趋势 -> 查看来源文件 -> 导出周报。这样能同时讲清楚工程闭环、AI 可控性、知识点图谱、错因归因、推荐/间隔重复算法、任务调度、数据可视化和可追溯的数据来源。

## 复试可讲技术点

- OCR/多模态抽取 + 文本修正二阶段
- LLM 结构化输出（JSON schema 约束）
- 知识点图谱（三级树 + 关键词匹配）
- 错因归因（AI + 规则双通道）
- 间隔重复算法（0/1/3/7/14 天）
- APScheduler 后台任务调度
- SVG 数据可视化（饼图 + 折线图）
- 可编辑 AI 工作流（人工确认闭环）
- 扫描 PDF 渲染 + 视觉 OCR fallback
