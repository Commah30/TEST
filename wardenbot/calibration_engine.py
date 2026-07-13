"""
calibration_engine.py — pour une région candidate (sélectionnée par
l'utilisateur via le futur overlay, Phase 4c), exécute le détecteur
approprié et retourne un résultat structuré prêt à afficher : "Buff bar ->
YOLO détecte les buffs", "Fury -> % lu", "Cast bar -> progression",
"Template -> score" (cf. spec Phase 4).

Phase 4b de l'industrialisation (2026-07-14) : pure logique, PAS d'overlay
graphique -- réutilisable telle quelle par la future UI (Phase 4c/4d) et
testable sans elle. Découpé en deux couches :
  - analyze_*_crop(crop, ...) : reçoit un crop DÉJÀ CAPTURÉ (np.ndarray) --
    testable avec des images synthétiques, zéro dépendance à un écran réel.
  - capture_and_analyze(capturer, region, kind, ...) : capture PUIS analyse,
    point d'entrée unique pour l'overlay (qui, lui, aura un vrai écran).

Réutilise les détecteurs EXISTANTS tels quels (wardenbot/yolo_detect.py,
wardenbot/bar_reader.py, wardenbot/matcher.py) -- ce module ne réimplémente
AUCUNE logique de détection, il les enchaîne juste et formate le résultat.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from wardenbot.bar_reader import read_fill_pct
from wardenbot.capture import ScreenCapture
from wardenbot.matcher import TemplateMatcher
from wardenbot.yolo_detect import detect

KIND_YOLO = "yolo"
KIND_BAR = "bar"
KIND_TEMPLATE = "template"


@dataclass
class CalibrationResult:
    """Ce que l'overlay affichera : le crop (pour l'aperçu visuel) + un
    résumé textuel du résultat du détecteur. success=False + error rempli
    si la capture ou le détecteur a levé une exception -- jamais propagée
    telle quelle vers l'appelant (une région mal calibrée par l'utilisateur
    ne doit jamais planter l'assistant)."""
    region: dict
    kind: str
    crop: Optional[np.ndarray]
    summary: str
    success: bool
    error: Optional[str] = None


def analyze_yolo_crop(crop: np.ndarray, region: dict, conf: float = 0.4) -> CalibrationResult:
    """Pour la barre de buffs Tariq -- exécute le modèle YOLO entraîné sur
    le crop, résume les classes détectées + confiance (triées, la plus
    confiante en premier)."""
    try:
        found = detect(crop, conf=conf)
    except Exception as exc:
        return CalibrationResult(region=region, kind=KIND_YOLO, crop=crop, summary="", success=False, error=str(exc))

    if not found:
        summary = "Aucun buff détecté"
    else:
        summary = ", ".join(f"{name}: {score:.2f}" for name, score in sorted(found.items(), key=lambda kv: -kv[1]))
    return CalibrationResult(region=region, kind=KIND_YOLO, crop=crop, summary=summary, success=True)


def analyze_bar_crop(
    crop: np.ndarray,
    region: dict,
    fill_bgr: tuple[int, int, int],
    tolerance: int = 30,
    direction: str = "ltr",
    max_gap: int = 2,
) -> CalibrationResult:
    """Pour Fury/cast bar -- exécute bar_reader.read_fill_pct, résume le %
    de remplissage lu."""
    try:
        pct = read_fill_pct(crop, fill_bgr, tolerance=tolerance, direction=direction, max_gap=max_gap)
    except Exception as exc:
        return CalibrationResult(region=region, kind=KIND_BAR, crop=crop, summary="", success=False, error=str(exc))
    return CalibrationResult(region=region, kind=KIND_BAR, crop=crop, summary=f"{pct * 100:.1f}% rempli", success=True)


def analyze_template_crop(
    crop: np.ndarray,
    region: dict,
    matcher: TemplateMatcher,
    template_name: str,
    threshold: float = 0.70,
) -> CalibrationResult:
    """Pour une icône ability/debuff -- exécute TemplateMatcher.confidence,
    résume le score vs le seuil de match."""
    try:
        confidence = matcher.confidence(crop, template_name)
    except Exception as exc:
        return CalibrationResult(region=region, kind=KIND_TEMPLATE, crop=crop, summary="", success=False, error=str(exc))
    verdict = "OK" if confidence >= threshold else "insuffisant"
    summary = f"score={confidence:.2f} (seuil {threshold:.2f}) -- {verdict}"
    return CalibrationResult(region=region, kind=KIND_TEMPLATE, crop=crop, summary=summary, success=True)


def capture_and_analyze(capturer: ScreenCapture, region: dict, kind: str, **kwargs) -> CalibrationResult:
    """Point d'entrée unique pour l'overlay (Phase 4c) : capture PUIS
    analyse, quel que soit le type de région. `kwargs` transmis tel quel à
    la fonction analyze_*_crop correspondante (fill_bgr/tolerance/... pour
    "bar", matcher/template_name/... pour "template", conf pour "yolo")."""
    try:
        crop = capturer.grab(region)
    except Exception as exc:
        return CalibrationResult(region=region, kind=kind, crop=None, summary="", success=False, error=str(exc))

    if kind == KIND_YOLO:
        return analyze_yolo_crop(crop, region, **kwargs)
    if kind == KIND_BAR:
        return analyze_bar_crop(crop, region, **kwargs)
    if kind == KIND_TEMPLATE:
        return analyze_template_crop(crop, region, **kwargs)

    return CalibrationResult(
        region=region, kind=kind, crop=crop, summary="", success=False,
        error=f"type de détecteur inconnu : {kind!r} (attendu {KIND_YOLO!r}/{KIND_BAR!r}/{KIND_TEMPLATE!r})",
    )
