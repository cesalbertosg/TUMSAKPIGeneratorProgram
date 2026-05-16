"""Aplicación Tkinter principal del KPI Generator."""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from kpi_generator.config import Config, LogLevel
from kpi_generator.domain.processor import DataProcessor
from kpi_generator.gui.widgets import ScrollableFrame

class KPIGeneratorGUI:
    """Interfaz gráfica optimizada para generación de reportes KPI con comodatos, cambios y OpCedula."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("KPI Generator v12.2 - OpCedula Period-Aware + Google Sheets")
        self.root.geometry("900x700")
        self.root.minsize(800, 600)
        
        self.setup_professional_theme()
        
        self.paths = {
            "trips": tk.StringVar(),
            "fuel": tk.StringVar(), 
            "cedulas": tk.StringVar(),
            "objectives": tk.StringVar(),
            "output": tk.StringVar()
        }
        
        self.processor = DataProcessor(self.log, LogLevel.INFO)
        self.setup_ui()
    
    def setup_professional_theme(self):
        """Configurar tema visual."""
        self.colors = {
            'bg_primary': '#1a1d29',
            'bg_secondary': '#252836',
            'bg_card': '#2d3142',
            'accent_primary': '#6366f1',
            'accent_secondary': '#ec4899',
            'accent_success': '#10b981',
            'accent_info': '#06b6d4',
            'text_primary': '#ffffff',
            'text_secondary': '#9ca3af',
            'border': '#374151'
        }
        
        self.root.configure(bg=self.colors['bg_primary'])
        
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('Vertical.TScrollbar',
                       background=self.colors['bg_secondary'],
                       troughcolor=self.colors['bg_card'],
                       borderwidth=0,
                       arrowcolor=self.colors['text_secondary'])
        
        style.configure('Professional.Horizontal.TProgressbar',
                       background=self.colors['accent_info'],
                       troughcolor=self.colors['bg_secondary'],
                       borderwidth=0,
                       lightcolor=self.colors['accent_info'],
                       darkcolor=self.colors['accent_info'])
    
    def setup_ui(self):
        """Configurar interfaz de usuario completa."""
        self.scroll_frame = ScrollableFrame(self.root)
        
        main_container = tk.Frame(self.scroll_frame.scrollable_frame, bg=self.colors['bg_primary'], padx=30, pady=25)
        main_container.pack(fill=tk.BOTH, expand=True)
        
        header_frame = tk.Frame(main_container, bg=self.colors['bg_primary'], height=80)
        header_frame.pack(fill="x", pady=(0, 25))
        header_frame.pack_propagate(False)
        
        title_label = tk.Label(header_frame,
                              text="KPI Generator v12.2",
                              bg=self.colors['bg_primary'],
                              fg=self.colors['text_primary'],
                              font=('Segoe UI', 28, 'bold'))
        title_label.pack(side="left", pady=15)
        
        subtitle_label = tk.Label(header_frame,
                                 text="Con OpCedula, Ingresos y Egresos",
                                 bg=self.colors['bg_primary'],
                                 fg=self.colors['text_secondary'],
                                 font=('Segoe UI', 12))
        subtitle_label.pack(side="left", padx=(15, 0), pady=20)
        
        files_card = self.create_card_frame(main_container, 
                                          "Configuración de Fuentes de Datos", 
                                          "Seleccione los archivos y carpetas requeridos")
        files_card.pack(fill="x", pady=(0, 15))
        
        file_configs = [
            ("Viajes", "trips", "🚚"),
            ("Combustible", "fuel", "⛽"),
            ("Cédulas", "cedulas", "📅"),
            ("Objetivos", "objectives", "🎯"),
            ("Directorio Salida", "output", "💾")
        ]
        
        for label, key, icon in file_configs:
            self.create_file_row(files_card, label, key, icon)
        
        log_card = self.create_card_frame(main_container, 
                                        "Monitor del Sistema", 
                                        "Seguimiento con códigos simplificados")
        log_card.pack(fill="x", pady=(0, 15))
        
        log_control_frame = tk.Frame(log_card, bg=self.colors['bg_card'])
        log_control_frame.pack(fill="x", padx=5, pady=(0, 10))
        
        tk.Label(log_control_frame, text="Nivel de Log:", 
                bg=self.colors['bg_card'], fg=self.colors['text_primary'],
                font=('Segoe UI', 10)).pack(side="left")
        
        self.log_level_var = tk.StringVar(value="INFO")
        log_combo = ttk.Combobox(log_control_frame, textvariable=self.log_level_var,
                               values=["ERROR", "INFO", "DEBUG"], state="readonly", width=10)
        log_combo.pack(side="left", padx=(10, 0))
        log_combo.bind("<<ComboboxSelected>>", self.change_log_level)
        
        log_container = tk.Frame(log_card, bg=self.colors['bg_secondary'])
        log_container.pack(fill="x", padx=5, pady=5)
        
        text_frame = tk.Frame(log_container, bg=self.colors['bg_secondary'])
        text_frame.pack(fill="x", padx=10, pady=10)
        
        self.log_text = tk.Text(text_frame,
                               bg=self.colors['bg_secondary'],
                               fg=self.colors['text_primary'],
                               font=('Consolas', 9),
                               border=0,
                               wrap=tk.WORD,
                               height=12,
                               insertbackground=self.colors['accent_info'])
        
        log_scrollbar = tk.Scrollbar(text_frame, 
                                   command=self.log_text.yview,
                                   bg=self.colors['bg_secondary'],
                                   troughcolor=self.colors['bg_card'],
                                   activebackground=self.colors['accent_primary'])
        
        self.log_text.configure(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scrollbar.pack(side="right", fill="y")
        
        self.setup_control_panel()
        
        self.log("Sistema KPI Generator v11 iniciado")
    
    def change_log_level(self, event=None):
        """Cambiar nivel de logging dinámicamente."""
        level_map = {"ERROR": LogLevel.ERROR, "INFO": LogLevel.INFO, "DEBUG": LogLevel.DEBUG}
        self.processor.log_level = level_map[self.log_level_var.get()]
        self.log(f"[CFG] Nivel de log: {self.log_level_var.get()}")
    
    def create_card_frame(self, parent, title, subtitle=""):
        """Crear componente de tarjeta profesional."""
        card_frame = tk.Frame(parent, bg=self.colors['bg_card'], pady=15, padx=20)
        
        header_frame = tk.Frame(card_frame, bg=self.colors['bg_card'])
        header_frame.pack(fill="x", pady=(0, 15))
        
        title_label = tk.Label(header_frame, 
                              text=title,
                              bg=self.colors['bg_card'],
                              fg=self.colors['text_primary'],
                              font=('Segoe UI', 14, 'bold'))
        title_label.pack(anchor="w")
        
        if subtitle:
            subtitle_label = tk.Label(header_frame,
                                    text=subtitle,
                                    bg=self.colors['bg_card'],
                                    fg=self.colors['text_secondary'],
                                    font=('Segoe UI', 9))
            subtitle_label.pack(anchor="w")
        
        return card_frame
    
    def create_file_row(self, parent, label_text, key, icon="📁"):
        """Crear fila de selección de archivo."""
        row_frame = tk.Frame(parent, bg=self.colors['bg_card'], pady=8)
        row_frame.pack(fill="x", pady=3)
        
        label_frame = tk.Frame(row_frame, bg=self.colors['bg_card'])
        label_frame.pack(side="left", padx=(0, 15))
        
        icon_label = tk.Label(label_frame, 
                             text=icon,
                             bg=self.colors['bg_card'],
                             font=('Segoe UI', 12))
        icon_label.pack(side="left", padx=(0, 8))
        
        text_label = tk.Label(label_frame,
                             text=label_text,
                             bg=self.colors['bg_card'],
                             fg=self.colors['text_primary'],
                             font=('Segoe UI', 10),
                             width=12,
                             anchor="w")
        text_label.pack(side="left")
        
        entry_frame = tk.Frame(row_frame, bg=self.colors['bg_secondary'], height=35)
        entry_frame.pack(side="left", fill="x", expand=True, padx=(0, 10))
        entry_frame.pack_propagate(False)
        
        entry = tk.Entry(entry_frame,
                        textvariable=self.paths[key],
                        bg=self.colors['bg_secondary'],
                        fg=self.colors['text_primary'],
                        border=0,
                        font=('Segoe UI', 10),
                        insertbackground=self.colors['text_primary'])
        entry.pack(fill="both", padx=12, pady=8)
        
        if key in ["output", "cedulas"]:
            cmd = self.select_folder
        else:
            cmd = lambda k=key: self.select_file(k)
        
        btn_frame = tk.Frame(row_frame, bg=self.colors['accent_primary'], height=35, width=80)
        btn_frame.pack(side="right")
        btn_frame.pack_propagate(False)
        
        btn = tk.Button(btn_frame,
                       text="Buscar",
                       command=cmd,
                       bg=self.colors['accent_primary'],
                       fg='white',
                       border=0,
                       relief='flat',
                       font=('Segoe UI', 9, 'bold'),
                       cursor='hand2',
                       activebackground=self.colors['accent_secondary'])
        btn.pack(fill="both", expand=True)
        
        return row_frame
    
    def setup_control_panel(self):
        """Configurar panel de control optimizado."""
        self.controls_frame = tk.Frame(self.root, bg=self.colors['bg_card'], pady=15)
        self.controls_frame.pack(side="bottom", fill="x")
        
        progress_container = tk.Frame(self.controls_frame, bg=self.colors['bg_card'])
        progress_container.pack(fill="x", padx=50, pady=(0, 15))
        
        self.progress = ttk.Progressbar(progress_container,
                                      mode='indeterminate',
                                      length=500,
                                      style='Professional.Horizontal.TProgressbar')
        self.progress.pack()
        
        buttons_frame = tk.Frame(self.controls_frame, bg=self.colors['bg_card'])
        buttons_frame.pack()
        
        self.process_btn = tk.Button(buttons_frame,
                                   text="🚀 EJECUTAR ANÁLISIS",
                                   command=self.start_processing,
                                   bg=self.colors['accent_success'],
                                   fg='white',
                                   font=('Segoe UI', 11, 'bold'),
                                   border=0,
                                   relief='flat',
                                   padx=25,
                                   pady=10,
                                   cursor='hand2',
                                   activebackground='#059669')
        self.process_btn.pack(side="left", padx=5)
        
        self.clear_cache_btn = tk.Button(buttons_frame,
                                       text="🗑️ LIMPIAR CACHE",
                                       command=self.clear_cache,
                                       bg=self.colors['accent_info'],
                                       fg='white',
                                       font=('Segoe UI', 11, 'bold'),
                                       border=0,
                                       relief='flat',
                                       padx=25,
                                       pady=10,
                                       cursor='hand2',
                                       activebackground='#0891b2')
        self.clear_cache_btn.pack(side="left", padx=5)
        
        self.clear_btn = tk.Button(buttons_frame,
                                 text="🔄 RESETEAR",
                                 command=self.clear_all,
                                 bg=self.colors['accent_info'],
                                 fg='white',
                                 font=('Segoe UI', 11, 'bold'),
                                 border=0,
                                 relief='flat',
                                 padx=25,
                                 pady=10,
                                 cursor='hand2',
                                 activebackground='#0891b2')
        self.clear_btn.pack(side="left", padx=5)
        
        self.close_btn = tk.Button(buttons_frame,
                                 text="❌ SALIR",
                                 command=self.close_application,
                                 bg=self.colors['accent_secondary'],
                                 fg='white',
                                 font=('Segoe UI', 11, 'bold'),
                                   border=0,
                                 relief='flat',
                                 padx=25,
                                 pady=10,
                                 cursor='hand2',
                                 activebackground='#dc2626')
        self.close_btn.pack(side="left", padx=5)
    
    def clear_cache(self):
        """Limpiar cache del procesador."""
        self.processor._get_operacion_cedula.cache_clear()
        self.processor._parse_cedula_filename.cache_clear()
        self.processor._get_daily_objective.cache_clear()
        self.processor._objective_cache.clear()
        self.processor._cedula_cache.clear()
        self.log("[CACHE] Cache limpiado")
    
    def select_file(self, key: str):
        """Seleccionar archivo de entrada."""
        filename = filedialog.askopenfilename(
            title=f"Seleccionar archivo de {key}",
            filetypes=[("Excel", "*.xlsx *.xls")]
        )
        if filename:
            self.paths[key].set(filename)
            self.log(f"[FILE] {key}: {Path(filename).name}")
    
    def select_folder(self):
        """Seleccionar directorio o carpeta."""
        folder = filedialog.askdirectory(title="Seleccionar directorio")
        if folder:
            if not self.paths["cedulas"].get():
                self.paths["cedulas"].set(folder)
                self.log(f"[FOLDER] Cédulas: {folder}")
            else:
                self.paths["output"].set(folder)
                self.log(f"[FOLDER] Salida: {folder}")
    
    def clear_all(self):
        """Limpiar configuración y registro."""
        for path_var in self.paths.values():
            path_var.set("")
        
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)
        
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        
        self.clear_cache()
        self.log("[RESET] Sistema reseteado")
    
    def close_application(self):
        """Cerrar aplicación."""
        if messagebox.askokcancel("Confirmar Cierre", "¿Confirma el cierre del sistema?"):
            self.log("[EXIT] Sistema cerrado")
            self.root.destroy()
    
    def log(self, message: str):
        """Registrar evento en el monitor del sistema con códigos."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        if any(keyword in message.lower() for keyword in ["[err]", "error", "crítico"]):
            color = self.colors['accent_secondary']
            prefix = "🚨"
        elif any(keyword in message.lower() for keyword in ["[ok]", "completado", "exitoso", "generado"]):
            color = self.colors['accent_success']
            prefix = "✅"
        elif any(keyword in message.lower() for keyword in ["[proc]", "[load]", "[kpi]", "procesando"]):
            color = self.colors['accent_info']
            prefix = "⚙️"
        elif any(keyword in message.lower() for keyword in ["[com]", "comodato"]):
            color = '#f59e0b'
            prefix = "📦"
        elif any(keyword in message.lower() for keyword in ["[chg]", "cambio"]):
            color = '#8b5cf6'
            prefix = "🔄"
        elif any(keyword in message.lower() for keyword in ["[opced]", "opcedula"]):
            color = '#06b6d4'
            prefix = "📊"
        elif any(keyword in message.lower() for keyword in ["[phantom]", "fantasma"]):
            color = '#a855f7'
            prefix = "👻"
        else:
            color = self.colors['text_primary']
            prefix = "ℹ️"
        
        self.log_text.config(state=tk.NORMAL)
        
        self.log_text.insert(tk.END, f"[{timestamp}] ", 'timestamp')
        self.log_text.tag_config('timestamp', foreground=self.colors['text_secondary'])
        
        self.log_text.insert(tk.END, f"{prefix} ", 'prefix')
        self.log_text.tag_config('prefix', foreground=color)
        
        self.log_text.insert(tk.END, f"{message}\n", 'message')
        self.log_text.tag_config('message', foreground=color)
        
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update_idletasks()
    
    def validate_inputs(self) -> bool:
        """Validar configuración de entrada."""
        required_fields = ['trips', 'fuel', 'cedulas', 'output']
        
        for key in required_fields:
            if not self.paths[key].get().strip():
                messagebox.showerror("Error de Validación", f"Debe seleccionar: {key.title()}")
                return False
        
        for key in ['trips', 'fuel']:
            if not Path(self.paths[key].get()).exists():
                messagebox.showerror("Error de Archivo", f"El archivo {key} no existe")
                return False
        
        if not Path(self.paths["cedulas"].get()).is_dir():
            messagebox.showerror("Error de Carpeta", "La carpeta de cédulas no es válida")
            return False
        
        objectives_path = self.paths["objectives"].get().strip()
        if objectives_path and not Path(objectives_path).exists():
            messagebox.showerror("Error de Archivo", "El archivo de objetivos no existe")
            return False
        
        if not Path(self.paths["output"].get()).is_dir():
            messagebox.showerror("Error de Directorio", "El directorio de salida no es válido")
            return False
                
        return True
    
    def start_processing(self):
        """Iniciar proceso de análisis."""
        if not self.validate_inputs():
            return
        
        self.process_btn.config(state="disabled", text="⏳ PROCESANDO...")
        self.progress.start(10)
        
        threading.Thread(target=self.process_data, daemon=True).start()
    
    def process_data(self):
        """Ejecutar procesamiento de datos en hilo separado."""
        try:
            objectives_file = self.paths["objectives"].get().strip()
            objectives_file = objectives_file if objectives_file else None
            
            result = self.processor.generate_report(
                self.paths["trips"].get(),
                self.paths["fuel"].get(),
                self.paths["cedulas"].get(),
                self.paths["output"].get(),
                objectives_file
            )
            
            self.root.after(0, self.processing_complete, result)
            
        except Exception as e:
            self.root.after(0, self.processing_error, str(e))
    
    def processing_complete(self, result: Optional[str]):
        """Manejar finalización exitosa del procesamiento."""
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        
        if result:
            self.log("[SUCCESS] Análisis completado")
            if messagebox.askyesno("Proceso Completado", 
                                 f"Reporte generado:\n{Path(result).name}\n\n¿Desea abrir el archivo?"):
                self.open_file(result)
        else:
            messagebox.showerror("Error de Procesamiento", "Error durante el análisis")
    
    def processing_error(self, error: str):
        """Manejar errores durante el procesamiento."""
        self.progress.stop()
        self.process_btn.config(state="normal", text="🚀 EJECUTAR ANÁLISIS")
        self.log(f"[ERR] Error: {error}")
        messagebox.showerror("Error del Sistema", f"Error crítico: {error}")
    
    def open_file(self, file_path: str):
        """Abrir archivo generado en el sistema."""
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)
            elif system == "Darwin":
                subprocess.run(["open", file_path])
            else:
                subprocess.run(["xdg-open", file_path])
        except Exception as e:
            self.log(f"[ERR] Error abriendo archivo: {e}")
    
    def run(self):
        """Iniciar aplicación."""
        self.log("[START] KPI Generator v11 iniciado")
        self.root.mainloop()
