# 一键启动脚本 (PowerShell)
$ErrorActionPreference = "Stop"

$venvPython = ".\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Host "正在创建虚拟环境..." -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "安装依赖..." -ForegroundColor Cyan
& $venvPython -m pip install -q -r requirements.txt

Write-Host "运行数据库迁移..." -ForegroundColor Cyan
& $venvPython -m alembic upgrade head

Write-Host "启动服务 http://127.0.0.1:8000" -ForegroundColor Green
& $venvPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
