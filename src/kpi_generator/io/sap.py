"""
Extracción ZVPF desde SAP — coordenadas exactas calculadas de screenshot.
Estado inicial: pantalla de selección de ZVPF-1 ya cargada en SQVI.
"""
import sys, time, os, traceback, ctypes
sys.stdout.reconfigure(encoding='utf-8')

import win32gui, win32con, win32api, win32process, win32ui
import pyautogui
pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.2

from PIL import Image

DATE_FROM   = "01.05.2026"
DATE_TO     = "31.05.2026"
OUTPUT_DIR  = r"C:\Users\Data Analyst\Desktop\Alberto\2026\Q2\Claude\KPI Generator\KPI Generator Program\data-input"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "ZVPF_nuevo.XLSX")

SAP_CLIENT = "300"
SAP_USER   = "SOLANOGC"
SAP_PASS   = "c1348201998"

# ── captura de pantalla (PrintWindow bypasa DirectX) ─────────────────────────

def capture(hwnd, tag):
    rect = win32gui.GetWindowRect(hwnd)
    w, h = rect[2]-rect[0], rect[3]-rect[1]
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC  = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfcDC, w, h)
    saveDC.SelectObject(bmp)
    ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)
    bmpstr = bmp.GetBitmapBits(True)
    img = Image.frombuffer('RGB', (w, h), bmpstr, 'raw', 'BGRX', 0, 1)
    path = os.path.join(OUTPUT_DIR, f"sap_{tag}.png")
    img.save(path)
    win32gui.DeleteObject(bmp.GetHandle())
    saveDC.DeleteDC(); mfcDC.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwndDC)
    return path

# ── foco y click ──────────────────────────────────────────────────────────────

def click(x, y, delay=0.5):
    pyautogui.click(x, y)
    time.sleep(delay)

def force_focus(hwnd):
    ctypes.windll.user32.AllowSetForegroundWindow(-1)
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    rect = win32gui.GetWindowRect(hwnd)
    cx = (rect[0]+rect[2])//2
    cy = rect[1] + 80          # click en la barra de herramientas (zona neutra)
    pyautogui.click(cx, cy)
    time.sleep(0.6)

def find_sap():
    wins = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == 'SAP_FRONTEND_SESSION':
            wins.append((h, win32gui.GetWindowText(h)))
    win32gui.EnumWindows(cb, None)
    return wins

def find_sap_logon():
    wins = []
    def cb(h, _):
        if win32gui.IsWindowVisible(h) and 'SAP Logon' in win32gui.GetWindowText(h):
            wins.append((h, win32gui.GetWindowText(h)))
    win32gui.EnumWindows(cb, None)
    return wins

def open_session_from_logon():
    """Abre sesión SAP desde SAP Logon 750 con el sistema ya seleccionado."""
    logons = find_sap_logon()
    if not logons:
        print("[ERR] SAP Logon 750 no encontrado.")
        return False
    hwnd, title = logons[0]
    print(f"[OK] SAP Logon encontrado: '{title}' HWND={hwnd}")
    rect = win32gui.GetWindowRect(hwnd)
    L, T = rect[0], rect[1]
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.8)
    # Doble-click en "Sap Tumsa Productivo" ≈ x+200, y+170 (6ª entrada de la lista)
    print(f"[..] Doble-click en 'Sap Tumsa Productivo' ({L+200}, {T+170})...")
    pyautogui.doubleClick(L+200, T+170)
    wait(8)
    sessions = find_sap()
    if sessions:
        print(f"[OK] Sesión abierta: '{sessions[0][1]}'")
        return True
    print("[WARN] Sesión no apareció tras 8 s.")
    return False

def wait(s=1.0): time.sleep(s)
def tap(k, d=0.8): pyautogui.press(k); wait(d)
def enter(d=2.0): pyautogui.press('enter'); wait(d)
def f8(d=5.0): pyautogui.press('f8'); wait(d)
def write(t, d=0.5): pyautogui.typewrite(t, interval=0.07); wait(d)
def clear(): pyautogui.hotkey('ctrl','a'); pyautogui.press('delete'); wait(0.3)

# ── lógica principal ──────────────────────────────────────────────────────────

def main():
    print("=== SAP ZVPF Extractor v3 (coordenadas exactas) ===")

    sessions = find_sap()
    if not sessions:
        print("[..] No hay sesión SAP activa. Intentando abrir desde SAP Logon...")
        if not open_session_from_logon():
            print("[ERR] No se pudo abrir sesión SAP.")
            return
        sessions = find_sap()
        if not sessions:
            print("[ERR] Sesión sigue sin aparecer.")
            return
    hwnd, title = sessions[0]
    print(f"[OK] Ventana: '{title}' | HWND={hwnd}")

    rect = win32gui.GetWindowRect(hwnd)
    L, T = rect[0], rect[1]   # esquina superior-izquierda en pantalla
    print(f"[OK] Posición en pantalla: left={L}, top={T}")

    print("\n>>> Empieza en 3 s <<<\n")
    for i in range(3,0,-1):
        print(f"  {i}..."); wait(1)

    # ── Login si necesario ────────────────────────────────────────────────────
    needs_login = "/000 " in title or "S000" in title
    if needs_login:
        print("[..] Login...")
        force_focus(hwnd)
        clear(); write(SAP_USER)
        tap('tab', 0.3)
        clear(); write(SAP_PASS)
        enter(6)
        sessions = find_sap()
        hwnd, title = sessions[0]
        rect = win32gui.GetWindowRect(hwnd)
        L, T = rect[0], rect[1]
        print(f"[OK] Post-login: '{title}'")

    # ── Navegar a SQVI + ZVPF-1 (siempre — el HWND cambia con /n) ────────────
    print("[..] Navegando a SQVI...")
    force_focus(hwnd)
    write("/nSQVI"); enter(3)
    sessions = find_sap()           # re-enumerar: /n destruye y recrea la ventana
    hwnd, title = sessions[0]
    rect = win32gui.GetWindowRect(hwnd)
    L, T = rect[0], rect[1]
    print(f"[OK] Post-SQVI: '{title}'")
    p = capture(hwnd, "01_sqvi_inicial")
    print(f"[..] Screenshot SQVI: {p}")

    print("[..] Ingresando query ZVPF-1...")
    force_focus(hwnd)
    clear(); write("ZVPF-1")
    f8(4)
    sessions = find_sap()           # re-enumerar después de F8
    hwnd, title = sessions[0]
    rect = win32gui.GetWindowRect(hwnd)
    L, T = rect[0], rect[1]
    print(f"[OK] Post-ZVPF-1: '{title}'")

    # ── Pantalla de selección: limpiar y poner fechas ─────────────────────────
    p = capture(hwnd, "02_seleccion")
    print(f"[..] Screenshot selección: {p}")

    # Coordenadas calculadas del screenshot (1024x710, window en L,T):
    # Número de viaje: campo "desde" ≈ x+316, y+172
    # Fecha creación:  campo "desde" ≈ x+316, y+571
    # Fecha creación:  campo "hasta" ≈ x+510, y+571

    NV_X, NV_Y   = L+316, T+172   # Número de viaje (limpiar)
    FC_X1, FC_Y  = L+316, T+571   # Fecha creación desde
    FC_X2, FC_Y2 = L+510, T+571   # Fecha creación hasta

    print(f"[..] Limpiando 'Número de viaje' en ({NV_X}, {NV_Y})...")
    click(NV_X, NV_Y)
    clear()

    print(f"[..] 'Fecha creación' desde → {DATE_FROM} en ({FC_X1}, {FC_Y})")
    click(FC_X1, FC_Y)
    clear(); write(DATE_FROM)

    print(f"[..] 'Fecha creación' hasta → {DATE_TO} en ({FC_X2}, {FC_Y2})")
    click(FC_X2, FC_Y2)
    clear(); write(DATE_TO)

    p = capture(hwnd, "03_fechas_rellenas")
    print(f"[..] Screenshot fechas: {p}")

    # Ejecutar query
    print("[..] F8 — ejecutando query...")
    force_focus(hwnd)
    f8(8)

    p = capture(hwnd, "04_resultados")
    print(f"[..] Screenshot resultados: {p}")
    print("[!] Revisa el screenshot para confirmar resultados antes de exportar.")
    print(f"    Ruta: {p}")

    # ── Export ────────────────────────────────────────────────────────────────
    # SetForegroundWindow explícito + click directo sobre "Informe" en la barra de menú
    # "Informe" está a x≈42, y≈27 desde la esquina de la ventana (incluye title bar)
    print("[..] Abriendo menú Informe (click directo + SetForegroundWindow)...")
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    pyautogui.click(L+42, T+27)
    wait(1.0)

    p = capture(hwnd, "05_menu_informe")
    print(f"[..] Screenshot menú abierto: {p}")

    p = capture(hwnd, "06_dialogo_formato")
    print(f"[..] Screenshot diálogo formato: {p}")

    # Confirmar formato
    enter(2)

    # Diálogo Guardar como
    p = capture(hwnd, "07_guardar_como")
    print(f"[..] Screenshot guardar: {p}")
    pyautogui.hotkey('ctrl','a'); wait(0.2)
    pyautogui.typewrite(OUTPUT_FILE, interval=0.04); wait(0.5)
    enter(3)
    enter(1)  # sobreescritura

    # ── Verificar ─────────────────────────────────────────────────────────────
    if os.path.exists(OUTPUT_FILE):
        sz = os.path.getsize(OUTPUT_FILE)
        print(f"\n[OK] Guardado: {OUTPUT_FILE} ({sz:,} bytes)")
    else:
        print(f"\n[WARN] Archivo no encontrado: {OUTPUT_FILE}")

    p = capture(hwnd, "08_final")
    print(f"[..] Screenshot final: {p}")
    print("=== Completado ===")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado por usuario.")
    except Exception:
        traceback.print_exc()
