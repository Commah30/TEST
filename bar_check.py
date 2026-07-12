"""
bar_check.py — affiche en direct le pourcentage de remplissage lu par
bar_reader.read_fill_pct() sur une région donnée, SANS presser aucune
touche. Sert à calibrer la couleur de remplissage et le sens de lecture
avant de brancher une barre dans un state_update_fn (ex. la cast bar
Hammer Storm de Tariq, ou rage_bar).

USAGE :
    python bar_check.py <left> <top> <width> <height> <b> <g> <r> [--rtl]

    <b> <g> <r> : couleur de remplissage (0-255 chacun), relevée à l'oeil
    sur une capture d'écran pendant que la barre est pleine (pipette
    Paint/GIMP/ShareX).
    --rtl : lecture depuis la droite (barre qui se vide de droite à
    gauche, ex. Hammer Storm). Par défaut : lecture depuis la gauche (ltr,
    ex. rage_bar classique).

Positionne-toi en jeu et fais varier la barre (lance un cast / dépense de
la rage) -- vérifie que le pourcentage affiché suit bien ce que tu vois à
l'écran. Ctrl+C pour arrêter.
"""
from __future__ import annotations

import sys
import time

from wardenbot.bar_reader import read_fill_pct
from wardenbot.capture import ScreenCapture


def main():
    args = sys.argv[1:]
    direction = "ltr"
    if args and args[-1] == "--rtl":
        direction = "rtl"
        args = args[:-1]

    if len(args) != 7:
        print(__doc__)
        return

    left, top, width, height, b, g, r = (int(v) for v in args)
    region = {"left": left, "top": top, "width": width, "height": height}
    fill_bgr = (b, g, r)

    capturer = ScreenCapture()
    print(f"Lecture ({direction}) sur {region}, couleur cible BGR={fill_bgr} -- Ctrl+C pour arrêter.\n")
    try:
        while True:
            frame = capturer.grab(region)
            pct = read_fill_pct(frame, fill_bgr, direction=direction)
            bar = "#" * int(pct * 30)
            print(f"\r{pct:5.1%} [{bar:<30}]     ", end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nArrêté.")


if __name__ == "__main__":
    main()
