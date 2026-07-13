# Installer — KPIGenerator-Setup.exe

Bootstrap installer **Inno Setup** que entrega KPI Generator a Yaneth en una
laptop limpia (sin Python instalado, sin VPN, sin dependencias del repo).

Distribucion: USB fisico → doble-click → wizard.

---

## Que hace el installer

1. Welcome + carpeta destino (default `%LOCALAPPDATA%\KPIGenerator`).
2. Extrae **Python 3.14.4 embedded** (incluido en `bundle/`).
3. Descarga la ultima release del repo (`https://github.com/cesalbertosg/TUMSAKPIGeneratorProgram/archive/refs/tags/{TAG}.zip`).
4. `pip install -e .` (sin extras `db` — Yaneth NO usa PostgreSQL).
5. Wizard custom de credenciales (v0.6.8+, solo en instalacion **nueva** —
   en Reinstalar/Actualizar se saltan, el `.env` existente se preserva tal
   cual):
   - Pega Service Account JSON (o carga desde archivo).
   - Selecciona el archivo `.env` **ya preparado** de antemano por Beto
     (junto al JSON, normalmente en el mismo USB) — el instalador lo copia
     tal cual, no genera ni hardcodea ningun valor de configuracion.
6. Copia `.env` (seleccionado) y escribe `secrets/google_service_account.json`
   (desde el JSON pegado), con ACL restrictivo (solo Yaneth lee).
7. Crea shortcuts: Desktop + Start Menu → `KPIGenerator.exe -m kpi_generator`.
8. Finaliza con opcion de abrir la GUI.

---

## Estructura del modulo

```
installer/
├── KPIGenerator-Setup.iss      # Script principal Inno Setup
├── pascal/
│   ├── credentials_wizard.pas  # 2 paginas custom (JSON, seleccionar .env) — solo instalacion nueva
│   ├── repo_downloader.pas     # curl HTTPS al ZIP del tag
│   └── env_writer.pas          # Escribe secrets/ (JSON) + backup/restore de .env + ACL
├── bundle/
│   ├── python-3.14.4-embed-amd64.zip  # Python embebido oficial (~12 MB)
│   ├── get-pip.py                       # Bootstrap de pip
│   └── icons/kpi.ico                    # Icono de la app
├── assets/
│   ├── wizard-image.bmp        # Banner lateral (164×314)
│   └── header.bmp              # Header de paginas (150×60)
├── build-installer.bat         # Compila el .iss con iscc.exe
├── verify-bundle.bat           # SHA-256 check de bundle/*
└── README-installer.md         # Este archivo
```

---

## Preparacion previa (una sola vez en mi maquina)

### 1. Instalar Inno Setup

Descargar de https://jrsoftware.org/isdl.php (Unicode, version 6+). Instalar.
El compilador `iscc.exe` queda en `C:\Program Files (x86)\Inno Setup 6\`.

### 2. Descargar bundle (automático)

```powershell
cd installer
powershell -ExecutionPolicy Bypass -File setup-bundle.ps1
```

Esto descarga Python 3.14.4 embebido + get-pip.py, calcula SHA-256 y
actualiza `verify-bundle.bat` con el hash real. ~14 MB de descargas.

Falta opcional: copiar un `kpi.ico` (32×32 o multi-resolución) a
`bundle/icons/kpi.ico`. Si no, Inno Setup usa el icono default.

### 3. Crear release en GitHub

El installer descarga un TAG especifico. Antes de compilar:

```bash
git tag -a v0.5.1 -m "release para Yaneth"
git push origin v0.5.1
```

Y editar `KPIGenerator-Setup.iss` linea `#define RELEASE_TAG "v0.5.1"`.

### 4. Service account de Google

**Antes de entregar el USB**, verificar en Google Cloud Console:
- El service account tiene rol `Editor` SOLO sobre el Sheet `KPI KM Auto`.
- NO tiene Drive completo ni acceso a otros Sheets.
- Si tiene mas privilegios, crear uno nuevo limitado.

---

## Compilacion

```cmd
cd installer
build-installer.bat
```

Output: `installer\dist\KPIGenerator-Setup.exe` (~30 MB).

---

## Entrega

1. Copiar `KPIGenerator-Setup.exe` al USB.
2. Copiar tambien un `.env` **ya preparado** (con `SHEETS_ID_KPI`,
   `GOOGLE_CREDENTIALS_PATH=secrets/google_service_account.json`,
   `CEDULAS_SOURCE=excel`, etc.) al mismo USB — el wizard lo pide y lo copia
   tal cual, no lo genera (v0.6.8+). Solo hace falta para instalacion
   **nueva**; en reinstalaciones el `.env` existente se preserva solo.
3. Imprimir el LEEME.txt con pasos para Yaneth.
4. Yaneth conecta USB → doble-click → sigue el wizard → tiene Service
   Account JSON y el `.env` preparado a la mano.

---

## Reinstalacion / Actualizacion

Yaneth corre el installer otra vez:
- Detecta carpeta existente.
- Pregunta si quiere reinstalar (reemplaza repo + dependencias).
- **Mantiene** `.env` y `secrets/` si existen.

Para nueva version de KPI Generator: hacer release nuevo tag + recompilar installer + nuevo USB.

---

## Troubleshooting

| Sintoma | Causa probable | Solucion |
|---|---|---|
| Defender bloquea el .exe | Falso positivo PyInstaller-like | Excepcion manual en Defender |
| "No se puede descargar el repo" | Sin internet o GitHub caido | Reintentar; o repo offline en USB v2 |
| GUI abre pero no procesa | Service Account JSON invalido | Verificar JSON + permisos del Sheet |
| Error "fuente db no disponible" | Yaneth selecciono 'db' por error | Cambiar dropdown a 'excel' |
