"""
capture.py — capture d'écran rapide via mss.

mss est nettement plus rapide que pyautogui.screenshot() pour un usage en
boucle serrée (30-60+ captures/sec sans peser sur le CPU). Chaque région
capturée (une icône d'ability, une barre de debuff, etc.) est définie une
fois dans le YAML de config et réutilisée à chaque tick.
"""
from __future__ import annotations

import numpy as np

try:
    import mss
except ImportError:  # permet d'importer le module pour les tests même sans mss installé
    mss = None


class ScreenCapture:
    def __init__(self):
        if mss is None:
            raise ImportError(
                "mss n'est pas installé. Sur ta machine : pip install mss"
            )
        self._sct = mss.mss()

    def grab(self, region: dict) -> np.ndarray:
        """
        region = {"left": x, "top": y, "width": w, "height": h}
        Retourne un array BGR (compatible OpenCV, alpha déjà retiré).
        """
        shot = self._sct.grab(region)
        img = np.array(shot)  # BGRA
        return img[:, :, :3]

    def close(self):
        self._sct.close()
