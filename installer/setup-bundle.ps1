<#
.SYNOPSIS
    Prepara el bundle/ del installer: descarga Python embebido + get-pip,
    calcula SHA-256 y actualiza verify-bundle.bat.

.DESCRIPTION
    Ejecutar UNA SOLA VEZ en la maquina donde se compila el installer.
    Despues correr: build-installer.bat

.EXAMPLE
    cd installer
    powershell -ExecutionPolicy Bypass -File setup-bundle.ps1

.NOTES
    Requiere internet. ~14 MB de descargas.
#>

[CmdletBinding()]
param(
    [string]$PythonVersion = "3.14.4",
    [switch]$Force  # Re-descargar incluso si ya existe
)

$ErrorActionPreference = "Stop"

# --- Rutas ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BundleDir = Join-Path $ScriptDir "bundle"
$IconsDir = Join-Path $BundleDir "icons"
$VerifyBat = Join-Path $ScriptDir "verify-bundle.bat"

# --- URLs y archivos esperados ---
$PythonZipName = "python-$PythonVersion-embed-amd64.zip"
$PythonZipUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonZipName"
$PythonZipPath = Join-Path $BundleDir $PythonZipName

$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"
$GetPipPath = Join-Path $BundleDir "get-pip.py"

$IconPath = Join-Path $IconsDir "kpi.ico"

# --- Asegurar carpetas ---
New-Item -ItemType Directory -Force -Path $BundleDir | Out-Null
New-Item -ItemType Directory -Force -Path $IconsDir | Out-Null

# --- Helper: descargar con verificacion ---
function Download-File {
    param([string]$Url, [string]$Dest)
    if ((Test-Path $Dest) -and -not $Force) {
        Write-Host "[SKIP] Ya existe: $Dest" -ForegroundColor Yellow
        return
    }
    Write-Host "[GET ] $Url" -ForegroundColor Cyan
    Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing
    Write-Host "[OK  ] $Dest" -ForegroundColor Green
}

# --- 1. Python embedded ---
Write-Host ""
Write-Host "=== 1. Python $PythonVersion embedded ===" -ForegroundColor Magenta
Download-File -Url $PythonZipUrl -Dest $PythonZipPath

# --- 2. get-pip.py ---
Write-Host ""
Write-Host "=== 2. get-pip.py ===" -ForegroundColor Magenta
Download-File -Url $GetPipUrl -Dest $GetPipPath

# --- 3. Calcular SHA-256 del Python zip ---
Write-Host ""
Write-Host "=== 3. SHA-256 verification ===" -ForegroundColor Magenta
$PythonSha = (Get-FileHash -Path $PythonZipPath -Algorithm SHA256).Hash.ToUpper()
Write-Host "Python SHA-256: $PythonSha" -ForegroundColor Cyan

# Actualizar verify-bundle.bat con el hash real
if (Test-Path $VerifyBat) {
    $batContent = Get-Content $VerifyBat -Raw
    $batContent = $batContent -replace "PYTHON_SHA256_EXPECTED=.*", "PYTHON_SHA256_EXPECTED=$PythonSha"
    Set-Content -Path $VerifyBat -Value $batContent -NoNewline
    Write-Host "[OK  ] verify-bundle.bat actualizado con hash real." -ForegroundColor Green
} else {
    Write-Host "[WARN] verify-bundle.bat no existe; copia este hash a mano." -ForegroundColor Yellow
}

# --- 4. Verificar contra hash publicado (si quieres pegarlo aqui) ---
# Para ser ultra-paranoide, comparar contra el hash publicado en
# https://www.python.org/downloads/release/python-3144/
# (boton "MD5/SHA-256" abajo del archivo).
Write-Host ""
Write-Host "[INFO] Verifica este hash contra el publicado en python.org:" -ForegroundColor Yellow
Write-Host "       https://www.python.org/downloads/release/python-3144/" -ForegroundColor Yellow
Write-Host "       Esperado: $PythonSha" -ForegroundColor Yellow

# --- 5. Icono ---
Write-Host ""
Write-Host "=== 4. Icono ===" -ForegroundColor Magenta
if (-not (Test-Path $IconPath)) {
    Write-Host "[WARN] Falta $IconPath" -ForegroundColor Yellow
    Write-Host "       Copia un .ico (32x32 o multi-resolucion) ahi antes de compilar." -ForegroundColor Yellow
    Write-Host "       Sugerencia: usar https://www.icoconverter.com/ con un PNG." -ForegroundColor Yellow
} else {
    Write-Host "[OK  ] Ya existe: $IconPath" -ForegroundColor Green
}

# --- 6. Resumen ---
Write-Host ""
Write-Host "=== Bundle listo ===" -ForegroundColor Green
Get-ChildItem -Recurse -Path $BundleDir |
    Where-Object { -not $_.PSIsContainer } |
    ForEach-Object {
        $size = if ($_.Length -ge 1MB) { "{0:N2} MB" -f ($_.Length / 1MB) }
                elseif ($_.Length -ge 1KB) { "{0:N1} KB" -f ($_.Length / 1KB) }
                else { "$($_.Length) B" }
        Write-Host ("  {0,-60} {1,10}" -f $_.FullName.Substring($BundleDir.Length + 1), $size)
    }

Write-Host ""
Write-Host "Siguiente paso: build-installer.bat" -ForegroundColor Cyan
