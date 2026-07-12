"""
yolo_detect.py — inférence d'un modèle YOLO entraîné (ultralytics) sur une
région d'écran, pour remplacer un groupe de template matches fragiles par un
seul détecteur appris. Pilote : tariq_buff_bar (3 classes -- cf.
build_yolo_dataset.py/train_yolo.py pour la génération du dataset et
l'entraînement).

Même principe que ocr.py : singleton paresseux (le chargement des poids est
coûteux, jamais recréé par appel), toujours pure lecture d'écran, zéro
lecture de mémoire du jeu.

YOLO_WEIGHTS_PATH pointe vers le fichier de poids entraîné -- absent tant
qu'aucun entraînement n'a été lancé (cf. train_yolo.py). detect() lève
FileNotFoundError dans ce cas plutôt que de silencieusement ne rien
détecter, pour ne pas masquer un déploiement incomplet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

try:
    from ultralytics import YOLO
except ImportError:  # permet d'importer le module pour les tests même sans ultralytics installé
    YOLO = None

try:
    import torch
except ImportError:
    torch = None

YOLO_WEIGHTS_PATH = Path("runs/detect/train-2/weights/best.pt")   # 2026-07-12 : entraîné sur le dataset élargi (4276 images, 2 clips) -- train/ (329 images, 1er run) est obsolète

_model = None  # singleton paresseux -- cf. docstring, chargement des poids coûteux


def _get_model():
    global _model
    if YOLO is None:
        raise ImportError("ultralytics requis : pip install ultralytics")
    if not YOLO_WEIGHTS_PATH.exists():
        raise FileNotFoundError(
            f"Poids introuvables : {YOLO_WEIGHTS_PATH} -- lance train_yolo.py d'abord, "
            "ou ajuste YOLO_WEIGHTS_PATH si le run a produit un autre dossier (train2, train3...)."
        )
    if _model is None:
        _model = YOLO(str(YOLO_WEIGHTS_PATH))
    return _model


def detect(frame: np.ndarray, conf: float = 0.4) -> dict[str, float]:
    """Retourne {nom_de_classe: confiance} pour chaque détection au-dessus du
    seuil `conf`. Une classe absente du dict = non détectée. S'il y a
    plusieurs boîtes pour la même classe (ne devrait pas arriver ici, les 3
    buffs occupant des positions distinctes dans la barre), on garde la
    confiance la plus haute."""
    model = _get_model()
    device = 0 if (torch is not None and torch.cuda.is_available()) else "cpu"
    results = model.predict(frame, conf=conf, device=device, verbose=False)

    found: dict[str, float] = {}
    for result in results:
        names = result.names
        for box in result.boxes:
            class_name = names[int(box.cls[0])]
            confidence = float(box.conf[0])
            if confidence > found.get(class_name, 0.0):
                found[class_name] = confidence
    return found
