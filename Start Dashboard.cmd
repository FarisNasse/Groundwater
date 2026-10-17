@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3.12 -c "import sys" >nul 2>nul
if errorlevel 1 goto use_py3
py -3.12 start_dashboard.py
if errorlevel 1 pause
exit /b
:use_py3
py -3 start_dashboard.py
if errorlevel 1 pause
exit /b
:use_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python start_dashboard.py
if errorlevel 1 pause
exit /b
:no_python
echo Install Python 3.12 from python.org, then open this file again.
pause
