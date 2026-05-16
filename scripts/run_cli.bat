@echo off
REM Ejecuta el pipeline KPI sin GUI (para scheduler / automatización).
REM Editar los paths abajo o pasarlos como argumentos.

setlocal
set "PROJECT_ROOT=%~dp0.."
cd /d "%PROJECT_ROOT%"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=C:\Users\Data Analyst\AppData\Local\Programs\Python\Python314\python.exe"
)

REM Ejemplo con argumentos posicionales — ajusta a tus rutas reales:
REM "%PYTHON%" -m kpi_generator.cli run ^
REM     --trips "data-input\Viajes_de_abril.xlsx" ^
REM     --fuel "data-input\Diesel_abril.XLSX" ^
REM     --cedulas "data-input\Cedulas" ^
REM     --objectives "data-input\Objetivo de KM Abril.xlsx"

"%PYTHON%" -m kpi_generator.cli %*
endlocal
