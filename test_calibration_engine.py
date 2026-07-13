"""
test_calibration_engine.py — prouve que chaque type de région de
calibration (bar/template/yolo) produit un CalibrationResult exploitable,
sans jamais lever d'exception même sur une entrée dégénérée (région vide,
template inconnu, crop corrompu).

Phase 4b de l'industrialisation (2026-07-14). Contrairement à
test_wardenbot.py/test_tariq.py (délibérément sans dépendance lourde), ce
fichier dépend d'OpenCV/ultralytics/torch comme le fait déjà
build_yolo_dataset.py/yolo_check.py -- inhérent à ce qu'il teste (les
détecteurs réels), pas une régression de philosophie du projet.

Lance avec : python test_calibration_engine.py
"""
from __future__ import annotations

import cv2
import numpy as np

from wardenbot.calibration_engine import (
    KIND_BAR,
    KIND_TEMPLATE,
    KIND_YOLO,
    analyze_bar_crop,
    analyze_template_crop,
    analyze_yolo_crop,
    capture_and_analyze,
)
from wardenbot.matcher import TemplateMatcher

DUMMY_REGION = {"left": 0, "top": 0, "width": 10, "height": 10}


def test_analyze_bar_crop_reports_correct_fill_percentage():
    """Même principe que les tests bar_reader existants (test_tariq.py) --
    ici on vérifie que le résumé texte produit par le moteur de calibration
    est cohérent avec le vrai pourcentage lu."""
    fill = (200, 50, 20)
    frame = np.zeros((6, 100, 3), dtype=np.uint8)
    frame[:, :50] = fill
    result = analyze_bar_crop(frame, DUMMY_REGION, fill_bgr=fill, direction="ltr")
    assert result.success is True
    assert result.kind == KIND_BAR
    assert "50.0%" in result.summary or "49." in result.summary  # tolérance d'arrondi
    print(f"analyze_bar_crop : {result.summary}")


def test_analyze_bar_crop_handles_zero_width_gracefully():
    """read_fill_pct gère déjà une largeur nulle -> 0.0 sans lever (cf.
    bar_reader.py), donc analyze_bar_crop doit remonter success=True/0.0%,
    pas une erreur."""
    zero_width_frame = np.zeros((6, 0, 3), dtype=np.uint8)
    result = analyze_bar_crop(zero_width_frame, DUMMY_REGION, fill_bgr=(0, 0, 0))
    assert result.success is True
    assert "0.0%" in result.summary
    print("analyze_bar_crop : largeur nulle -> 0.0%, géré nativement par read_fill_pct.")


def test_analyze_bar_crop_never_raises_on_a_truly_corrupt_crop():
    """Une région mal calibrée AU POINT de produire un crop à 0 lignes fait
    lever une IndexError DANS read_fill_pct (indexation de la ligne du
    milieu sur un tableau vide) -- le rôle du moteur de calibration est
    justement de ne JAMAIS laisser cette exception remonter jusqu'à
    l'assistant/l'overlay : success=False + message, pas un crash."""
    zero_height_frame = np.zeros((0, 10, 3), dtype=np.uint8)
    result = analyze_bar_crop(zero_height_frame, DUMMY_REGION, fill_bgr=(0, 0, 0))
    assert result.success is False
    assert result.error is not None
    print(f"analyze_bar_crop : crop à 0 lignes -> success=False capturé proprement, error={result.error!r}")


def test_analyze_template_crop_matches_the_real_reference_template():
    """Charge le VRAI template heavy_strike.png (templates/tariq/) comme
    'crop' -- doit matcher contre lui-même à ~1.0 de confiance."""
    matcher = TemplateMatcher("templates/tariq")
    reference = cv2.imread("templates/tariq/heavy_strike.png", cv2.IMREAD_COLOR)
    assert reference is not None, "templates/tariq/heavy_strike.png introuvable -- prérequis du test"

    result = analyze_template_crop(reference, DUMMY_REGION, matcher, "heavy_strike", threshold=0.70)
    assert result.success is True
    assert result.kind == KIND_TEMPLATE
    assert "OK" in result.summary
    print(f"analyze_template_crop (template contre lui-même) : {result.summary}")


def test_analyze_template_crop_reports_insufficient_score_on_mismatch():
    matcher = TemplateMatcher("templates/tariq")
    noise = np.random.randint(0, 255, size=(48, 51, 3), dtype=np.uint8)
    result = analyze_template_crop(noise, DUMMY_REGION, matcher, "heavy_strike", threshold=0.70)
    assert result.success is True
    assert "insuffisant" in result.summary
    print(f"analyze_template_crop (bruit aléatoire) : {result.summary}")


def test_analyze_template_crop_reports_failure_for_unknown_template():
    matcher = TemplateMatcher("templates/tariq")
    frame = np.zeros((48, 51, 3), dtype=np.uint8)
    result = analyze_template_crop(frame, DUMMY_REGION, matcher, "ce_template_nexiste_pas")
    assert result.success is False
    assert result.error is not None
    print(f"analyze_template_crop (template inconnu) : success=False, error={result.error!r}")


def test_analyze_yolo_crop_detects_a_real_buff_from_the_training_set():
    """Utilise une vraie image du dataset d'entraînement (label connu :
    classe 2 = focused_wrath_buff, cf. dataset_tariq_buffs/labels/train/00022.txt)
    -- preuve que le moteur de calibration détecte réellement un buff, pas
    juste 'ne plante pas'."""
    frame = cv2.imread("dataset_tariq_buffs/images/train/00022.png", cv2.IMREAD_COLOR)
    assert frame is not None, "image d'entraînement introuvable -- prérequis du test"

    result = analyze_yolo_crop(frame, DUMMY_REGION, conf=0.4)
    assert result.success is True
    assert result.kind == KIND_YOLO
    assert result.summary != "Aucun buff détecté", f"attendu au moins un buff détecté, reçu : {result.summary!r}"
    print(f"analyze_yolo_crop (vraie image d'entraînement) : {result.summary}")


def test_analyze_yolo_crop_does_not_crash_on_a_blank_crop():
    blank = np.zeros((150, 350, 3), dtype=np.uint8)
    result = analyze_yolo_crop(blank, DUMMY_REGION, conf=0.4)
    assert result.success is True
    print(f"analyze_yolo_crop (crop vide) : {result.summary}")


def test_capture_and_analyze_reports_failure_for_unknown_kind():
    class _FakeCapturer:
        def grab(self, region):
            return np.zeros((10, 10, 3), dtype=np.uint8)

    result = capture_and_analyze(_FakeCapturer(), DUMMY_REGION, kind="nawak")
    assert result.success is False
    assert "inconnu" in result.error
    print(f"capture_and_analyze (type inconnu) : success=False, error={result.error!r}")


def test_capture_and_analyze_reports_failure_when_capture_itself_fails():
    class _BrokenCapturer:
        def grab(self, region):
            raise RuntimeError("région hors écran")

    result = capture_and_analyze(_BrokenCapturer(), DUMMY_REGION, kind=KIND_BAR, fill_bgr=(0, 0, 0))
    assert result.success is False
    assert result.crop is None
    assert "hors écran" in result.error
    print(f"capture_and_analyze (capture échouée) : success=False, error={result.error!r}")


if __name__ == "__main__":
    test_analyze_bar_crop_reports_correct_fill_percentage()
    test_analyze_bar_crop_handles_zero_width_gracefully()
    test_analyze_bar_crop_never_raises_on_a_truly_corrupt_crop()
    test_analyze_template_crop_matches_the_real_reference_template()
    test_analyze_template_crop_reports_insufficient_score_on_mismatch()
    test_analyze_template_crop_reports_failure_for_unknown_template()
    test_analyze_yolo_crop_detects_a_real_buff_from_the_training_set()
    test_analyze_yolo_crop_does_not_crash_on_a_blank_crop()
    test_capture_and_analyze_reports_failure_for_unknown_kind()
    test_capture_and_analyze_reports_failure_when_capture_itself_fails()
    print("\nTous les tests passent.")
