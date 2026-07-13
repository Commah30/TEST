"""
test_region_selector.py — prouve que la logique PURE de l'overlay de
sélection (normalisation des points cliqués, bornes multi-écrans) est
correcte, sans jamais déclencher la vraie boucle interactive.

IMPORTANT : select_region_interactively() bloque sur root.mainloop() jusqu'à
un VRAI clic-glisser souris -- volontairement PAS appelée ici, ça figerait
la suite de tests indéfiniment sans un humain pour interagir. Seul un test
de construction/destruction (sans mainloop()) vérifie que l'API tkinter
utilisée ne lève pas d'erreur basique.

Le vrai geste de clic-glisser doit être essayé manuellement (cf. rapport).

Lance avec : python test_region_selector.py
"""
from __future__ import annotations

from wardenbot.environment_detect import MonitorInfo
from wardenbot.region_selector import _points_to_region, _virtual_screen_bounds


def test_points_to_region_top_left_to_bottom_right():
    region = _points_to_region(100, 200, 150, 250)
    assert region == {"left": 100, "top": 200, "width": 50, "height": 50}
    print(f"Glisser haut-gauche -> bas-droite : {region}")


def test_points_to_region_bottom_right_to_top_left():
    """L'utilisateur peut glisser dans n'importe quelle direction -- même
    résultat final que le sens 'normal'."""
    region = _points_to_region(150, 250, 100, 200)
    assert region == {"left": 100, "top": 200, "width": 50, "height": 50}
    print(f"Glisser bas-droite -> haut-gauche : {region} (identique, normalisé)")


def test_points_to_region_top_right_to_bottom_left():
    region = _points_to_region(150, 200, 100, 250)
    assert region == {"left": 100, "top": 200, "width": 50, "height": 50}
    print(f"Glisser haut-droite -> bas-gauche : {region} (identique, normalisé)")


def test_points_to_region_zero_size_when_no_drag():
    """Un simple clic sans glisser (mêmes coordonnées) -- région de taille
    nulle, pas une erreur. L'assistant (Phase 4d) décidera de rejeter une
    région à 0x0, pas ce module."""
    region = _points_to_region(500, 500, 500, 500)
    assert region == {"left": 500, "top": 500, "width": 0, "height": 0}
    print(f"Clic sans glisser : {region} (taille nulle, pas une erreur ici)")


def test_virtual_screen_bounds_single_monitor():
    monitors = [MonitorInfo(left=0, top=0, width=2560, height=1440, is_primary=True)]
    bounds = _virtual_screen_bounds(monitors)
    assert bounds == (0, 0, 2560, 1440)
    print(f"Un seul écran : bornes = {bounds}")


def test_virtual_screen_bounds_multi_monitor_with_negative_offset():
    """Un 2e écran placé À GAUCHE du principal a des coordonnées left
    négatives (convention Windows normale) -- les bornes virtuelles doivent
    englober les deux, pas juste repartir de 0."""
    monitors = [
        MonitorInfo(left=0, top=0, width=2560, height=1440, is_primary=True),
        MonitorInfo(left=-1920, top=0, width=1920, height=1080, is_primary=False),
    ]
    bounds = _virtual_screen_bounds(monitors)
    assert bounds == (-1920, 0, 2560 - (-1920), 1440)
    print(f"Deux écrans (2e à gauche, offset négatif) : bornes = {bounds}")


def test_virtual_screen_bounds_falls_back_when_no_monitor_detected():
    """Repli prudent si list_monitors() ne trouve rien (ne devrait jamais
    arriver en pratique, Phase 4a garantit >=1 écran) -- ne doit pas lever."""
    bounds = _virtual_screen_bounds([])
    assert bounds == (0, 0, 1920, 1080)
    print(f"Aucun écran détecté (cas dégénéré) : repli = {bounds}")


def test_tkinter_overlay_window_constructs_and_destroys_without_error():
    """Ne teste PAS l'interaction utilisateur (nécessite un vrai clic) --
    vérifie juste que les appels tkinter utilisés par select_region_interactively()
    (transparence, topmost, sans bordure, canvas) fonctionnent sur cette
    machine, avant de tout détruire immédiatement -- jamais root.mainloop()."""
    import tkinter as tk

    root = tk.Tk()
    try:
        root.attributes("-alpha", 0.25)
        root.attributes("-topmost", True)
        root.overrideredirect(True)
        root.geometry("100x100+0+0")
        root.configure(bg="black")
        canvas = tk.Canvas(root, cursor="cross", bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_rectangle(0, 0, 10, 10, outline="#00ff00", width=2)
    finally:
        root.destroy()
    print("Fenêtre overlay tkinter : construction/destruction sans erreur (interaction NON testée).")


if __name__ == "__main__":
    test_points_to_region_top_left_to_bottom_right()
    test_points_to_region_bottom_right_to_top_left()
    test_points_to_region_top_right_to_bottom_left()
    test_points_to_region_zero_size_when_no_drag()
    test_virtual_screen_bounds_single_monitor()
    test_virtual_screen_bounds_multi_monitor_with_negative_offset()
    test_virtual_screen_bounds_falls_back_when_no_monitor_detected()
    test_tkinter_overlay_window_constructs_and_destroys_without_error()
    print("\nTous les tests passent.")
