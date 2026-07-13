; KPIGenerator-Setup.iss
; Bootstrap installer Inno Setup para KPI Generator (distribucion Yaneth).
;
; Compila con: iscc.exe KPIGenerator-Setup.iss
; Genera: dist\KPIGenerator-Setup.exe
;
; Ver README-installer.md para preparacion previa.

#define MyAppName "KPI Generator"
#define MyAppVersion "0.6.8"
#define MyAppPublisher "TUMSA"
#define MyAppURL "https://github.com/cesalbertosg/TUMSAKPIGeneratorProgram"
#define RELEASE_TAG "v0.6.8"

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
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=KPIGenerator-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; ArchiveExtraction=auto habilita el flag `extractarchive` en [Files] —
; Inno extrae los ZIPs internamente sin depender de tar / PowerShell.
ArchiveExtraction=auto
SetupIconFile=bundle\icons\kpi.ico
UninstallDisplayIcon={app}\bundle\icons\kpi.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Files]
; ZIPs van a {tmp} y se extraen en [Code] con ExtractZip (API nativa Inno).
; Pattern original — sin extractarchive porque eso requiere `external` (Inno 7 beta).
Source: "bundle\python-3.14.4-embed-amd64.zip"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\tkinter-addon.zip"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\repo.zip"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\get-pip.py"; DestDir: "{tmp}"; Flags: deleteafterinstall
Source: "bundle\icons\kpi.ico"; DestDir: "{app}\bundle\icons"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\python\python.exe"; \
  Parameters: "-m kpi_generator"; WorkingDir: "{app}\repo"; \
  IconFilename: "{app}\bundle\icons\kpi.ico"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\python\python.exe"; \
  Parameters: "-m kpi_generator"; WorkingDir: "{app}\repo"; \
  IconFilename: "{app}\bundle\icons\kpi.ico"

[Run]
Filename: "{app}\python\python.exe"; Parameters: "-m kpi_generator"; \
  WorkingDir: "{app}\repo"; Description: "Abrir {#MyAppName} ahora"; \
  Flags: nowait postinstall skipifsilent

[Code]
// Helpers (variables globales del wizard viven dentro de cada .pas)
#include "pascal\repo_downloader.pas"
#include "pascal\credentials_wizard.pas"
#include "pascal\env_writer.pas"
#include "pascal\install_mode_wizard.pas"

procedure InitializeWizard;
begin
  // Si ya esta instalado, mostrar pagina de modo (Reinstalar/Actualizar/Desinstalar)
  if IsAlreadyInstalled() then
    CreateInstallModePage();
  CreateCredentialsPages();
end;

// PageEnvFile (seleccionar .env) solo aplica a instalacion NUEVA: en
// Reinstalar/Actualizar, RestoreCredentialsFromTmp ya preserva el .env
// existente sin tocarlo — pedir uno nuevo ahi seria redundante y arriesgaria
// que alguien pise por accidente un .env que ya funciona.
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = PageEnvFile.ID) and IsAlreadyInstalled();
end;

// --- Borra repo/ y python/ pero CONSERVA repo\.env y repo\secrets\ ---
procedure DeleteRepoExceptCredentials(const RepoDir: string);
var
  FindRec: TFindRec;
  EntryPath: string;
  NameLower: string;
begin
  if not DirExists(RepoDir) then Exit;
  if FindFirst(RepoDir + '\*', FindRec) then try
    repeat
      if (FindRec.Name <> '.') and (FindRec.Name <> '..') then begin
        EntryPath := RepoDir + '\' + FindRec.Name;
        NameLower := Lowercase(FindRec.Name);
        // Conservar .env y secrets/ para reinstalacion sin re-pegar credenciales
        if (NameLower = '.env') or (NameLower = 'secrets') then begin
          // skip
        end else begin
          if (FindRec.Attributes and $00000010) <> 0 then  // FILE_ATTRIBUTE_DIRECTORY
            DelTree(EntryPath, True, True, True)
          else
            DeleteFile(EntryPath);
        end;
      end;
    until not FindNext(FindRec);
  finally
    FindClose(FindRec);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  PythonDir: string;
  RepoDir: string;
  ExtractedRepoDir: string;
begin
  if CurStep = ssPostInstall then
  begin
    PythonDir := ExpandConstant('{app}\python');
    RepoDir := ExpandConstant('{app}\repo');
    // El ZIP del repo se extrae a {app}\TUMSAKPIGeneratorProgram-<tag-sin-v>\
    ExtractedRepoDir := ExpandConstant('{app}\TUMSAKPIGeneratorProgram-' + Copy('{#RELEASE_TAG}', 2, 99));

    // 1. Extraer Python embebido
    WizardForm.StatusLabel.Caption := 'Instalando Python embebido...';
    ExtractZip(ExpandConstant('{tmp}\python-3.14.4-embed-amd64.zip'), PythonDir);

    // 2. Superponer Tkinter (no incluido en embedded oficial)
    WizardForm.StatusLabel.Caption := 'Instalando Tkinter...';
    ExtractZip(ExpandConstant('{tmp}\tkinter-addon.zip'), PythonDir);

    // 3. Habilitar import site + agregar Lib al pythonXXX._pth
    EnablePythonSitePackages(PythonDir);

    // 4. Extraer el repo a {app}\repo
    //    Si OverrideTag esta seteado (modo Actualizar), descargar ese tag de
    //    GitHub. Si no, usar el repo.zip bundled.
    WizardForm.StatusLabel.Caption := 'Instalando KPI Generator...';
    if OverrideTag <> '' then begin
      WizardForm.StatusLabel.Caption := 'Descargando ' + OverrideTag + ' desde GitHub...';
      if not DownloadRepoAtTag(OverrideTag, ExpandConstant('{tmp}\repo.zip')) then begin
        MsgBox('Fallo la descarga del tag ' + OverrideTag + ' desde GitHub.' + #13#10 +
               'Verifica que el repo sea publico y haya conexion a internet.', mbError, MB_OK);
        Abort;
      end;
      // Re-calcular el nombre de la carpeta extraida (sin "v")
      ExtractedRepoDir := ExpandConstant('{app}\' + GITHUB_REPO + '-') + Copy(OverrideTag, 2, Length(OverrideTag));
    end;

    // En reinstalacion / actualizacion: respaldar .env y secrets/ a {tmp}, borrar
    // repo/ COMPLETO, extraer + renombrar, y restaurar credenciales.
    // RenameFile falla si destino existe; este flujo lo evita.
    BackupCredentialsToTmp(RepoDir);
    if DirExists(RepoDir) then DelTree(RepoDir, True, True, True);

    ExtractZip(ExpandConstant('{tmp}\repo.zip'), ExpandConstant('{app}'));
    if not DirExists(ExtractedRepoDir) then begin
      MsgBox('No se encontro la carpeta extraida: ' + ExtractedRepoDir, mbError, MB_OK);
      Abort;
    end;
    if not RenameFile(ExtractedRepoDir, RepoDir) then begin
      MsgBox('No se pudo renombrar el repo de ' + ExtractedRepoDir + ' a ' + RepoDir, mbError, MB_OK);
      Abort;
    end;
    RestoreCredentialsFromTmp(RepoDir);

    // 3. Instalar pip
    WizardForm.StatusLabel.Caption := 'Instalando pip...';
    Exec(PythonDir + '\python.exe', '"' + ExpandConstant('{tmp}\get-pip.py') + '" --no-warn-script-location', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then begin
      MsgBox('Error instalando pip (codigo ' + IntToStr(ResultCode) + ').', mbError, MB_OK);
      Abort;
    end;

    // 3b. Instalar setuptools + wheel — get-pip.py reciente ya no los incluye
    // y son necesarios para "pip install -e ." con pyproject.toml.
    WizardForm.StatusLabel.Caption := 'Instalando setuptools...';
    Exec(PythonDir + '\python.exe', '-m pip install --upgrade setuptools wheel --no-warn-script-location', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then begin
      MsgBox('Error instalando setuptools (codigo ' + IntToStr(ResultCode) + ').', mbError, MB_OK);
      Abort;
    end;

    // 4. Instalar dependencias del repo (sin extras `db`)
    WizardForm.StatusLabel.Caption := 'Instalando dependencias...';
    Exec(PythonDir + '\python.exe', '-m pip install -e "' + RepoDir + '" --no-warn-script-location', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    if ResultCode <> 0 then begin
      MsgBox('Error instalando dependencias (codigo ' + IntToStr(ResultCode) + ').', mbError, MB_OK);
      Abort;
    end;

    // 5. Credenciales: JSON se escribe desde la pagina del wizard (sin
    //    cambios); .env se COPIA del archivo seleccionado — solo en
    //    instalacion nueva (SelectedEnvPath queda '' en Reinstalar/
    //    Actualizar porque PageEnvFile se salta via ShouldSkipPage, y
    //    RestoreCredentialsFromTmp ya dejo el .env existente intacto).
    WizardForm.StatusLabel.Caption := 'Configurando credenciales...';
    WriteServiceAccountJson(RepoDir, PageJson.Values[0]);
    if SelectedEnvPath <> '' then
      CopyFile(SelectedEnvPath, RepoDir + '\.env', False);
    ApplyRestrictiveAcl(RepoDir + '\secrets\google_service_account.json');
    ApplyRestrictiveAcl(RepoDir + '\.env');
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;

  // Pagina de modo (solo aparece si IsAlreadyInstalled)
  if (ModePage <> nil) and (CurPageID = ModePage.ID) then begin
    Result := HandleModeSelection();
    Exit;
  end;

  if CurPageID = PageJson.ID then begin
    if Trim(PageJson.Values[0]) = '' then begin
      MsgBox('Pega el JSON del Service Account o cargalo desde archivo.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if Pos('"type": "service_account"', PageJson.Values[0]) = 0 then begin
      MsgBox('El JSON no parece ser un Service Account valido (falta "type": "service_account").', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;

  if CurPageID = PageEnvFile.ID then begin
    if Trim(SelectedEnvPath) = '' then begin
      MsgBox('Selecciona el archivo .env antes de continuar.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
    if not FileExists(SelectedEnvPath) then begin
      MsgBox('El archivo .env seleccionado ya no existe. Vuelve a seleccionarlo.', mbError, MB_OK);
      Result := False;
      Exit;
    end;
  end;
end;

// --- Uninstall paso a paso: borrar python/ y repo/ conservando credenciales ---
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  AppDir, PythonDir, RepoDir, BundleDir: string;
begin
  if CurUninstallStep = usPostUninstall then begin
    AppDir := ExpandConstant('{app}');
    PythonDir := AppDir + '\python';
    RepoDir := AppDir + '\repo';
    BundleDir := AppDir + '\bundle';

    // Python embebido fuera completo
    if DirExists(PythonDir) then DelTree(PythonDir, True, True, True);
    // Bundle (icono) fuera
    if DirExists(BundleDir) then DelTree(BundleDir, True, True, True);
    // Repo: borrar todo excepto .env y secrets/
    DeleteRepoExceptCredentials(RepoDir);
  end;
end;
