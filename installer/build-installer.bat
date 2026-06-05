@echo off
REM build-installer.bat
REM Compila KPIGenerator-Setup.iss con Inno Setup 6.
REM Output: dist\KPIGenerator-Setup.exe

setlocal

REM Ruta tipica de Inno Setup 6 (ajustar si esta instalado en otro lado)
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if not exist %ISCC% (
    echo [ERR] No se encontro ISCC.exe en %ISCC%
    echo Instala Inno Setup desde https://jrsoftware.org/isdl.php
    exit /b 1
)

REM Verificar que el bundle de Python embebido existe
if not exist "bundle\python-3.14.4-embed-amd64.zip" (
    echo [ERR] Falta bundle\python-3.14.4-embed-amd64.zip
    echo Ver README-installer.md seccion "Preparacion previa".
    exit /b 1
)

if not exist "bundle\get-pip.py" (
    echo [ERR] Falta bundle\get-pip.py
    exit /b 1
)

if not exist "bundle\icons\kpi.ico" (
    echo [ERR] Falta bundle\icons\kpi.ico
    exit /b 1
)

REM Compilar
echo [INFO] Compilando KPIGenerator-Setup.iss...
%ISCC% KPIGenerator-Setup.iss
if errorlevel 1 (
    echo [ERR] Compilacion fallo.
    exit /b 1
)

echo.
echo [OK] Installer generado en: dist\KPIGenerator-Setup.exe
dir /b dist\KPIGenerator-Setup.exe

endlocal
