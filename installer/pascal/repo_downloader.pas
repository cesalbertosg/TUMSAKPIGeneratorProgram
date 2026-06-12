// repo_downloader.pas
// Helpers para configurar el Python embedded post-extraccion.

// Wrapper alrededor de la API nativa ExtractArchive de Inno Setup 7.
// La firma usada aqui es la del scripting reference (test): 5 parametros con
// resultado de tipo Integer (codigo HRESULT, 0 = OK).
// Extraccion via tar.exe + cmd.exe como wrapper.
// Por que no ExtractArchive (Inno 7): la lib interna rechaza algunos ZIPs estandar
// con "formato no soportado". tar de BSD (System32\tar.exe en Win10 1803+) los maneja.
// Por que cmd.exe: permite invocar tar.exe de System32 sin que Inno haga
// File System Redirection a SysWOW64. cmd.exe respeta {win}\System32\... literal.
procedure ExtractZip(const ZipPath: string; const DestDir: string);
var
  ResultCode: Integer;
  CmdParams: string;
begin
  ForceDirectories(DestDir);
  // cmd.exe /c "tar.exe -x -f ZIP -C DEST"   — quotes anidados con ""
  CmdParams := Format('/c ""%s\System32\tar.exe" -x -f "%s" -C "%s""', [ExpandConstant('{win}'), ZipPath, DestDir]);
  Exec(ExpandConstant('{sys}\cmd.exe'), CmdParams, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if ResultCode <> 0 then begin
    MsgBox(Format('Fallo la extraccion de %s a %s (codigo %d).' + #13#10 + 'Verifica que tar.exe esta disponible (Windows 10 1803+).', [ZipPath, DestDir, ResultCode]), mbError, MB_OK);
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
