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
  Params := Format('"%s" /inheritance:r /grant:r "%s:F" /T /C /Q',
                   [FilePath, UserName]);
  Exec(
    ExpandConstant('{sys}\icacls.exe'),
    Params,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  // Si falla, no abortar — el archivo existe, solo no esta blindado.
  // (Por ej. en USB / FAT32 donde icacls no aplica.)
end;
