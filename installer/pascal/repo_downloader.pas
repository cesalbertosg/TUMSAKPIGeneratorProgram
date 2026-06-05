// repo_downloader.pas
// Descarga del ZIP del repo desde GitHub usando curl (Windows 10+ trae curl.exe).
// curl con TLS estricto: solo HTTPS, sin downgrade.

function DownloadRepoZip(const Url: string; const DestPath: string): Boolean;
var
  ResultCode: Integer;
  CurlParams: string;
begin
  CurlParams := Format(
    '-L --tlsv1.2 --proto =https --fail --silent --show-error -o "%s" "%s"',
    [DestPath, Url]
  );
  Exec(
    ExpandConstant('{sys}\curl.exe'),
    CurlParams,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Result := (ResultCode = 0) and FileExists(DestPath);
end;

// --- Extraer un ZIP usando Shell.Application (sin dependencias externas) ---
procedure ExtractZip(const ZipPath: string; const DestDir: string);
var
  Shell: Variant;
  Source, Target: Variant;
begin
  ForceDirectories(DestDir);
  Shell := CreateOleObject('Shell.Application');
  Source := Shell.NameSpace(ZipPath);
  Target := Shell.NameSpace(DestDir);
  if VarIsNull(Source) or VarIsNull(Target) then begin
    MsgBox('No se pudo abrir el ZIP: ' + ZipPath, mbError, MB_OK);
    Abort;
  end;
  // 16 = silenciar promps, 4 = ocultar progreso del Explorer
  Target.CopyHere(Source.Items, 16 or 4);
end;

// --- Python embebido viene con `import site` deshabilitado por default ---
// Hay que editar pythonXXX._pth para que el `pip install -e .` funcione.
procedure EnablePythonSitePackages(const PythonDir: string);
var
  PthFiles: TArrayOfString;
  Lines: TArrayOfString;
  i, j: Integer;
  PthPath: string;
begin
  FindFiles(PythonDir + '\python*._pth', PthFiles);
  for i := 0 to GetArrayLength(PthFiles) - 1 do begin
    PthPath := PthFiles[i];
    if LoadStringsFromFile(PthPath, Lines) then begin
      for j := 0 to GetArrayLength(Lines) - 1 do begin
        if Lines[j] = '#import site' then
          Lines[j] := 'import site';
      end;
      SaveStringsToFile(PthPath, Lines, False);
    end;
  end;
end;

// --- FindFiles helper (FindFirstFile + FindNextFile + arreglo) ---
procedure FindFiles(const Pattern: string; out Results: TArrayOfString);
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
