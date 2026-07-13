"""
gui_app.py — lance l'interface graphique (Phase 6 de l'industrialisation,
2026-07-14).

Phase 6a (cette étape) : structure pywebview + rendu visuel uniquement --
données statiques (ui/index.html), boutons inertes (cf. ui/app.js). Le
bridge Python<->JS réel (profils, diagnostic via wardenbot/diagnostic_report.py,
contrôle du bot via RotationEngine) arrive en Phase 6b/6c -- pas encore ici,
volontairement, pour valider le rendu visuel d'abord (stack choisie : pywebview
+ HTML/CSS pour un vrai glassmorphism, cf. discussion 2026-07-14 -- tkinter
seul ne peut pas faire de flou d'arrière-plan réel).

pywebview utilise le WebView2 déjà installé sur Windows (pas de navigateur
embarqué à la Electron) -- cf. requirements.txt.
"""
from __future__ import annotations

from pathlib import Path

import webview

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def launch_gui() -> None:
    webview.create_window(
        "WardenBot",
        str(UI_DIR / "index.html"),
        width=1280,
        height=800,
        min_size=(1000, 650),
        background_color="#05060a",
    )
    webview.start()


if __name__ == "__main__":
    launch_gui()
