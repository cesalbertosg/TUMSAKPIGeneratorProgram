@echo off
REM verify-bundle.bat
REM Verifica SHA-256 de los archivos en bundle/ contra los hashes publicados.
REM Detecta manipulacion del USB o descarga corrupta.

setlocal enabledelayedexpansion

REM SHA-256 publicado en python.org/downloads para Python 3.14.4 embed amd64
REM IMPORTANTE: actualizar este hash cuando se cambie de version
set PYTHON_SHA256_EXPECTED=CDA80A9B1E75C0F1B4F9872CA1B417F0D19BCE32FACC811AEA9180E70FAD5FB9

set PYTHON_ZIP=bundle\python-3.14.4-embed-amd64.zip

if not exist "%PYTHON_ZIP%" (
    echo [ERR] Falta %PYTHON_ZIP%
    exit /b 1
)

echo [INFO] Calculando SHA-256 de %PYTHON_ZIP%...
for /f "skip=1 tokens=*" %%H in ('certutil -hashfile "%PYTHON_ZIP%" SHA256 ^| findstr /v "hash"') do (
    set HASH_LINE=%%H
    goto :done
)
:done

REM Quitar espacios del hash
set ACTUAL=!HASH_LINE: =!

echo Hash esperado: %PYTHON_SHA256_EXPECTED%
echo Hash actual:   !ACTUAL!

if /i "!ACTUAL!"=="%PYTHON_SHA256_EXPECTED%" (
    echo [OK] Python embedded zip verificado.
    exit /b 0
) else (
    echo [WARN] Hash NO coincide. Bundle posiblemente manipulado o version distinta.
    echo Si descargaste recien, actualiza PYTHON_SHA256_EXPECTED en este script.
    exit /b 2
)

endlocal
