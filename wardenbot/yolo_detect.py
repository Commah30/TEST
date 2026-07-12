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

import os
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

YOLO_WEIGHTS_PATH = Path("runs/detect/train-3/weights/best.pt")   # 2026-07-12 : entraîné sur le dataset élargi (4775 images, 3 clips) -- 59 epochs (early stop), P=0.998/R=0.996/mAP50=0.995 toutes classes confondues. train/ et train-2/ obsolètes.

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


def _select_device() -> "int | str":
    """GPU par défaut si dispo, CPU sinon -- mais overridable via la variable
    d'environnement WARDENBOT_YOLO_DEVICE=cpu, AJOUTÉE (2026-07-12) comme
    diagnostic pour la question "YOLO/EasyOCR peuvent-ils faire chuter le FPS
    du jeu ?" : capture_watchdog.log montre des pics ponctuels de detect() à
    plusieurs secondes (vs ~20ms en régime normal, confirmé non-illusoire par
    une comparaison avec torch.cuda.synchronize() -- même ordre de grandeur),
    compatibles avec une contention GPU (le jeu et torch/CUDA se disputent le
    même GPU). Mesuré : l'inférence CPU pour ce modèle/cette taille d'image
    coûte ~30-45ms (repère, machine de dev) -- largement sous
    YOLO_DETECT_INTERVAL_S (150ms, cf. wardenbot/state_update.py), donc
    forcer le CPU est un test JOUABLE en jeu réel (pas de nouveau budget
    dépassé) : si les freezes disparaissent en jouant avec cette variable
    positionnée, la contention GPU est confirmée comme cause -- sinon, il
    faut chercher ailleurs (capture mss/GDI, etc.)."""
    if os.environ.get("WARDENBOT_YOLO_DEVICE", "").strip().lower() == "cpu":
        return "cpu"
    return 0 if (torch is not None and torch.cuda.is_available()) else "cpu"


def detect(frame: np.ndarray, conf: float = 0.4) -> dict[str, float]:
    """Retourne {nom_de_classe: confiance} pour chaque détection au-dessus du
    seuil `conf`. Une classe absente du dict = non détectée. S'il y a
    plusieurs boîtes pour la même classe (ne devrait pas arriver ici, les 3
    buffs occupant des positions distinctes dans la barre), on garde la
    confiance la plus haute."""
    model = _get_model()
    device = _select_device()
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
