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
    brut, même pour un OCR deep-learning.

    NE MARCHE PAS sur un fond texturé/multicolore (cf. _upscale_only ci-
    dessous et son commentaire) -- Otsu suppose un histogramme globalement
    bimodal (texte net vs fond uni), qui n'existe pas sur un artwork d'icône
    de buff (spirale/dégradé). Réservé aux zones à fond UNI (barres de
    Tariq, badges de stacks) où ça a été validé."""
    if cv2 is None:
        raise ImportError("opencv-python-headless requis pour l'OCR : pip install opencv-python-headless")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if upscale > 1:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def _upscale_only(frame: np.ndarray, upscale: int = 3) -> np.ndarray:
    """Niveaux de gris -> upscale, SANS binarisation Otsu.

    BUG TROUVÉ EN JEU (2026-07-16, comparaison log/vidéo avec l'utilisateur) :
    read_countdown_seconds (via _preprocess/Otsu) lisait "4.0" en continu
    alors que la vidéo montrait clairement "26" affiché au même instant --
    testé en isolant chaque étape : le crop couleur BRUT lit "26" avec
    ~99.9999% de confiance EasyOCR, mais la version Otsu-binarisée ne
    détecte RIEN DU TOUT. Root cause : Otsu suppose un fond globalement
    bimodal (texte net sur fond uni) -- sur l'artwork d'un buff Meiko
    (spirale/dégradé violet à fort contraste LOCAL mais PAS bimodal
    globalement), le seuil choisi automatiquement détruit le chiffre au lieu
    de l'isoler. Validé sur 3 échantillons vidéo supplémentaires (30, 14,
    18) : upscale seul (gris ou couleur, sans Otsu) lit correctement dans
    tous les cas testés, contre 0/4 pour la version Otsu."""
    if cv2 is None:
        raise ImportError("opencv-python-headless requis pour l'OCR : pip install opencv-python-headless")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if upscale > 1:
        gray = cv2.resize(gray, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
    return gray


def read_digits(frame: np.ndarray) -> Optional[int]:
    """Lit un nombre entier depuis la région capturée (stacks, etc.).
    Retourne None si rien de reconnaissable comme chiffre -- à traiter comme
    "valeur inconnue ce tick", pas comme 0 (0 est une vraie valeur possible)."""
    reader = _get_reader()
    processed = _preprocess(frame)
    results = reader.readtext(processed, allowlist="0123456789", detail=0)
    digits = "".join(c for c in "".join(results) if c.isdigit())
    return int(digits) if digits else None


def read_countdown_seconds(frame: np.ndarray) -> Optional[float]:
    """Lit un compte à rebours affiché en secondes sur une icône de buff
    (ex: '27', '16', '1.8', '0.4') et retourne un float, ou None si rien de
    lisible.

    AJOUTÉ (2026-07-16) pour le refresh proactif de Spirited Strikes côté
    Meiko (cf. hooks.py) -- contrairement à read_digits(), l'allowlist inclut
    le point décimal. Sans ça, '1.8' serait lu comme '18' (le point est
    silencieusement supprimé par l'allowlist digits-only, valeur x10 fausse)
    -- CRITIQUE ici : le jeu affiche justement le compte à rebours en
    décimal quand il approche de 0 (vu sur templates/meiko lors de la
    capture YOLO : '3.3', '1.8', '0.4'...), exactement la plage où un
    refresh proactif doit se déclencher -- une lecture x10 fausse dans cette
    plage précise ferait rater le refresh (ex: '1.8' lu comme 18s -> jamais
    sous le seuil de 5s avant que le buff n'expire pour de vrai)."""
    reader = _get_reader()
    processed = _upscale_only(frame)   # PAS _preprocess/Otsu -- cf. docstring _upscale_only
    results = reader.readtext(processed, allowlist="0123456789.", detail=0)
    text = "".join(results).strip()
    cleaned = "".join(c for c in text if c.isdigit() or c == ".")
    if not cleaned or cleaned == ".":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def read_text(frame: np.ndarray) -> str:
    """Lit du texte libre (nom du sort en cours de cast, etc.) -- moins
    fiable que read_digits sur de petites zones, à réserver aux cas où le
    texte brut est vraiment nécessaire."""
    reader = _get_reader()
    processed = _preprocess(frame)
    results = reader.readtext(processed, detail=0)
    return " ".join(results).strip()
