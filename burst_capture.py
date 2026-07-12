"""
burst_capture.py — capture une RAFALE de screenshots d'une région sur une
courte durée, pour les états transitoires (un cast de quelques secondes, un
proc qui clignote) où une seule capture au bon instant est trop risquée à
timer à la main -- et où le round-trip de la conversation est trop lent
pour que Claude la déclenche lui-même au bon moment.

Lance la commande PILE avant de déclencher l'action en jeu (le cast, le
proc...) -- la rafale démarre immédiatement et dure `duration` secondes,
une image toutes les `interval` secondes.

USAGE :
    python burst_capture.py <left> <top> <width> <height> [duration=4] [interval=0.15]

Sauvegarde dans burst/<timestamp>/000.png, 001.png, ... -- partage le
dossier (ou colle le chemin) et Claude peut lire toute la séquence pour
choisir la couleur/le seuil/le bon instant, sans que tu aies besoin de
viser un instant précis toi-même.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from wardenbot.capture import ScreenCapture

try:
    import cv2
except ImportError:
    cv2 = None


def main():
    args = sys.argv[1:]
    if len(args) < 4:
        print(__doc__)
        return
    if cv2 is None:
        print("opencv-python n'est pas installé -- pip install -r requirements.txt")
        return

    left, top, width, height = (int(v) for v in args[:4])
    duration = float(args[4]) if len(args) > 4 else 4.0
    interval = float(args[5]) if len(args) > 5 else 0.15

    region = {"left": left, "top": top, "width": width, "height": height}
    out_dir = Path("burst") / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    capturer = ScreenCapture()
    print(f"Capture de {region} pendant {duration}s (1 image / {interval}s) -> {out_dir}")
    print("Déclenche l'action en jeu MAINTENANT.")

    start = time.monotonic()
    i = 0
    while time.monotonic() - start < duration:
        frame = capturer.grab(region)
        cv2.imwrite(str(out_dir / f"{i:03d}.png"), frame)
        i += 1
        time.sleep(interval)

    print(f"Terminé -- {i} images dans {out_dir}")


if __name__ == "__main__":
    main()
