; KPIGenerator-Setup.iss
; Bootstrap installer Inno Setup para KPI Generator (distribucion Yaneth).
;
; Compila con: iscc.exe KPIGenerator-Setup.iss
; Genera: dist\KPIGenerator-Setup.exe
;
; Ver README-installer.md para preparacion previa.

#define MyAppName "KPI Generator"
#define MyAppVersion "0.5.1"
#define MyAppPublisher "TUMSA"
#define MyAppURL "https://github.com/cesalbertosg/TUMSAKPIGeneratorProgram"
#define MyAppExeName "KPIGenerator.exe"
#define RELEASE_TAG "v0.5.1"
#define PythonEmbedZip "python-3.14.4-embed-amd64.zip"
#define RepoZipUrl "https://github.com/cesalbertosg/TUMSAKPIGeneratorProgram/archive/refs/tags/" + RELEASE_TAG + ".zip"

[Setup]
AppId={{B1C5E8A3-4F2D-4A1E-9B7C-2E8D5F3C7A91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={localappdata}\KPIGenerator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=KPIGenerator-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; WizardImageFile y WizardSmallImageFile son opcionales — si no estan, Inno
; usa sus imagenes default. Descomenta cuando agregues los .bmp a assets/.
; WizardImageFile=assets\wizard-image.bmp
; WizardSmallImageFile=assets\header.bmp
SetupIconFile=bundle\icons\kpi.ico
UninstallDisplayIcon={app}\bundle\icons\kpi.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; Python embebido (~12 MB)
Source: "bundle\{#PythonEmbedZip}"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\get-pip.py"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\icons\kpi.ico"; DestDir: "{app}\bundle\icons"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\python\python.exe"; \
  Parameters: "-m kpi_generator"; WorkingDir: "{app}\repo"; \
  IconFilename: "{app}\bundle\icons\kpi.ico"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\python\python.exe"; \
  Parameters: "-m kpi_generator"; WorkingDir: "{app}\repo"; \
  IconFilename: "{app}\bundle\icons\kpi.ico"

[Run]
Filename: "{app}\python\python.exe"; Parameters: "-m kpi_generator"; \
  WorkingDir: "{app}\repo"; Description: "Abrir {#MyAppName} ahora"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Includes de helpers en Pascal.
// IMPORTANTE: las variables globales del wizard estan declaradas dentro de
// credentials_wizard.pas para que las funciones del mismo archivo las vean.
#include "pascal\repo_downloader.pas"
#include "pascal\credentials_wizard.pas"
#include "pascal\env_writer.pas"

// --- Setup del wizard ---
procedure InitializeWizard;
begin
  CreateCredentialsPages();
end;

// --- Tras aceptar el destino, instalar Python + repo + dependencias ---
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PythonDir: string;
  RepoDir: string;
  ZipPath: string;
begin
  if CurStep = ssInstall then
  begin
    PythonDir := ExpandConstant('{app}\python');
    RepoDir := ExpandConstant('{app}\repo');
    ZipPath := ExpandConstant('{tmp}\repo.zip');

    // 1. Extraer Python embebido
    WizardForm.StatusLabel.Caption := 'Instalando Python embebido...';
    ExtractZip(ExpandConstant('{tmp}\{#PythonEmbedZip}'), PythonDir);

    // 2. Habilitar import system (descomentar `import site` en pthXXX._pth)
    EnablePythonSitePackages(PythonDir);

    // 3. Instalar pip
    WizardForm.StatusLabel.Caption := 'Instalando pip...';
    Exec(PythonDir + '\python.exe',
         '"' + ExpandConstant('{tmp}\get-pip.py') + '" --no-warn-script-location',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then begin
      MsgBox('Error instalando pip (codigo ' + IntToStr(ResultCode) + ').',
             mbError, MB_OK);
      Abort;
    end;

    // 4. Descargar repo desde GitHub (tag fijo)
    WizardForm.StatusLabel.Caption := 'Descargando KPI Generator desde GitHub...';
    if not DownloadRepoZip('{#RepoZipUrl}', ZipPath) then begin
      MsgBox('No se pudo descargar el repositorio. Verifica conexion a internet.',
             mbError, MB_OK);
      Abort;
    end;
    ExtractZip(ZipPath, ExpandConstant('{app}'));

    // El ZIP de GitHub extrae a TUMSAKPIGeneratorProgram-{tag}\; renombrar
    RenameFile(
      ExpandConstant('{app}\TUMSAKPIGeneratorProgram-' + Copy('{#RELEASE_TAG}', 2, 99)),
      RepoDir
    );

    // 5. Instalar dependencias (sin extras `db` — Yaneth usa Excel)
    WizardForm.StatusLabel.Caption := 'Instalando dependencias...';
    Exec(PythonDir + '\python.exe',
         '-m pip install -e "' + RepoDir + '" --no-warn-script-location',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then begin
      MsgBox('Error instalando dependencias (codigo ' + IntToStr(ResultCode) + ').',
             mbError, MB_OK);
      Abort;
    end;

    // 6. Escribir .env y secrets/ desde las paginas del wizard
    WizardForm.StatusLabel.Caption := 'Configurando credenciales...';
    WriteEnvFile(RepoDir, PageSheetsId.Values[0]);
    WriteServiceAccountJson(RepoDir, PageJson.Values[0]);
    ApplyRestrictiveAcl(RepoDir + '\secrets\google_service_account.json');
    ApplyRestrictiveAcl(RepoDir + '\.env');
  end;
end;

// --- Validar antes de avanzar de las paginas custom ---
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  if CurPageID = PageJson.ID then begin
    if Trim(PageJson.Values[0]) = '' then begin
      MsgBox('Pega el JSON del Service Account o cargalo desde archivo.',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Pos('"type": "service_account"', PageJson.Values[0]) = 0 then begin
      MsgBox('El JSON no parece ser un Service Account valido ' +
             '(falta "type": "service_account").',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  if CurPageID = PageSheetsId.ID then begin
    if Length(Trim(PageSheetsId.Values[0])) < 20 then begin
      MsgBox('El ID del Google Sheet parece invalido (muy corto).',
             mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;
