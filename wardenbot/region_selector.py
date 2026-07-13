"""
region_selector.py — overlay graphique plein écran pour sélectionner une
région rectangulaire à la souris (Phase 4c de l'industrialisation,
2026-07-14).

Remplace le flux manuel "positionne la souris, appuie sur F8 deux fois" de
capture_helper.py par un vrai rectangle visible qu'on dessine à la souris --
même résultat final ({left, top, width, height}), UX très différente.
tkinter (stdlib) : zéro dépendance supplémentaire, cohérent avec l'objectif
"l'utilisateur n'installe rien" (Phase 7, packaging).

Suppose que enable_dpi_awareness() (wardenbot/dpi_awareness.py) a déjà été
appelée (cf. main.py, tout en haut) -- sinon les coordonnées récupérées ici
seraient elles aussi mensongères, comme pour tout le reste du projet qui lit
l'écran.

Scope volontairement limité à LA SÉLECTION seule -- afficher le crop/le
résultat du détecteur immédiatement après (cf. spec Phase 4) est le travail
de l'assistant complet (Phase 4d), qui enchaînera select_region_interactively()
-> capture_and_analyze() (wardenbot/calibration_engine.py) -> Valider/Refaire.

IMPORTANT (limite de test) : select_region_interactively() bloque sur
root.mainloop() jusqu'à une VRAIE interaction souris -- impossible à tester
automatiquement sans figer la suite de tests indéfiniment. Le calcul de
région (_points_to_region) et les bornes multi-écrans (_virtual_screen_bounds)
sont extraits en fonctions pures, testables séparément ; la boucle Tk
elle-même doit être essayée en conditions réelles (cf. test_region_selector.py).
"""
from __future__ import annotations

import tkinter as tk
from typing import Optional

from wardenbot.environment_detect import MonitorInfo, list_monitors


def _points_to_region(x1: int, y1: int, x2: int, y2: int) -> dict:
    """Normalise deux points cliqués dans N'IMPORTE QUEL ordre (l'utilisateur
    peut glisser dans n'importe quelle direction -- bas-droite vers
    haut-gauche est tout aussi valide) vers {left, top, width, height} --
    jamais de largeur/hauteur négative."""
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _virtual_screen_bounds(monitors: "Optional[list[MonitorInfo]]" = None) -> tuple[int, int, int, int]:
    """(left, top, width, height) englobant TOUS les écrans connectés --
    l'overlay doit pouvoir couvrir un setup multi-écrans, pas juste l'écran
    principal. `monitors` injectable pour les tests (cf.
    wardenbot/dpi_awareness.py pour le même principe)."""
    if monitors is None:
        monitors = list_monitors()
    if not monitors:
        return 0, 0, 1920, 1080  # repli très prudent, ne devrait jamais arriver (Phase 4a garantit >=1 écran)
    left = min(m.left for m in monitors)
    top = min(m.top for m in monitors)
    right = max(m.left + m.width for m in monitors)
    bottom = max(m.top + m.height for m in monitors)
    return left, top, right - left, bottom - top


def select_region_interactively(
    instructions: str = "Clique-glisse pour sélectionner une région -- Échap pour annuler",
) -> Optional[dict]:
    """Ouvre un overlay semi-transparent plein écran (tous moniteurs
    confondus), laisse l'utilisateur dessiner un rectangle à la souris,
    retourne {left, top, width, height} en coordonnées écran PHYSIQUES -- ou
    None si annulé (Échap).

    BLOQUANT : lance sa propre boucle Tk (root.mainloop()), ne revient
    qu'une fois la sélection terminée ou annulée -- jamais appelé
    automatiquement dans les tests pour cette raison.
    """
    vleft, vtop, vwidth, vheight = _virtual_screen_bounds()

    root = tk.Tk()
    root.attributes("-alpha", 0.25)
    root.attributes("-topmost", True)
    root.overrideredirect(True)  # pas de barre de titre/bordure -- un vrai overlay
    root.geometry(f"{vwidth}x{vheight}+{vleft}+{vtop}")
    root.configure(bg="black")

    canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)
    canvas.create_text(vwidth // 2, 30, text=instructions, fill="white", font=("Segoe UI", 14, "bold"))

    state: dict = {"start_root": None, "rect_id": None, "result": None}

    def on_press(event):
        state["start_root"] = (event.x_root, event.y_root)
        state["rect_id"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#00ff00", width=2)

    def on_drag(event):
        if state["start_root"] is None:
            return
        x0_root, y0_root = state["start_root"]
        # event.x/y sont déjà relatifs au canvas (donc à l'overlay) ; le
        # point de départ est stocké en coordonnées ÉCRAN (x_root/y_root),
        # reconverti ici en coordonnées canvas pour rester cohérent avec
        # event.x/y pendant le glisser.
        canvas.coords(state["rect_id"], x0_root - vleft, y0_root - vtop, event.x, event.y)

    def on_release(event):
        if state["start_root"] is None:
            return
        x0_root, y0_root = state["start_root"]
        state["result"] = _points_to_region(x0_root, y0_root, event.x_root, event.y_root)
        root.destroy()

    def on_escape(_event):
        state["result"] = None
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)

    root.mainloop()
    return state["result"]
