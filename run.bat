@echo off
REM QuickBooks Bank Statement Converter - Launcher
REM This script runs the application from the src directory

cd /d "%~dp0"
python src\app.py
pause
