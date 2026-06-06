// repo_downloader.pas
// Descarga del ZIP del repo desde GitHub usando curl (Windows 10+ trae curl.exe).
// curl con TLS estricto: solo HTTPS, sin downgrade.

function DownloadRepoZip(const Url: string; const DestPath: string): Boolean;
var
  ResultCode: Integer;
  CurlParams: string;
begin
  CurlParams := Format('-L --tlsv1.2 --proto =https --fail --silent --show-error -o "%s" "%s"', [DestPath, Url]);
  Exec(ExpandConstant('{sys}\curl.exe'), CurlParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := (ResultCode = 0) and FileExists(DestPath);
end;

// --- Extraer un ZIP usando PowerShell Expand-Archive ---
// Sincrono (ewWaitUntilTerminated) — Shell.Application.CopyHere era asincrono
// y dejaba la carpeta vacia cuando el installer continuaba al siguiente paso.
procedure ExtractZip(const ZipPath: string; const DestDir: string);
var
  ResultCode: Integer;
  PsCmd: string;
begin
  ForceDirectories(DestDir);
  PsCmd := Format('-NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath ''%s'' -DestinationPath ''%s'' -Force"', [ZipPath, DestDir]);
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), PsCmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then begin
    MsgBox(Format('Falló la extracción de %s a %s (codigo %d).', [ZipPath, DestDir, ResultCode]), mbError, MB_OK);
    Abort;
  end;
end;

// --- FindFiles helper (FindFirstFile + FindNextFile + arreglo) ---
procedure FindFilesPattern(const Pattern: string; var Results: TArrayOfString);
var
  FindRec: TFindRec;
  Dir: string;
  Count: Integer;
begin
  SetArrayLength(Results, 0);
  Count := 0;
  Dir := ExtractFilePath(Pattern);
  if FindFirst(Pattern, FindRec) then begin
    try
      repeat
        SetArrayLength(Results, Count + 1);
        Results[Count] := Dir + FindRec.Name;
        Count := Count + 1;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

// --- Python embebido viene con `import site` deshabilitado por default ---
// Hay que editar pythonXXX._pth para que:
//   - `import site` este activo (habilita site-packages para pip)
//   - `Lib` este en sys.path (donde copiamos tkinter)
procedure EnablePythonSitePackages(const PythonDir: string);
var
  PthFiles: TArrayOfString;
  Lines, NewLines: TArrayOfString;
  i, j, k: Integer;
  PthPath: string;
  HasLib: Boolean;
begin
  FindFilesPattern(PythonDir + '\python*._pth', PthFiles);
  for i := 0 to GetArrayLength(PthFiles) - 1 do begin
    PthPath := PthFiles[i];
    if LoadStringsFromFile(PthPath, Lines) then begin
      HasLib := False;
      for j := 0 to GetArrayLength(Lines) - 1 do begin
        if (Lines[j] = '#import site') or (Lines[j] = '# import site') then
          Lines[j] := 'import site';
        if Trim(Lines[j]) = 'Lib' then
          HasLib := True;
      end;
      if not HasLib then begin
        SetArrayLength(NewLines, GetArrayLength(Lines) + 2);
        NewLines[0] := 'Lib';
        NewLines[1] := 'Lib\site-packages';
        for k := 0 to GetArrayLength(Lines) - 1 do
          NewLines[k + 2] := Lines[k];
        SaveStringsToFile(PthPath, NewLines, False);
      end else begin
        SaveStringsToFile(PthPath, Lines, False);
      end;
    end;
  end;
end;
