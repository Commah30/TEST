"""
matcher.py — template matching via OpenCV.

Principe classique de détection par template : chaque ability/condition a une image de référence
(PNG) capturée une fois manuellement, puis comparée en continu à la région
d'écran correspondante. cv2.matchTemplate + TM_CCOEFF_NORMED renvoie un score
de confiance 0.0-1.0 ; on considère "ready"/"présent" au-dessus d'un seuil
(0.70 par défaut, ajustable par template — c'est exactement le
detection.buff_threshold dont tu parlais).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # permet d'importer le module pour les tests même sans opencv installé
    cv2 = None


class TemplateMatcher:
    def __init__(self, template_dir: str | Path):
        self.template_dir = Path(template_dir)
        self._cache: dict[str, np.ndarray] = {}

    def _load(self, name: str) -> np.ndarray:
        if name not in self._cache:
            if cv2 is None:
                raise ImportError("opencv-python n'est pas installé. Sur ta machine : pip install opencv-python")
            path = self.template_dir / f"{name}.png"
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"Template introuvable : {path}")
            self._cache[name] = img
        return self._cache[name]

    def confidence(self, screen_region: np.ndarray, template_name: str) -> float:
        """Retourne la confiance de match (0.0-1.0)."""
        template = self._load(template_name)
        if screen_region.shape[0] < template.shape[0] or screen_region.shape[1] < template.shape[1]:
            return 0.0
        result = cv2.matchTemplate(screen_region, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return float(max_val)

    def is_match(self, screen_region: np.ndarray, template_name: str, threshold: float = 0.70) -> bool:
        return self.confidence(screen_region, template_name) >= threshold
