// env_writer.pas
// Escritura de .env y secrets/google_service_account.json + aplicacion de
// ACL restrictivo (solo el usuario actual puede leer/escribir).

procedure WriteEnvFile(const RepoDir: string; const SheetsId: string);
var
  EnvContent: TArrayOfString;
begin
  SetArrayLength(EnvContent, 6);
  EnvContent[0] := '# Generado por KPIGenerator-Setup.exe';
  EnvContent[1] := '# No editar manualmente salvo necesidad.';
  EnvContent[2] := '';
  EnvContent[3] := 'CEDULAS_SOURCE=excel';
  EnvContent[4] := 'SHEETS_ID_KPI=' + SheetsId;
  EnvContent[5] := 'GOOGLE_CREDENTIALS_PATH=secrets/google_service_account.json';
  SaveStringsToFile(RepoDir + '\.env', EnvContent, False);
end;

procedure WriteServiceAccountJson(const RepoDir: string; const JsonText: string);
var
  SecretsDir: string;
  JsonPath: string;
  Lines: TArrayOfString;
begin
  SecretsDir := RepoDir + '\secrets';
  ForceDirectories(SecretsDir);
  JsonPath := SecretsDir + '\google_service_account.json';

  // SaveStringsToFile espera array; lo metemos como una sola "linea"
  // (el contenido ya tiene sus propios saltos de linea internos).
  SetArrayLength(Lines, 1);
  Lines[0] := JsonText;
  SaveStringsToFile(JsonPath, Lines, False);
end;

// --- Backup/restore de .env y secrets/ a {tmp} (para reinstalacion/actualizacion) ---
// Permite borrar repo/ entero (necesario para RenameFile sobre directorio nuevo)
// y restaurar credenciales despues.

function GetCredentialsBackupDir(): string;
begin
  Result := ExpandConstant('{tmp}\creds-backup');
end;

procedure BackupCredentialsToTmp(const RepoDir: string);
var
  BackupDir: string;
  ResultCode: Integer;
  EnvSrc, EnvDst, SecretsSrc, SecretsDst: string;
begin
  BackupDir := GetCredentialsBackupDir();
  ForceDirectories(BackupDir);
  EnvSrc := RepoDir + '\.env';
  EnvDst := BackupDir + '\.env';
  SecretsSrc := RepoDir + '\secrets';
  SecretsDst := BackupDir + '\secrets';
  // Solo respaldar si hay algo que respaldar (primera instalacion no tiene).
  if FileExists(EnvSrc) then CopyFile(EnvSrc, EnvDst, False);
  if DirExists(SecretsSrc) then begin
    Exec(ExpandConstant('{sys}\cmd.exe'), Format('/c xcopy "%s" "%s" /E /Y /Q /I', [SecretsSrc, SecretsDst]), '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

procedure RestoreCredentialsFromTmp(const RepoDir: string);
var
  BackupDir: string;
  ResultCode: Integer;
  EnvSrc, EnvDst, SecretsSrc, SecretsDst: string;
begin
  BackupDir := GetCredentialsBackupDir();
  if not DirExists(BackupDir) then Exit;
  EnvSrc := BackupDir + '\.env';
  EnvDst := RepoDir + '\.env';
  SecretsSrc := BackupDir + '\secrets';
  SecretsDst := RepoDir + '\secrets';
  if FileExists(EnvSrc) then CopyFile(EnvSrc, EnvDst, False);
  if DirExists(SecretsSrc) then begin
    ForceDirectories(SecretsDst);
    Exec(ExpandConstant('{sys}\cmd.exe'), Format('/c xcopy "%s" "%s" /E /Y /Q /I', [SecretsSrc, SecretsDst]), '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// --- ACL restrictivo: solo el usuario actual lee/escribe ---
// Usa icacls.exe (incluido en Windows).
//   /inheritance:r        -> remueve permisos heredados
//   /grant:r %USERNAME%:F -> da Full control al usuario actual
procedure ApplyRestrictiveAcl(const FilePath: string);
var
  ResultCode: Integer;
  Params: string;
  UserName: string;
begin
  if not FileExists(FilePath) then Exit;
  UserName := GetUserNameString();
  Params := Format('"%s" /inheritance:r /grant:r "%s:F" /T /C /Q', [FilePath, UserName]);
  Exec(ExpandConstant('{sys}\icacls.exe'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Si falla, no abortar — el archivo existe, solo no esta blindado.
  // (Por ej. en USB / FAT32 donde icacls no aplica.)
end;
