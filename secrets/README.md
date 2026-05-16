# secrets/

Carpeta para credenciales y archivos sensibles. **Todo el contenido (excepto este README y `.gitkeep`) está en `.gitignore`.**

## Archivos esperados

| Archivo | Contenido | Origen |
|---|---|---|
| `google_service_account.json` | Service account de Google Cloud con acceso a las Sheets de KPI y Cédulas | Google Cloud Console → IAM → Service Accounts |

## Cómo regenerar las credenciales de Google

1. Google Cloud Console → proyecto correspondiente
2. IAM & Admin → Service Accounts → crear o seleccionar uno
3. Keys → Add Key → JSON → descargar
4. Renombrar a `google_service_account.json` y colocar aquí
5. Compartir las Sheets destino con el email del service account (`...@...iam.gserviceaccount.com`)

## Nunca

- No subas estos archivos al repo
- No los pegues en chats, issues, ni capturas de pantalla
- Si se filtra una credencial, **revócala inmediatamente** en Google Cloud Console
