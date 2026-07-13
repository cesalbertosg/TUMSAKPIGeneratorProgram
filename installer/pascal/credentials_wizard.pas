// credentials_wizard.pas
// 2 paginas custom del installer:
//   1. Service Account JSON (textarea grande + boton "Cargar archivo")
//   2. Archivo .env (selector de archivo — se COPIA tal cual, nunca se
//      genera desde una plantilla ni valores hardcodeados en el instalador)
//
// v0.6.8: se elimino la pagina de "Google Sheets ID" + el .env generado por
// WriteEnvFile (plantilla fija con SHEETS_ID_KPI y un DEFAULT_SHEETS_ID
// hardcodeado en este archivo). Ahora el .env se prepara de antemano
// (fuera del instalador) y solo se copia — cero valores de configuracion
// viven en el codigo fuente del instalador. En "Reinstalar"/"Actualizar"
// esta pagina ni aparece: RestoreCredentialsFromTmp (env_writer.pas) ya
// preserva el .env existente sin tocarlo.
//
// Estas paginas alimentan las variables globales PageJson, PageEnvFile
// declaradas aqui, y SelectedEnvPath (leido desde KPIGenerator-Setup.iss).

// --- Variables globales del wizard (compartidas con KPIGenerator-Setup.iss) ---
var
  PageJson: TInputQueryWizardPage;
  PageEnvFile: TInputQueryWizardPage;
  SelectedEnvPath: string;

// --- Boton "Cargar desde archivo" en pagina 1 (JSON) ---
procedure OnLoadJsonClick(Sender: TObject);
var
  SelectedFile: string;
  FileLines: TArrayOfString;
  i: Integer;
  Combined: string;
begin
  SelectedFile := '';
  if GetOpenFileName(
       'Selecciona el JSON del Service Account',
       SelectedFile,
       '',
       'JSON Service Account (*.json)|*.json|Todos|*.*',
       'json') then begin
    if LoadStringsFromFile(SelectedFile, FileLines) then begin
      Combined := '';
      for i := 0 to GetArrayLength(FileLines) - 1 do
        Combined := Combined + FileLines[i] + #13#10;
      PageJson.Values[0] := Combined;
    end else begin
      MsgBox('No se pudo leer el archivo.', mbError, MB_OK);
    end;
  end;
end;

// --- Boton "Seleccionar archivo .env..." en pagina 2 ---
// A diferencia del JSON (que se lee y re-escribe), aqui solo se guarda la
// RUTA elegida — CurStepChanged en el .iss hace un CopyFile puro. El
// contenido del .env nunca pasa por Pascal ni queda hardcodeado aqui.
procedure OnLoadEnvClick(Sender: TObject);
var
  SelectedFile: string;
begin
  SelectedFile := '';
  if GetOpenFileName(
       'Selecciona el archivo .env ya preparado',
       SelectedFile,
       '',
       'Archivo .env (*.env;.env)|*.env;.env|Todos (*.*)|*.*',
       '') then begin
    SelectedEnvPath := SelectedFile;
    PageEnvFile.Values[0] := SelectedFile;
  end;
end;

// --- Crear las 2 paginas ---
procedure CreateCredentialsPages();
var
  LoadJsonButton: TNewButton;
  LoadEnvButton: TNewButton;
begin
  // Pagina 1: Service Account JSON
  PageJson := CreateInputQueryPage(
    wpSelectDir,
    'Credenciales de Google',
    'Pega el JSON del Service Account o cargalo desde archivo',
    'Para sincronizar con Google Sheets se necesita un Service Account JSON. ' +
    'Solicita el archivo a Beto si no lo tienes.'
  );
  PageJson.Add('Service Account JSON:', False);

  LoadJsonButton := TNewButton.Create(WizardForm);
  LoadJsonButton.Parent := PageJson.Surface;
  LoadJsonButton.Caption := 'Cargar desde archivo...';
  LoadJsonButton.Top := PageJson.Edits[0].Top + PageJson.Edits[0].Height + 8;
  LoadJsonButton.Left := PageJson.Edits[0].Left;
  LoadJsonButton.Width := 180;
  LoadJsonButton.Height := 25;
  LoadJsonButton.OnClick := @OnLoadJsonClick;

  // Pagina 2: archivo .env (solo en instalacion nueva — ver ShouldSkipPage
  // en KPIGenerator-Setup.iss, que la salta si ya hay una instalacion
  // previa cuyo .env se va a restaurar).
  PageEnvFile := CreateInputQueryPage(
    PageJson.ID,
    'Archivo de configuracion (.env)',
    'Selecciona el archivo .env ya preparado con las credenciales',
    'Beto te debio compartir un archivo .env listo (junto con el JSON, ' +
    'normalmente en el mismo USB). Selecciona su ubicacion — se copia tal ' +
    'cual, el instalador no genera ni modifica su contenido.'
  );
  PageEnvFile.Add('Archivo .env:', False);
  PageEnvFile.Edits[0].ReadOnly := True;

  LoadEnvButton := TNewButton.Create(WizardForm);
  LoadEnvButton.Parent := PageEnvFile.Surface;
  LoadEnvButton.Caption := 'Seleccionar archivo .env...';
  LoadEnvButton.Top := PageEnvFile.Edits[0].Top + PageEnvFile.Edits[0].Height + 8;
  LoadEnvButton.Left := PageEnvFile.Edits[0].Left;
  LoadEnvButton.Width := 200;
  LoadEnvButton.Height := 25;
  LoadEnvButton.OnClick := @OnLoadEnvClick;
end;
