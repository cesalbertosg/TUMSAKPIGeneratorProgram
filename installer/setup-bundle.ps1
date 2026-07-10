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
    [string]$RepoTag = "v0.6.6",
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

# --- 5. Tkinter (NO viene en Python embedded) ---
Write-Host ""
Write-Host "=== 5. Tkinter add-on (Python embedded NO incluye Tk) ===" -ForegroundColor Magenta

$TkAddonZip = Join-Path $BundleDir "tkinter-addon.zip"
$FullPythonRoot = "C:\Users\Data Analyst\AppData\Local\Programs\Python\Python314"

if ((Test-Path $TkAddonZip) -and -not $Force) {
    Write-Host "[SKIP] Ya existe: $TkAddonZip" -ForegroundColor Yellow
} elseif (-not (Test-Path $FullPythonRoot)) {
    Write-Host "[WARN] No se encontro Python full install en $FullPythonRoot" -ForegroundColor Yellow
    Write-Host "       El installer fallara al abrir la GUI (Tkinter ausente)." -ForegroundColor Yellow
    Write-Host "       Instala Python 3.14.4 full y reintenta." -ForegroundColor Yellow
} else {
    Write-Host "[GET ] Copiando Tk de $FullPythonRoot ..." -ForegroundColor Cyan
    $TempStage = Join-Path $env:TEMP "kpi-tk-stage"
    if (Test-Path $TempStage) { Remove-Item -Recurse -Force $TempStage }
    New-Item -ItemType Directory -Force -Path $TempStage | Out-Null

    # Archivos y carpetas necesarios para tkinter en Python embedded.
    $items = @(
        @{ src = "Lib\tkinter"; dst = "Lib\tkinter" },
        @{ src = "tcl";         dst = "tcl"        },
        @{ src = "DLLs\_tkinter.pyd";  dst = "_tkinter.pyd"   },
        @{ src = "DLLs\tcl86t.dll";    dst = "tcl86t.dll"     },
        @{ src = "DLLs\tk86t.dll";     dst = "tk86t.dll"      },
        @{ src = "DLLs\zlib1.dll";     dst = "zlib1.dll"      }
    )
    foreach ($item in $items) {
        $srcPath = Join-Path $FullPythonRoot $item.src
        $dstPath = Join-Path $TempStage $item.dst
        if (-not (Test-Path $srcPath)) {
            Write-Host "[WARN] Falta $srcPath (se omite)" -ForegroundColor Yellow
            continue
        }
        $dstDir = Split-Path -Parent $dstPath
        if ($dstDir) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        if ((Get-Item $srcPath).PSIsContainer) {
            Copy-Item -Recurse -Force $srcPath $dstPath
        } else {
            Copy-Item -Force $srcPath $dstPath
        }
    }
    Compress-Archive -Path "$TempStage\*" -DestinationPath $TkAddonZip -Force
    Remove-Item -Recurse -Force $TempStage
    $TkSize = "{0:N1} MB" -f ((Get-Item $TkAddonZip).Length / 1MB)
    Write-Host "[OK  ] $TkAddonZip ($TkSize)" -ForegroundColor Green
}

# --- 6. Repo ZIP via git archive (el repo en GitHub es privado) ---
Write-Host ""
Write-Host "=== 6. Repo ZIP local desde tag $RepoTag ===" -ForegroundColor Magenta
$RepoZipPath = Join-Path $BundleDir "repo.zip"
$RepoRoot = Split-Path -Parent $ScriptDir  # ../KPI Generator Program (raiz del repo git)

if ((Test-Path $RepoZipPath) -and -not $Force) {
    Write-Host "[SKIP] Ya existe: $RepoZipPath" -ForegroundColor Yellow
} else {
    Push-Location $RepoRoot
    try {
        # git archive produce un ZIP con el contenido del tag (sin metadata .git).
        # El prefix imita el comportamiento del archive de GitHub
        # (TUMSAKPIGeneratorProgram-<tag-sin-v>/...).
        $tagWithoutV = $RepoTag -replace '^v', ''
        $prefix = "TUMSAKPIGeneratorProgram-$tagWithoutV/"
        Write-Host "[GIT ] git archive --prefix=$prefix $RepoTag" -ForegroundColor Cyan
        & git archive --format=zip --prefix=$prefix --output=$RepoZipPath $RepoTag
        if ($LASTEXITCODE -ne 0) {
            throw "git archive fallo con exit $LASTEXITCODE"
        }
        $sz = "{0:N1} MB" -f ((Get-Item $RepoZipPath).Length / 1MB)
        Write-Host "[OK  ] $RepoZipPath ($sz)" -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

# --- 7. Icono ---
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
