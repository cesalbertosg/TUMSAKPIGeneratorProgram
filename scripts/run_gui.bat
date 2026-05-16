@echo off
REM Lanza la GUI del KPI Generator.
REM Requiere: `pip install -e .` previo desde la raíz del proyecto.

setlocal
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=C:\Users\Data Analyst\AppData\Local\Programs\Python\Python314\python.exe"
)

"%PYTHON%" -m kpi_generator
endlocal
