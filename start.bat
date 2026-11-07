@echo off
chcp 65001 >nul
title 招标需求解析组件 - 快速启动
Set-Location 'C:\Users\yuzukii\Desktop\tender_extractor'
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "ROOT=%~dp0"
set "PY_CMD="

py -3 -c "import sys" >nul 2>nul && set "PY_CMD=py -3"
if not defined PY_CMD (
    python -c "import sys" >nul 2>nul && set "PY_CMD=python"
)
if not defined PY_CMD (
    echo [错误] 未找到 Python 3.10+，请先安装：https://www.python.org/downloads/
    echo        安装时请勾选 "Add Python to PATH"。
    if "%~1"=="" pause
    exit /b 1
)

%PY_CMD% "%ROOT%scripts\launch.py" %*
set "CODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %CODE%
