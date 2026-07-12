"""
yolo_check.py — affiche en direct ce que le modèle YOLO entraîné détecte sur
tariq_buff_bar, SANS presser aucune touche. Même rôle que detect_check.py
(template matching) et ocr_check.py (EasyOCR), pour le détecteur YOLO --
à lancer avant de brancher yolo_detect.detect() dans
make_tariq_state_update_fn (main.py), pour valider en conditions réelles.

USAGE :
    python yolo_check.py

Positionne-toi en jeu et fais apparaître/disparaître chaque buff (Hammer
Storm, Skull Crusher, Focused Wrath) -- vérifie que le bon nom sort avec une
confiance stable, et qu'aucun buff n'apparaît quand rien n'est actif.
Ctrl+C pour arrêter.
"""
from __future__ import annotations

import time

try:
    import cv2
except ImportError:
    cv2 = None

from wardenbot.capture import ScreenCapture
from wardenbot.yolo_detect import detect
from main import REGIONS

# Seuil très bas pour le diagnostic -- si le vrai buff est actif mais que
# rien ne sort au seuil normal (0.4, cf. yolo_detect.detect), ça permet de
# voir si le modèle est "presque bon" (score juste en dessous, signe d'un
# décalage d'échelle/qualité entre le clip d'entraînement et la capture
# live) ou complètement à côté (score quasi nul, signe d'un problème de
# région/format plutôt que de seuil).
DEBUG_CONF = 0.05
DEBUG_FRAME_PATH = "yolo_check_debug.png"


def main():
    region = REGIONS["tariq"]["tariq_buff_bar"]
    capturer = ScreenCapture()

    print(f"Région capturée : {region}")
    print(f"Frame courante sauvegardée en continu dans {DEBUG_FRAME_PATH} (à partager si besoin).")
    print("Ctrl+C pour arrêter.\n")
    try:
        while True:
            frame = capturer.grab(region)
            if cv2 is not None:
                cv2.imwrite(DEBUG_FRAME_PATH, frame)

            found = detect(frame, conf=DEBUG_CONF)
            if found:
                parts = [
                    f"{name}: {conf:.2f}{'' if conf >= 0.4 else ' (sous le seuil 0.4)'}"
                    for name, conf in found.items()
                ]
                text = " | ".join(parts)
            else:
                text = f"(rien détecté, même sous {DEBUG_CONF}) -- shape={frame.shape}"
            print(f"\r{text}                                        ", end="", flush=True)
            time.sleep(0.15)
    except KeyboardInterrupt:
        print("\nArrêté.")


if __name__ == "__main__":
    main()
