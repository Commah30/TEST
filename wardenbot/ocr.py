"""
ocr.py — lecture de texte/chiffres à l'écran via EasyOCR (torch, GPU).

Remplace Tesseract (pytesseract) : PaddleOCR a été écarté car paddlepaddle
ne publie aucune wheel pour Python 3.14 (version installée sur cette
machine) -- confirmé via PyPI, dernière release 3.3.1 : wheels jusqu'à
cp313 seulement. EasyOCR tourne sur torch (déjà installé en variante CUDA
pour ce projet, cf. requirements.txt) et exploite le GPU (RTX 3060 présent
sur cette machine, cf. torch.cuda.is_available()) -- nettement plus rapide
qu'un OCR CPU pur pour ce genre d'appel répété en quasi temps réel.

Complète matcher.py (qui répond juste "est-ce que ÇA ressemble à CETTE
image ?") pour les cas où il faut la vraie valeur affichée à l'écran (un
chiffre, un texte). Toujours pure lecture d'écran (mss + EasyOCR), zéro
lecture de mémoire du jeu -- même principe que le reste de wardenbot.
PLUS utilisé dans le pipeline Tariq en prod depuis le 2026-07-12 (le
comptage de stacks Hammer Storm a été retiré, cf. wardenbot/hooks.py) --
gardé comme utilitaire générique pour un futur besoin de lecture de valeur
à l'écran, testable directement via ocr_check.py.

Le lecteur EasyOCR (`easyocr.Reader`) charge des poids de modèle au premier
appel (~qq secondes) -- instancié une seule fois en singleton paresseux,
jamais recréé par appel.

IMPORTANT si un appelant futur le rebranche dans une boucle temps réel :
l'inférence EasyOCR, même sur GPU, reste bien plus coûteuse qu'un simple
template match OpenCV -- ne JAMAIS l'appeler à la fréquence du tick engine
(~60Hz). Throttler à 5-10 appels/seconde maximum, et seulement sur des
crops déjà précis et petits (pas l'écran entier).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

try:
    import cv2
except ImportError:  # permet d'importer le module pour les tests même sans opencv installé
    cv2 = None

try:
    import easyocr
except ImportError:  # permet d'importer le module pour les tests même sans easyocr installé
    easyocr = None

_reader = None  # singleton paresseux -- cf. docstring, chargement des poids coûteux


def _get_reader():
    global _reader
    if easyocr is None:
        raise ImportError("easyocr requis pour l'OCR : pip install easyocr")
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=True)
    return _reader


def _preprocess(frame: np.ndarray, upscale: int = 3) -> np.ndarray:
    """Niveaux de gris -> upscale -> seuillage Otsu (binaire noir/blanc).
    Rend les petits chiffres d'UI nettement plus lisibles qu'un crop couleur
    brut, même pour un OCR deep-learning."""
    if cv2 is None:
        raise ImportError("opencv-python-headless requis pour l'OCR : pip install opencv-python-headless")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if upscale > 1:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def read_digits(frame: np.ndarray) -> Optional[int]:
    """Lit un nombre entier depuis la région capturée (stacks, etc.).
    Retourne None si rien de reconnaissable comme chiffre -- à traiter comme
    "valeur inconnue ce tick", pas comme 0 (0 est une vraie valeur possible)."""
    reader = _get_reader()
    processed = _preprocess(frame)
    results = reader.readtext(processed, allowlist="0123456789", detail=0)
    digits = "".join(c for c in "".join(results) if c.isdigit())
    return int(digits) if digits else None


def read_text(frame: np.ndarray) -> str:
    """Lit du texte libre (nom du sort en cours de cast, etc.) -- moins
    fiable que read_digits sur de petites zones, à réserver aux cas où le
    texte brut est vraiment nécessaire."""
    reader = _get_reader()
    processed = _preprocess(frame)
    results = reader.readtext(processed, detail=0)
    return " ".join(results).strip()
