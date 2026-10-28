@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 每日简报生成器
".venv\Scripts\python.exe" report.py
echo.
echo 按任意键关闭...
pause >nul
