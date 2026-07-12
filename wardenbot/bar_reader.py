"""
bar_reader.py — lecture du pourcentage de remplissage d'une barre (rage,
canalisation Hammer Storm...) par scan de pixels. Ni OCR ni template : une
bande fine capturée sur toute la largeur de la barre, on scanne pixel par
pixel depuis un bord et on cherche où la couleur "remplie" s'arrête -- le
ratio (largeur remplie / largeur totale) donne le pourcentage.

direction="ltr" (par défaut) : le remplissage grandit de la gauche vers la
droite (barre de rage classique) -- on scanne depuis la gauche, le résultat
est la fraction remplie à partir du bord GAUCHE.
direction="rtl" : le remplissage décroît de la droite vers la gauche (cast
bar Hammer Storm de Tariq -- elle se vide dans ce sens à mesure que la
canalisation avance) -- on scanne depuis la DROITE, le résultat est la
fraction ENCORE remplie à partir du bord droit (~1.0 au début du cast,
~0.0 juste avant la fin).

Zéro dépendance en plus de numpy (déjà requis par le reste de wardenbot) --
contrairement à matcher.py/ocr.py, pas besoin d'OpenCV/EasyOCR ici.
"""
from __future__ import annotations

from typing import Literal

import numpy as np

Direction = Literal["ltr", "rtl"]


def read_fill_pct(
    frame: np.ndarray,
    fill_bgr: tuple[int, int, int],
    tolerance: int = 30,
    direction: Direction = "ltr",
    max_gap: int = 2,
) -> float:
    """
    frame : capture BGR de la barre (ScreenCapture.grab), idéalement une
    bande fine sur toute sa largeur (quelques pixels de haut suffisent) --
    choisie pour éviter un éventuel texte/label qui traverserait la barre
    (ex. le nom du sort centré verticalement) : préfère une bande proche du
    bord haut ou bas de la barre plutôt que sa ligne centrale.
    fill_bgr : couleur (B, G, R) du remplissage, relevée PAS au bord (les
    bordures de barre ont souvent 1-2px de biseau/glow qui s'éloignent de la
    couleur pure) mais depuis une capture où le rempli occupe presque toute
    la largeur (donne une zone large où échantillonner loin des bords).
    tolerance : écart max accepté par canal pour qu'un pixel compte comme
    "rempli" (tolère l'anti-aliasing/variations de luminosité mineures).
    direction : "ltr" ou "rtl", cf. docstring du module.
    max_gap : nombre de pixels non-matchés consécutifs tolérés avant de
    considérer que le rempli s'arrête vraiment là -- absorbe le biseau
    anti-aliasé d'1-2px typique des bordures de barre (vérifié sur un vrai
    clip Hammer Storm : le tout premier pixel du bord rate le seuil de
    couleur à lui seul, sans max_gap toute la lecture retombait à 0%).

    Retourne un float 0.0-1.0.
    """
    row = frame[frame.shape[0] // 2, :, :3].astype(np.int16)
    target = np.array(fill_bgr, dtype=np.int16)
    matches = np.all(np.abs(row - target) <= tolerance, axis=1)

    width = len(matches)
    if width == 0:
        return 0.0

    if direction == "rtl":
        matches = matches[::-1]

    filled = 0
    gap = 0
    for i, is_match in enumerate(matches):
        if is_match:
            gap = 0
            filled = i + 1
        else:
            gap += 1
            if gap > max_gap:
                break

    return filled / width
