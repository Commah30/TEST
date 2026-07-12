"""
train_yolo.py — entraîne un modèle YOLO de détection sur un dataset produit
par build_yolo_dataset.py. Wrapper fin autour de l'API Python d'ultralytics,
avec des défauts pensés pour ce cas d'usage : des icônes minuscules,
toujours au même angle de caméra (UI de jeu, pas de vision "monde réel"),
peu de classes (3 pour l'instant) -- yolov8n (nano) et une petite résolution
d'entrée suffisent largement, pas besoin des variantes plus lourdes.

USAGE :
    python train_yolo.py <dataset_dir> [--epochs 100] [--imgsz 320]

Le run s'appuie sur le GPU si torch.cuda.is_available() (RTX 3060 détectée
sur cette machine) -- quelques minutes pour ce volume de données/classes,
pas des heures.

Résultat : runs/detect/train*/weights/best.pt -- c'est le fichier à pointer
depuis wardenbot/yolo_detect.py (YOLO_WEIGHTS_PATH) une fois satisfait des
métriques affichées en fin de run (précision/rappel par classe).
"""
from __future__ import annotations

import argparse
import sys

try:
    from ultralytics import YOLO
except ImportError:
    print("ultralytics requis : pip install ultralytics")
    sys.exit(1)

try:
    import torch
except ImportError:
    torch = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_dir")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=320)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--model", default="yolov8n.pt")
    args = parser.parse_args()

    device = 0 if (torch is not None and torch.cuda.is_available()) else "cpu"
    print(f"Device: {'GPU (' + torch.cuda.get_device_name(0) + ')' if device == 0 else 'CPU'}")

    model = YOLO(args.model)
    model.train(
        data=f"{args.dataset_dir}/data.yaml",
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        patience=20,  # arrêt anticipé si la validation stagne -- évite de sur-entraîner sur un petit dataset
    )

    print("\nEntraînement terminé -- poids dans runs/detect/train*/weights/best.pt")
    print("Vérifie precision/recall par classe ci-dessus avant de brancher le modèle en jeu.")


if __name__ == "__main__":
    main()
