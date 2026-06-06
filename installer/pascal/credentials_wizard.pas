// credentials_wizard.pas
// 3 paginas custom del installer:
//   1. Service Account JSON (textarea grande + boton "Cargar archivo")
//   2. Google Sheets ID (prellenado con SHEETS_ID del KPI KM Auto)
//   3. Confirmacion (CEDULAS_SOURCE=excel, recap)
//
// Estas paginas alimentan las variables globales PageJson, PageSheetsId,
// PageConfirm declaradas en KPIGenerator-Setup.iss.

// Sheet por defecto del wizard (puede ser sobrescrito).
const
  DEFAULT_SHEETS_ID = '1sv8P004Ej85D_GF4YwEmoBO1XqWR1KYdGOSb1FJWM8Y';

// --- Variables globales del wizard (compartidas con KPIGenerator-Setup.iss) ---
var
  PageJson: TInputQueryWizardPage;
  PageSheetsId: TInputQueryWizardPage;
  PageConfirm: TOutputMsgWizardPage;

// --- Boton "Cargar desde archivo" en pagina 1 ---
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

// --- Crear las 3 paginas ---
procedure CreateCredentialsPages();
var
  LoadButton: TNewButton;
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

  // Boton "Cargar desde archivo" debajo del textarea
  LoadButton := TNewButton.Create(WizardForm);
  LoadButton.Parent := PageJson.Surface;
  LoadButton.Caption := 'Cargar desde archivo...';
  LoadButton.Top := PageJson.Edits[0].Top + PageJson.Edits[0].Height + 8;
  LoadButton.Left := PageJson.Edits[0].Left;
  LoadButton.Width := 180;
  LoadButton.Height := 25;
  LoadButton.OnClick := @OnLoadJsonClick;

  // Pagina 2: Google Sheets ID
  PageSheetsId := CreateInputQueryPage(
    PageJson.ID,
    'Google Sheet de destino',
    'ID del Sheet "KPI KM Auto" donde se publicaran los KPIs',
    'El installer prellena el ID del Sheet de produccion. ' +
    'Cambialo solo si Yaneth tiene un Sheet propio.'
  );
  PageSheetsId.Add('SHEETS_ID_KPI:', False);
  PageSheetsId.Values[0] := DEFAULT_SHEETS_ID;

  // Pagina 3: Confirmacion
  PageConfirm := CreateOutputMsgPage(
    PageSheetsId.ID,
    'Resumen de configuracion',
    'Revisa antes de instalar',
    'KPI Generator se instalara con los siguientes valores:' + #13#10 +
    '' + #13#10 +
    '  - Fuente de cedulas: Excel (carpeta seleccionada en la GUI)' + #13#10 +
    '  - Google Sheets: configurado con el ID que indicaste' + #13#10 +
    '  - Service Account: el JSON se guardara en {app}\repo\secrets\' + #13#10 +
    '' + #13#10 +
    'Solo Yaneth tendra permisos de lectura sobre el .env y secrets/. ' +
    'Si necesitas cambiar algun valor mas tarde, edita los archivos manualmente.'
  );
end;
