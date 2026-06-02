"""Paletas de color para la GUI Tkinter del KPI Generator.

Cada tema es un dict `{role: hex_color}` con el mismo schema. Para anadir
una paleta nueva, copiar DARK_THEME y reemplazar valores; despues
registrarla en `THEMES` y referenciarla via `KPI_GUI_THEME` en `.env`.

Schema:
- bg_primary       Fondo principal de la ventana.
- bg_secondary     Fondo de areas de scroll, entries.
- bg_card          Fondo de tarjetas (grupos de controles).
- accent_primary   Color principal de acentos (botones primarios).
- accent_secondary Color secundario (badges, hover).
- accent_success   Verde de exito (procesamiento OK).
- accent_info      Cian informativo (logs neutros).
- text_primary     Texto principal (alto contraste sobre bg_primary).
- text_secondary   Texto auxiliar (medio contraste).
- border           Borde sutil entre secciones.
"""

from __future__ import annotations

from typing import Dict


DARK_THEME: Dict[str, str] = {
    'bg_primary': '#1a1d29',
    'bg_secondary': '#252836',
    'bg_card': '#2d3142',
    'accent_primary': '#6366f1',
    'accent_secondary': '#ec4899',
    'accent_success': '#10b981',
    'accent_info': '#06b6d4',
    'text_primary': '#ffffff',
    'text_secondary': '#9ca3af',
    'border': '#374151',
}

# Paleta clara — placeholder para soporte futuro. Los valores son una
# aproximacion razonable; ajustar cuando se decida la paleta corporativa.
LIGHT_THEME: Dict[str, str] = {
    'bg_primary': '#f5f5f7',
    'bg_secondary': '#ffffff',
    'bg_card': '#fafafa',
    'accent_primary': '#4f46e5',
    'accent_secondary': '#db2777',
    'accent_success': '#059669',
    'accent_info': '#0891b2',
    'text_primary': '#1a1d29',
    'text_secondary': '#4b5563',
    'border': '#d1d5db',
}

THEMES: Dict[str, Dict[str, str]] = {
    'dark': DARK_THEME,
    'light': LIGHT_THEME,
}


def get_theme(name: str = 'dark') -> Dict[str, str]:
    """Devuelve el dict de colores de la paleta solicitada.

    Si `name` no existe, cae a DARK_THEME sin fallar (la GUI siempre arranca).
    """
    return THEMES.get(name.lower(), DARK_THEME)
