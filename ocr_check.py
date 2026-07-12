"""
ocr_check.py — affiche en direct ce qu'EasyOCR lit dans une région
d'écran donnée, SANS presser aucune touche. Sert à valider une zone
(compteur de mana, nombre de stacks...) avant de la brancher dans un
state_update_fn, exactement comme detect_check.py pour le template
matching.

Pas besoin d'une entrée dans REGIONS pour l'instant -- donne les
coordonnées brutes directement (clic droit sur une capture d'écran, ou
`python capture_helper.py mark` pour les lire en jeu).

USAGE :
    python ocr_check.py <left> <top> <width> <height> [--text]

    Par défaut lit des chiffres (read_digits). Passe --text pour lire du
    texte libre (read_text) -- utile pour vérifier une zone avant de savoir
    si elle contient un nombre ou un nom de sort.

Positionne-toi en jeu sur la valeur à lire et vérifie que ce qui s'affiche
en console correspond. Ctrl+C pour arrêter.
"""
from __future__ import annotations

import sys
import time

from wardenbot.capture import ScreenCapture
from wardenbot.ocr import read_digits, read_text


def main():
    args = sys.argv[1:]
    mode = "digits"
    if args and args[-1] == "--text":
        mode = "text"
        args = args[:-1]

    if len(args) != 4:
        print(__doc__)
        return

    left, top, width, height = (int(v) for v in args)
    region = {"left": left, "top": top, "width": width, "height": height}
    reader = read_text if mode == "text" else read_digits

    capturer = ScreenCapture()
    print(f"Lecture ({mode}) sur {region} -- Ctrl+C pour arrêter.\n")
    try:
        while True:
            frame = capturer.grab(region)
            value = reader(frame)
            print(f"\rlu: {value!r}          ", end="", flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nArrêté.")


if __name__ == "__main__":
    main()
