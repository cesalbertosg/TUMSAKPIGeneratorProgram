// install_mode_wizard.pas
// Wizard de seleccion de modo cuando se detecta instalacion previa.
// Aparece solo si IsAlreadyInstalled() retorna True; permite elegir entre:
//   - Reinstalar  (usa el repo bundled en el USB)
//   - Actualizar  (descarga el ultimo tag de GitHub Releases publico)
//   - Desinstalar (corre uninstaller selectivo y sale)

const
  GITHUB_OWNER = 'cesalbertosg';
  GITHUB_REPO  = 'TUMSAKPIGeneratorProgram';
  APP_ID       = '{B1C5E8A3-4F2D-4A1E-9B7C-2E8D5F3C7A91}';

var
  ModePage: TWizardPage;
  RbReinstall, RbUpdate, RbUninstall: TNewRadioButton;
  // Modo seleccionado (vacio si no hay ModePage, ej. primera instalacion).
  InstallMode: string;
  // Tag a usar para extraer el repo. Vacio = usar el bundled.
  // Se setea a algo como 'v0.5.2' cuando elige Actualizar.
  OverrideTag: string;

// --- Detecta instalacion previa via Registry (UninstallString) ---
function IsAlreadyInstalled(): Boolean;
var
  S: string;
  Key32, Key64: string;
begin
  Key32 := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + APP_ID + '_is1';
  Key64 := 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\' + APP_ID + '_is1';
  Result := RegQueryStringValue(HKCU, Key32, 'UninstallString', S)
         or RegQueryStringValue(HKCU, Key64, 'UninstallString', S)
         or RegQueryStringValue(HKLM, Key32, 'UninstallString', S)
         or RegQueryStringValue(HKLM, Key64, 'UninstallString', S);
end;

// --- Devuelve la ruta del unins000.exe registrado ---
function GetUninstallerPath(): string;
var
  Key32, Key64: string;
  S: string;
begin
  Result := '';
  Key32 := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + APP_ID + '_is1';
  Key64 := 'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\' + APP_ID + '_is1';
  if RegQueryStringValue(HKCU, Key32, 'UninstallString', S) then begin Result := S; Exit; end;
  if RegQueryStringValue(HKCU, Key64, 'UninstallString', S) then begin Result := S; Exit; end;
  if RegQueryStringValue(HKLM, Key32, 'UninstallString', S) then begin Result := S; Exit; end;
  if RegQueryStringValue(HKLM, Key64, 'UninstallString', S) then begin Result := S; Exit; end;
end;

// --- Crea la pagina de modo ---
procedure CreateInstallModePage();
begin
  ModePage := CreateCustomPage(wpWelcome,
    'Instalacion existente detectada',
    'KPI Generator ya esta instalado. Que quieres hacer?');

  RbReinstall := TNewRadioButton.Create(WizardForm);
  RbReinstall.Parent := ModePage.Surface;
  RbReinstall.Caption := 'Reinstalar (usar la version incluida en este USB)';
  RbReinstall.Top := 16;
  RbReinstall.Left := 16;
  RbReinstall.Width := ModePage.SurfaceWidth - 32;
  RbReinstall.Height := 20;
  RbReinstall.Checked := True;

  RbUpdate := TNewRadioButton.Create(WizardForm);
  RbUpdate.Parent := ModePage.Surface;
  RbUpdate.Caption := 'Actualizar (descargar la ultima version publicada en GitHub)';
  RbUpdate.Top := 50;
  RbUpdate.Left := 16;
  RbUpdate.Width := ModePage.SurfaceWidth - 32;
  RbUpdate.Height := 20;

  RbUninstall := TNewRadioButton.Create(WizardForm);
  RbUninstall.Parent := ModePage.Surface;
  RbUninstall.Caption := 'Desinstalar (conserva tus credenciales en repo\.env y repo\secrets\)';
  RbUninstall.Top := 84;
  RbUninstall.Left := 16;
  RbUninstall.Width := ModePage.SurfaceWidth - 32;
  RbUninstall.Height := 20;
end;

// --- Consulta GitHub Tags API para obtener el tag mas reciente ---
// Endpoint /tags funciona sin auth si el repo es publico. Devuelve un array
// JSON ordenado descendentemente por fecha de commit. Buscamos la PRIMERA
// ocurrencia de '"name": "...".
//
// Nota: /releases/latest seria mas elegante pero requiere que GitHub releases
// formales esten creados. /tags solo necesita que existan tags git.
function GetLatestTag(): string;
var
  ResultCode: Integer;
  TmpFile: string;
  Url: string;
  CurlParams: string;
  Lines: TArrayOfString;
  i, P, Q: Integer;
  Line: string;
  Combined: string;
begin
  Result := '';
  TmpFile := ExpandConstant('{tmp}\tags.json');
  Url := 'https://api.github.com/repos/' + GITHUB_OWNER + '/' + GITHUB_REPO + '/tags';
  CurlParams := Format('-L --tlsv1.2 --proto =https --fail --silent --show-error -A "KPIGeneratorInstaller" -o "%s" "%s"', [TmpFile, Url]);
  Exec(ExpandConstant('{sys}\curl.exe'), CurlParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if (ResultCode <> 0) or (not FileExists(TmpFile)) then Exit;

  if not LoadStringsFromFile(TmpFile, Lines) then Exit;
  // Concatenar todas las lineas en una sola string para parseo robusto
  Combined := '';
  for i := 0 to GetArrayLength(Lines) - 1 do
    Combined := Combined + Lines[i];

  // Buscar la primera ocurrencia de '"name"' (primera clave del primer objeto del array)
  P := Pos('"name"', Combined);
  if P = 0 then Exit;
  // Avanzar despues de "name"
  Line := Copy(Combined, P + 6, Length(Combined));
  // Saltar : y espacios, encontrar el primer "
  P := Pos('"', Line);
  if P = 0 then Exit;
  // El tag esta entre P+1 y el siguiente "
  Q := Pos('"', Copy(Line, P + 1, Length(Line)));
  if Q = 0 then Exit;
  Result := Copy(Line, P + 1, Q - 1);
end;

// --- Descarga el archive ZIP de un tag desde GitHub ---
function DownloadRepoAtTag(const Tag, DestPath: string): Boolean;
var
  ResultCode: Integer;
  Url: string;
  CurlParams: string;
begin
  Url := 'https://github.com/' + GITHUB_OWNER + '/' + GITHUB_REPO + '/archive/refs/tags/' + Tag + '.zip';
  CurlParams := Format('-L --tlsv1.2 --proto =https --fail --silent --show-error -o "%s" "%s"', [DestPath, Url]);
  Exec(ExpandConstant('{sys}\curl.exe'), CurlParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0) and FileExists(DestPath);
end;

// --- Ejecuta el uninstaller registrado en modo silencioso y aborta el setup ---
procedure RunUninstallerAndExit();
var
  UninstPath: string;
  ResultCode: Integer;
  P: Integer;
begin
  UninstPath := GetUninstallerPath();
  // UninstallString puede venir con quotes y argumentos: "C:\...\unins000.exe" /UNINSTALL
  if (Length(UninstPath) > 0) and (UninstPath[1] = '"') then begin
    P := Pos('"', Copy(UninstPath, 2, Length(UninstPath)));
    if P > 0 then
      UninstPath := Copy(UninstPath, 2, P - 1);
  end;
  if FileExists(UninstPath) then
    Exec(UninstPath, '/SILENT', '', SW_SHOW, ewWaitUntilTerminated, ResultCode)
  else
    MsgBox('No se encontro el uninstaller registrado: ' + UninstPath, mbError, MB_OK);
  // Abort detiene el setup limpiamente (sin continuar con la instalacion)
  Abort;
end;

// --- Despacha la accion segun el modo elegido ---
// Se llama desde NextButtonClick(ModePage.ID). Retorna False si el wizard
// debe pararse (caso uninstall ya ejecutado).
function HandleModeSelection(): Boolean;
begin
  Result := True;
  if RbUninstall.Checked then begin
    InstallMode := 'uninstall';
    RunUninstallerAndExit();
    Result := False;
    Exit;
  end;
  if RbUpdate.Checked then begin
    InstallMode := 'update';
    WizardForm.StatusLabel.Caption := 'Consultando ultimo release en GitHub...';
    OverrideTag := GetLatestTag();
    if OverrideTag = '' then begin
      MsgBox('No se pudo consultar el ultimo release en GitHub. ' +
             'Verifica conexion a internet. Volviendo a Reinstalar.', mbInformation, MB_OK);
      InstallMode := 'reinstall';
      RbReinstall.Checked := True;
      Exit;
    end;
  end else begin
    InstallMode := 'reinstall';
  end;
end;
