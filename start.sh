#!/usr/bin/env bash
# 一键启动脚本 (Linux/macOS)
set -e

VENV_PYTHON=".venv/bin/python"

if [ ! -f "$VENV_PYTHON" ]; then
    echo "正在创建虚拟环境..."
    python3 -m venv .venv
fi

echo "安装依赖..."
$VENV_PYTHON -m pip install -q -r requirements.txt

echo "运行数据库迁移..."
$VENV_PYTHON -m alembic upgrade head

echo "启动服务 http://127.0.0.1:8000"
$VENV_PYTHON -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
