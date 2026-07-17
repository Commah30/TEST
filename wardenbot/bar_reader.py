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
    return _scan_matches(matches, direction, max_gap)


def read_fill_pct_colorless(
    frame: np.ndarray,
    saturation_threshold: int = 50,
    luminance_threshold: int = 90,
    red_dominance_threshold: int = 20,
    direction: Direction = "ltr",
    max_gap: int = 4,
) -> float:
    """
    Comme read_fill_pct, mais SANS couleur cible fixe -- "rempli" = pixel
    SATURÉ (écart max(B,G,R)-min(B,G,R) >= saturation_threshold) OU LUMINEUX
    (niveau de gris >= luminance_threshold) OU à DOMINANTE ROUGE
    (R - G > red_dominance_threshold) ; "vide" = aucun des trois. Pensé pour
    une barre dont la couleur de remplissage change d'une session à l'autre
    (ex. cadre de groupe Vigour : teinte par personnage/classe, PAS fixe --
    vérifié sur 2 vidéos réelles, 2026-07-15 : orange/bleu/rouge/blanc dans
    une session, beige/orange/bleu/blanc dans une autre -- l'orange et le
    bleu ont changé de SLOT d'une session à l'autre).

    POURQUOI TROIS CONDITIONS EN OU, PAS UNE SEULE (2026-07-15, testé contre
    les 2 vidéos réelles avant de trancher, jamais en théorie seule) :
    - Saturation seule : rate le blanc/crème (saturation nulle PAR
      DÉFINITION -- indiscernable du gris de fond).
    - Luminance seule : rate le rouge sombre (luminance réelle mesurée ~33,
      SOUS le seuil "vide" -- une teinte de classe peut être sombre ET
      saturée en même temps, la luminance seule ne le voit pas comme
      "rempli").
    - R - G > 20 (2026-07-15, ajouté après coup) : débloque justement ce
      rouge sombre -- même très sombre, son canal Rouge domine nettement le
      Vert (mesuré : rempli R-G≈50, vide/fond gris R-G≈2-4, marge large).
      AJOUTÉ EN OU, jamais en remplacement des deux autres : sur du bleu/
      blanc/orange, R-G est nul ou négatif, cette condition ne se déclenche
      jamais et ne change donc rien à un cas déjà couvert -- vérifié sur les
      2 vidéos avant/après ajout, AUCUNE régression sur les 3 autres slots.
      Root cause débloquée : party_3 (rouge très sombre) passe de "bloqué au
      plancher icône sur toute la vidéo" à une lecture qui suit le vrai %
      (93/100/75/66% mesurés, à quelques points près).

    direction="ltr" AVEC tolérance de gap (max_gap), PAS un simple "premier
    pixel trouvé en scannant depuis un bord" : testé les deux (2026-07-15) --
    scanner depuis la droite sans tolérance de gap est dangereux
    spécifiquement pour un healer : une décoration lumineuse ponctuelle au
    bord droit d'un cadre (halo de bordure, icône adjacente) fait lire un
    joueur MORT (0% réel) comme quasi plein (95%+ mesuré) -- l'inverse exact
    de ce qu'un triage de soin doit détecter. Le scan gauche->droite avec
    max_gap absorbe ce genre de pixel isolé sans se faire tromper par lui.

    EN: Like read_fill_pct, but WITHOUT a fixed target color -- "filled" = a
    EN: SATURATED pixel (max(B,G,R)-min(B,G,R) >= saturation_threshold) OR a
    EN: BRIGHT one (grayscale >= luminance_threshold) OR one with a RED-
    EN: DOMINANT channel (R - G > red_dominance_threshold); "empty" = none of
    EN: the three. Meant for a bar whose fill color changes from session to
    EN: session (e.g. Vigour's party frame: tinted per character/class, NOT
    EN: fixed -- verified on 2 real videos, 2026-07-15: orange/blue/red/white
    EN: in one session, tan/orange/blue/white in another -- orange and blue
    EN: swapped SLOTS between sessions).

    EN: WHY THREE OR'D CONDITIONS, NOT JUST ONE (2026-07-15, tested against
    EN: both real videos before deciding, never in theory alone):
    EN: - Saturation alone: misses white/cream (zero saturation BY
    EN:   DEFINITION -- indistinguishable from the grey background).
    EN: - Luminance alone: misses dark red (real measured luminance ~33,
    EN:   BELOW the "empty" threshold -- a class tint can be dark AND
    EN:   saturated at the same time, luminance alone doesn't see it as
    EN:   "filled").
    EN: - R - G > 20 (2026-07-15, added afterward): unblocks exactly that
    EN:   dark red -- even very dark, its Red channel clearly dominates Green
    EN:   (measured: filled R-G≈50, empty/grey background R-G≈2-4, wide
    EN:   margin). ADDED AS AN OR, never replacing the other two: on blue/
    EN:   white/orange, R-G is zero or negative, so this condition never
    EN:   fires and changes nothing for an already-covered case -- verified
    EN:   on both videos before/after adding it, NO regression on the other 3
    EN:   slots. Root cause unblocked: party_3 (very dark red) went from
    EN:   "stuck at the icon floor for the entire video" to a reading that
    EN:   tracks the real % (93/100/75/66% measured, within a few points).

    EN: direction="ltr" WITH gap tolerance (max_gap), NOT a naive "first
    EN: pixel found scanning from one edge": tested both (2026-07-15) --
    EN: scanning from the right without gap tolerance is dangerous
    EN: specifically for a healer: a single bright decoration at a frame's
    EN: right edge (border glow, adjacent icon) makes a DEAD player (0% real)
    EN: read as near-full (95%+ measured) -- the exact opposite of what heal
    EN: triage needs to detect. The left->right scan with max_gap absorbs
    EN: that kind of isolated stray pixel instead of being fooled by it.
    """
    row = frame[frame.shape[0] // 2, :, :3].astype(np.int16)
    b, g, r = row[:, 0], row[:, 1], row[:, 2]
    saturation = row.max(axis=1) - row.min(axis=1)
    gray = 0.114 * b + 0.587 * g + 0.299 * r   # BGR -> luminance, mêmes poids que cv2.cvtColor(...BGR2GRAY) · EN: BGR -> luminance, same weights as cv2.cvtColor(...BGR2GRAY)
    red_dominant = (r - g) > red_dominance_threshold
    matches = (saturation >= saturation_threshold) | (gray >= luminance_threshold) | red_dominant
    return _scan_matches(matches, direction, max_gap)


def _scan_matches(matches: np.ndarray, direction: Direction, max_gap: int) -> float:
    """Logique de scan partagée entre read_fill_pct et
    read_fill_pct_by_saturation -- les deux ne diffèrent que dans LA FAÇON
    de décider si un pixel compte comme "rempli", pas dans comment on
    parcourt la ligne pour en déduire un pourcentage.

    EN: Scan logic shared between read_fill_pct and
    EN: read_fill_pct_by_saturation -- the two only differ in HOW they decide
    EN: whether a pixel counts as "filled", not in how the line is walked to
    EN: derive a percentage.
    """
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
