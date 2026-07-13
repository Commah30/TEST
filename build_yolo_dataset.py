"""
build_yolo_dataset.py — construit un dataset YOLO (format détection) à partir
d'un clip vidéo, en s'auto-labellisant SOIT via le template matching EXISTANT
(matcher.py) sur tariq_buff_bar, SOIT via le modèle YOLO déjà entraîné
(--yolo-labels) -- pilote choisi car c'est la détection la plus fragile
identifiée cette session ("mitigée mais pas root-causée", cf.
CLAUDE.md/discussion). Zéro annotation manuelle de bounding box nécessaire
dans les deux cas.

Mode template (par défaut) : cv2.matchTemplate donne confiance + position
(minMaxLoc) pour les 3 templates hammer_storm_buff/skull_crusher_buff/
focused_wrath_buff -- au-dessus du seuil connu-bon (TARIQ_BUFF_BAR_THRESHOLDS,
main.py), on écrit directement une bounding box YOLO à cet endroit.

Mode YOLO (--yolo-labels, AJOUTÉ 2026-07-13) : cv2.matchTemplate n'est PAS
scale-invariant -- inutilisable si les icônes ont changé de taille à l'écran
(ex: option d'affichage "taille des buffs" augmentée en jeu) puisque les
templates PNG capturés restent à l'ancienne échelle. Le modèle YOLO déjà
entraîné, lui, généralise raisonnablement à une nouvelle échelle (mesuré :
0.94-0.96 de confiance sur des captures live à la nouvelle taille) -- on
s'en sert pour pseudo-labelliser le nouveau clip (semi-supervised
bootstrapping : le modèle imparfait actuel aide à construire son propre
jeu d'entraînement amélioré), plutôt que de recapturer des templates à la
main pour chaque changement d'échelle futur.

Dans les deux modes : sous le seuil pour les 3 classes -> image "vide"
(label vide), utile comme exemple négatif. Ce n'est PAS une vérité terrain
parfaite (mêmes faux positifs/négatifs qu'aujourd'hui en jeu) mais un point
de départ largement plus rapide qu'un étiquetage 100% manuel. l'utilisateur garde
la main pour supprimer les paires image+label visiblement fausses avant
training (cf. dossier preview/, section USAGE).

USAGE :
    python build_yolo_dataset.py <video.mp4> [--fps 3] [--out dataset_tariq_buffs]
    python build_yolo_dataset.py <video.mp4> --yolo-labels [--yolo-conf 0.3]

Étapes :
    1. Lance ce script sur un ou plusieurs clips Game Bar de Tariq couvrant
       le plus de situations possible : aucun buff actif, chaque buff seul,
       plusieurs buffs simultanés si ça arrive en jeu, différents fonds/VFX
       de combat. Plus les clips sont variés, mieux le modèle généralise --
       largement plus important que le nombre total d'images.
    2. Ouvre dataset_tariq_buffs/preview/ -- chaque image montre les
       bounding box auto-détectées dessinées dessus + le nom de la classe.
       Repère les erreurs évidentes (boîte au mauvais endroit, buff
       manqué, faux positif) et note leur nom de fichier (ex: 00042).
    3. Pour chaque erreur repérée, supprime la paire correspondante :
           images/train/00042.png (ou images/val/00042.png)
           labels/train/00042.txt (ou labels/val/00042.txt)
       (le dossier preview/ n'a pas besoin d'être nettoyé, il ne sert qu'à
       la relecture.)
    4. Relance sur d'autres clips si besoin (les nouvelles images
       s'ajoutent au même dataset, numérotées à la suite).
    5. Une fois satisfait : python train_yolo.py dataset_tariq_buffs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:
    print("opencv-python-headless requis : pip install -r requirements.txt")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).parent))
from main import REGIONS, TARIQ_BUFF_BAR_THRESHOLDS  # noqa: E402
from wardenbot.yolo_detect import detect_with_boxes  # noqa: E402

CLASS_NAMES = list(TARIQ_BUFF_BAR_THRESHOLDS.keys())
TEMPLATE_DIR = Path("templates/tariq")
VAL_EVERY = 7  # 1 image sur 7 part en validation (~15%), le reste en train


def load_templates() -> dict[str, np.ndarray]:
    templates = {}
    for name in CLASS_NAMES:
        path = TEMPLATE_DIR / f"{name}.png"
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            print(f"Template introuvable : {path}")
            sys.exit(1)
        templates[name] = img
    return templates


def auto_label(crop: np.ndarray, templates: dict[str, np.ndarray]) -> list[tuple[int, float, float, float, float]]:
    """Retourne une liste de (class_id, cx, cy, w, h) normalisés 0-1 (format
    YOLO), une par template dont la confiance dépasse son seuil connu-bon."""
    boxes = []
    h_crop, w_crop = crop.shape[:2]
    for class_id, name in enumerate(CLASS_NAMES):
        template = templates[name]
        th, tw = template.shape[:2]
        if h_crop < th or w_crop < tw:
            continue
        result = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < TARIQ_BUFF_BAR_THRESHOLDS[name]:
            continue
        x, y = max_loc
        cx = (x + tw / 2) / w_crop
        cy = (y + th / 2) / h_crop
        boxes.append((class_id, cx, cy, tw / w_crop, th / h_crop))
    return boxes


def auto_label_yolo(crop: np.ndarray, conf: float) -> list[tuple[int, float, float, float, float]]:
    """Équivalent de auto_label() mais via le modèle YOLO déjà entraîné
    (detect_with_boxes) plutôt que cv2.matchTemplate -- cf. docstring du
    module. Garde une seule boîte par classe (la plus confiante), même
    logique que detect() en prod : au-delà de ça, le nettoyage des
    chevauchements inter-classes (hammer_storm_buff/skull_crusher_buff sur
    la même icône, bug déjà rencontré) se fait après coup sur le dataset
    complet, pas ici image par image."""
    name_to_id = {name: i for i, name in enumerate(CLASS_NAMES)}
    best: dict[str, tuple[float, tuple[float, float, float, float]]] = {}
    for class_name, confidence, box in detect_with_boxes(crop, conf=conf):
        if class_name not in name_to_id:
            continue
        if confidence > best.get(class_name, (0.0, None))[0]:
            best[class_name] = (confidence, box)
    return [(name_to_id[name], *box) for name, (_, box) in best.items()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--fps", type=float, default=3.0)
    parser.add_argument("--out", default="dataset_tariq_buffs")
    parser.add_argument("--yolo-labels", action="store_true",
                         help="Auto-labellise avec le modèle YOLO déjà entraîné au lieu du template matching -- "
                              "nécessaire si l'échelle des icônes à l'écran a changé (cf. docstring du module).")
    parser.add_argument("--yolo-conf", type=float, default=0.3,
                         help="Seuil de confiance en mode --yolo-labels (défaut 0.3 -- volontairement permissif, "
                              "le tri se fait ensuite via preview/, pas ici).")
    args = parser.parse_args()

    region = REGIONS["tariq"]["tariq_buff_bar"]
    templates = None if args.yolo_labels else load_templates()

    out_dir = Path(args.out)
    for sub in ("images/train", "images/val", "labels/train", "labels/val", "preview"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"Impossible d'ouvrir {args.video}")
        sys.exit(1)

    video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_stride = max(1, round(video_fps / args.fps))

    # Numérotation à la suite de ce qui existe déjà, pour pouvoir cumuler
    # plusieurs clips dans le même dataset sans écraser les fichiers d'un
    # run précédent.
    existing = list((out_dir / "images/train").glob("*.png")) + list((out_dir / "images/val").glob("*.png"))
    next_idx = max((int(p.stem) for p in existing), default=-1) + 1

    frame_idx = 0
    saved = 0
    empty = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % frame_stride == 0:
            x, y, w, h = region["left"], region["top"], region["width"], region["height"]
            crop = frame[y : y + h, x : x + w]
            if crop.shape[0] == h and crop.shape[1] == w:
                boxes = auto_label_yolo(crop, args.yolo_conf) if args.yolo_labels else auto_label(crop, templates)
                if not boxes:
                    empty += 1
                idx = next_idx + saved
                split = "val" if idx % VAL_EVERY == 0 else "train"
                name = f"{idx:05d}"

                cv2.imwrite(str(out_dir / f"images/{split}/{name}.png"), crop)
                with open(out_dir / f"labels/{split}/{name}.txt", "w", encoding="utf-8") as f:
                    for class_id, cx, cy, bw, bh in boxes:
                        f.write(f"{class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

                preview = crop.copy()
                for class_id, cx, cy, bw, bh in boxes:
                    ph, pw = preview.shape[:2]
                    x1, y1 = int((cx - bw / 2) * pw), int((cy - bh / 2) * ph)
                    x2, y2 = int((cx + bw / 2) * pw), int((cy + bh / 2) * ph)
                    cv2.rectangle(preview, (x1, y1), (x2, y2), (0, 255, 0), 1)
                    cv2.putText(preview, CLASS_NAMES[class_id][:12], (x1, max(0, y1 - 3)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
                cv2.imwrite(str(out_dir / f"preview/{name}.png"), preview)
                saved += 1
        frame_idx += 1
    cap.release()

    data_yaml = out_dir / "data.yaml"
    data_yaml.write_text(
        "path: " + str(out_dir.resolve()).replace("\\", "/") + "\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n" + "".join(f"  {i}: {name}\n" for i, name in enumerate(CLASS_NAMES)),
        encoding="utf-8",
    )

    print(f"{saved} images ajoutées ({empty} sans aucun buff détecté -- exemples négatifs, normal).")
    print(f"Relis {out_dir / 'preview'} et supprime les paires image+label visiblement fausses (cf. docstring).")
    print(f"Puis : python train_yolo.py {args.out}")


if __name__ == "__main__":
    main()
