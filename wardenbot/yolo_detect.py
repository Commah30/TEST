"""
yolo_detect.py — inférence d'un modèle YOLO entraîné (ultralytics) sur une
région d'écran, pour remplacer un groupe de template matches fragiles par un
seul détecteur appris. Deux modèles distincts, un par personnage : Tariq
(tariq_buff_bar, 3 classes) et Meiko (buff_bar, 3 classes -- spirited_vortex/
spirited_strikes/warden_of_the_temple_3, cf. build_yolo_dataset.py/
train_yolo.py pour la génération du dataset et l'entraînement de chacun).

Même principe que ocr.py : singleton paresseux (le chargement des poids est
coûteux, jamais recréé par appel), toujours pure lecture d'écran, zéro
lecture de mémoire du jeu.

MULTI-MODÈLE (2026-07-16, généralisé pour Meiko) : `_models` est un cache
PAR CHEMIN DE POIDS (dict), pas un singleton unique -- Tariq et Meiko ont
chacun leur propre modèle entraîné (classes différentes), les deux peuvent
coexister en mémoire sans se marcher dessus. `weights_path` par défaut sur
TARIQ_YOLO_WEIGHTS_PATH partout (comportement STRICTEMENT inchangé pour tout
le code existant qui appelle detect()/detect_with_boxes()/_get_model() sans
argument -- calibration_engine.py, diagnostic_report.py, yolo_check.py,
state_update.py côté Tariq). YOLO_WEIGHTS_PATH reste un alias de
TARIQ_YOLO_WEIGHTS_PATH pour rétrocompatibilité.

*_YOLO_WEIGHTS_PATH pointe vers le fichier de poids entraîné -- absent tant
qu'aucun entraînement n'a été lancé (cf. train_yolo.py). detect() lève
FileNotFoundError dans ce cas plutôt que de silencieusement ne rien
détecter, pour ne pas masquer un déploiement incomplet.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from wardenbot.paths import resource_path

try:
    from ultralytics import YOLO
except ImportError:  # permet d'importer le module pour les tests même sans ultralytics installé
    YOLO = None

try:
    import torch
except ImportError:
    torch = None

# resource_path() (2026-07-26, packaging PyInstaller onedir) : en dev,
# comportement inchangé (Path relatif au CWD comme avant) ; dans un .exe
# packagé, ancré au dossier de l'exécutable -- cf. wardenbot/paths.py. Les
# poids eux-mêmes ne sont PLUS versionnés dans git (cf. .gitignore) -- à
# fournir en asset de release séparé au moment du packaging.
TARIQ_YOLO_WEIGHTS_PATH = resource_path("runs/detect/train-5/weights/best.pt")   # 2026-07-13 : dataset élargi avec la session post-fix (skip_detection focused_wrath/thunder_call, seuil fury_above:30) -- 7358 images (5129 + 2229, dont 748 avec buff réel), 36 epochs (early stop, patience=20) -- P=0.992/R=0.997/mAP50=0.995 toutes classes confondues (hammer_storm_buff P=1.00/R=0.99, skull_crusher_buff P=0.987/R=1.00, focused_wrath_buff P=0.99/R=1.00). train-4 reste valide (5129 images, ancien dataset) si besoin de comparaison -- train/, train-2/, train-3/ obsolètes.
MEIKO_YOLO_WEIGHTS_PATH = resource_path("runs/detect/train-6/weights/best.pt")   # 2026-07-15 : spirited_vortex/spirited_strikes/warden_of_the_temple_3, 428 images (1 clip dummy, nettoyées de 2 contaminations de fond -- braise + artefact début de combat, cf. build_yolo_dataset.py), 30 epochs (early stop) -- P>=0.994/R=1.0/mAP50=0.995 toutes classes. Un seul clip/contexte -- généralisation à un vrai combat pas encore validée (cf. discussion).

YOLO_WEIGHTS_PATH = TARIQ_YOLO_WEIGHTS_PATH   # alias rétrocompatible -- ne pas retirer, cf. docstring

_models: dict[str, "YOLO"] = {}  # un singleton par chemin de poids -- cf. docstring MULTI-MODÈLE

# LOCK (2026-07-19, audit performance) : _get_model() a toujours reposé sur
# un invariant IMPLICITE -- un seul thread de détection actif à la fois (un
# seul personnage sélectionné, cf. game_config.py::CHARACTERS + shutdown_fn
# du 2026-07-18 qui coupe l'ancien worker avant qu'un nouveau démarre) --
# jamais sur une synchronisation explicite dans CE fichier. Ce lock ne
# protège pas contre une contention GPU (VRAM/kernels CUDA sérialisés si deux
# modèles inféraient VRAIMENT en parallèle un jour -- hors-scope ici, ça
# resterait un problème même avec ce lock) : il protège UNIQUEMENT le
# check-then-set sur _models, pour qu'un futur bug de lifecycle (worker qui
# traîne pendant un switch de personnage) ne charge pas deux fois les mêmes
# poids en parallèle plutôt que de faire planter ou corrompre quoi que ce
# soit de silencieux. Coût nul sur le chemin chaud : le lock n'est pris QUE
# sur cache-miss (chargement, une fois par poids), jamais à chaque appel de
# detect()/detect_with_boxes().
# EN: LOCK (2026-07-19, performance audit): _get_model() has always relied on
# EN: an IMPLICIT invariant -- only one detection thread active at a time
# EN: (only one character selected, cf. game_config.py::CHARACTERS +
# EN: 2026-07-18's shutdown_fn which cuts the old worker before a new one
# EN: starts) -- never on explicit synchronization in THIS file. This lock
# EN: does NOT protect against GPU contention (VRAM/serialized CUDA kernels
# EN: if two models truly inferred in parallel one day -- out of scope here,
# EN: that would remain a problem even with this lock): it ONLY protects the
# EN: check-then-set on _models, so a future lifecycle bug (a worker lingering
# EN: during a character switch) loads the same weights twice in parallel
# EN: instead of silently crashing or corrupting anything. Zero cost on the
# EN: hot path: the lock is only taken on a cache miss (loading, once per
# EN: weights file), never on every detect()/detect_with_boxes() call.
_models_lock = threading.Lock()


def _get_model(weights_path: Path = TARIQ_YOLO_WEIGHTS_PATH):
    global _models
    if YOLO is None:
        raise ImportError("ultralytics requis : pip install ultralytics")
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Poids introuvables : {weights_path} -- lance train_yolo.py d'abord, "
            "ou ajuste le chemin si le run a produit un autre dossier (train2, train3...)."
        )
    key = str(weights_path)
    if key not in _models:
        with _models_lock:
            if key not in _models:   # double-check locking -- evite un 2e chargement si deja fait entre-temps
                _models[key] = YOLO(key)
    return _models[key]


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


# Dernière décomposition par étape d'un appel predict() (préprocess/
# inférence/postprocess en ms, déjà calculée par ultralytics lui-même dans
# results[0].speed -- gratuit, pas besoin de la recalculer à la main).
# AJOUTÉ (2026-07-13) pour le diagnostic multi-étapes de detect() (cf.
# wardenbot/diagnostics.py::log_pipeline_diagnostic, appelé depuis
# state_update.py). Effet de bord assumé (module-level, pas thread-safe à
# 100% en théorie) -- CORRIGÉ (2026-07-19, audit performance) : ce
# commentaire disait à l'origine "detect() n'est appelé que depuis UN SEUL
# thread (le thread de détection Tariq)", devenu FAUX depuis l'ajout du
# thread de détection Meiko (qui appelle aussi detect_with_boxes) -- il
# existe maintenant DEUX threads candidats, pas un seul. Ce qui protège
# réellement cette variable n'est donc PAS "un seul thread appelant", mais
# l'invariant "un seul personnage actif à la fois" (shutdown_fn, 2026-07-18)
# -- un thread meurt avant que l'autre démarre. Aucun lock ajouté ici (cette
# variable n'est que du DIAGNOSTIC, une valeur légèrement racée n'a aucun
# impact sur une décision de jeu -- contrairement à _models ci-dessus, qui
# a eu droit à son lock).
_last_predict_speed_ms: dict[str, float] = {}


def detect(frame: np.ndarray, conf: float = 0.4, weights_path: Path = TARIQ_YOLO_WEIGHTS_PATH) -> dict[str, float]:
    """Retourne {nom_de_classe: confiance} pour chaque détection au-dessus du
    seuil `conf`. Une classe absente du dict = non détectée. S'il y a
    plusieurs boîtes pour la même classe (ne devrait pas arriver ici, les 3
    buffs occupant des positions distinctes dans la barre), on garde la
    confiance la plus haute.

    half=True (2026-07-13) : mesuré sur le modèle réel (train-4) --
    médiane 11.31ms (FP32) -> 10.95ms (FP16), ~11% de gain sur la seule
    inférence. Gratuit, pas de perte de précision observée sur ce modèle
    3 classes (P/R inchangés sur le dataset de validation).

    quantize=16 (2026-07-19) : remplace half=True -- `half` est déprécié dans
    la version d'ultralytics installée (8.4.92), warning "'half' is
    deprecated... Use 'quantize' instead." à CHAQUE appel predict(). Vérifié
    dans ultralytics/cfg/__init__.py : ultralytics traduisait déjà lui-même
    half=True/False en quantize=16/None en interne (_handle_deprecation,
    "int8 wins over half") -- même précision FP16 qu'avant, le passage à
    quantize= direct n'est qu'un nettoyage du warning, aucun changement de
    comportement/précision.

    weights_path (2026-07-16) : défaut Tariq pour ne rien changer aux
    appelants existants -- passer MEIKO_YOLO_WEIGHTS_PATH pour Meiko."""
    global _last_predict_speed_ms
    model = _get_model(weights_path)
    device = _select_device()
    results = model.predict(frame, conf=conf, device=device, quantize=(16 if device != "cpu" else None), verbose=False)
    _last_predict_speed_ms = dict(results[0].speed) if results else {}

    # BATCH GPU->CPU (2026-07-18, suite discussion capture_watchdog.log) :
    # int(box.cls[0])/float(box.conf[0]) DANS la boucle forcent un sync
    # device->host PAR élément -- ici au plus 3 (3 classes), donc le gain
    # mesurable est probablement modeste (cf. discussion : results[0].speed
    # a vraisemblablement déjà synchronisé pour être exact), mais c'est un
    # anti-pattern reconnu et .cpu().numpy() une seule fois sur le tenseur
    # entier ne coûte rien de plus dans TOUS les cas (y compris device="cpu",
    # où .cpu() est un no-op) -- gardé en hygiène, pas présenté comme LE fix
    # des pics de plusieurs secondes observés (cf. discussion, hypothèses
    # encore ouvertes : fuite de threads pré-fix, échauffement cuDNN).
    # EN: BATCH GPU->CPU (2026-07-18, following the capture_watchdog.log
    # EN: discussion): int(box.cls[0])/float(box.conf[0]) INSIDE the loop
    # EN: force a device->host sync PER element -- at most 3 here (3
    # EN: classes), so the measurable gain is probably modest (cf.
    # EN: discussion: results[0].speed has likely already synchronized to be
    # EN: accurate), but it's a recognized anti-pattern and .cpu().numpy()
    # EN: once on the whole tensor costs nothing extra in ANY case (including
    # EN: device="cpu", where .cpu() is a no-op) -- kept as hygiene, not
    # EN: presented as THE fix for the multi-second spikes observed (cf.
    # EN: discussion, hypotheses still open: pre-fix thread leak, cuDNN
    # EN: warmup).
    found: dict[str, float] = {}
    for result in results:
        names = result.names
        classes = result.boxes.cls.cpu().numpy()
        confidences = result.boxes.conf.cpu().numpy()
        for cls_val, conf_val in zip(classes, confidences):
            class_name = names[int(cls_val)]
            confidence = float(conf_val)
            if confidence > found.get(class_name, 0.0):
                found[class_name] = confidence
    return found


def get_last_predict_speed_ms() -> dict[str, float]:
    """Décomposition ultralytics (preprocess/inference/postprocess, en ms)
    du DERNIER appel à detect() -- lue par le diagnostic multi-étapes
    (cf. state_update.py) juste après un appel lent, pour savoir combien de
    temps ultralytics dit lui-même avoir passé dans chaque étape. Si la
    somme de ces 3 valeurs est BIEN INFÉRIEURE à la durée totale mesurée en
    wall-clock autour de detect(), le temps manquant est passé AILLEURS
    (scheduling GIL/OS, attente GPU non comptabilisée par ultralytics) --
    signal précieux pour distinguer "le calcul est lent" de "quelque chose
    d'externe a bloqué avant même que le calcul commence"."""
    return dict(_last_predict_speed_ms)


def detect_with_boxes(
    frame: np.ndarray, conf: float = 0.4, weights_path: Path = TARIQ_YOLO_WEIGHTS_PATH
) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    """Comme detect(), mais retourne aussi la position de chaque boîte
    (cx, cy, w, h normalisés 0-1, format YOLO) -- detect() les jette
    volontairement (le pipeline live n'a besoin que d'un booléen/confiance
    par classe). AJOUTÉ (2026-07-13) pour auto-labelliser un nouveau dataset
    avec le modèle déjà entraîné plutôt qu'avec les anciens templates PNG
    (cv2.matchTemplate n'est pas scale-invariant -- inutilisable après un
    changement d'échelle de l'UI, cf. build_yolo_dataset.py --yolo-labels).
    Retourne une liste (pas un dict) : contrairement à detect(), on veut ICI
    toutes les boîtes, pas juste la meilleure par classe -- l'appelant filtre
    lui-même si besoin.

    RÉUTILISÉ (2026-07-16) par make_meiko_state_update_fn (state_update.py)
    pour localiser la boîte de spirited_strikes et y lire le compte à rebours
    en OCR -- la barre de buffs étant dynamique (centrée, position variable
    selon le nombre d'icônes actives), impossible de cibler une région fixe
    pour l'OCR : seule la boîte YOLO donne la position réelle à cet instant.

    weights_path (2026-07-16) : défaut Tariq pour ne rien changer aux
    appelants existants -- passer MEIKO_YOLO_WEIGHTS_PATH pour Meiko."""
    model = _get_model(weights_path)
    device = _select_device()
    results = model.predict(frame, conf=conf, device=device, verbose=False)

    # BATCH GPU->CPU -- même raison que detect() ci-dessus.
    # EN: BATCH GPU->CPU -- same reason as detect() above.
    boxes_out: list[tuple[str, float, tuple[float, float, float, float]]] = []
    for result in results:
        names = result.names
        classes = result.boxes.cls.cpu().numpy()
        confidences = result.boxes.conf.cpu().numpy()
        xywhn = result.boxes.xywhn.cpu().numpy()
        for cls_val, conf_val, box_xywhn in zip(classes, confidences, xywhn):
            class_name = names[int(cls_val)]
            confidence = float(conf_val)
            cx, cy, w, h = (float(v) for v in box_xywhn)
            boxes_out.append((class_name, confidence, (cx, cy, w, h)))
    return boxes_out
