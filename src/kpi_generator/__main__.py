"""Entry point por defecto: lanza la GUI Tkinter.

Uso:
    python -m kpi_generator
    kpi-gui                  # si se instaló con `pip install -e .`
"""

from __future__ import annotations


def main() -> None:
    from kpi_generator.gui.app import KPIGeneratorGUI

    app = KPIGeneratorGUI()
    app.run()


if __name__ == "__main__":
    main()
