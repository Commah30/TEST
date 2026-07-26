"""
diagnostic_report.py — bouton "Diagnostic" (Phase 5 de l'industrialisation,
2026-07-14) : exécute TOUTES les vérifications indépendamment (contrairement
à ConfigManager.validate_profile(), Phase 2, qui s'arrête à la première
erreur -- le bon choix pour bloquer un démarrage, pas pour un diagnostic qui
doit montrer l'état de CHAQUE vérification, y compris ce qui va bien).

Réutilise les briques déjà construites (4a: fenêtre du jeu/résolution,
4b: capture_and_analyze, config_manager: validate_profile, dpi_awareness:
niveau + scaling réel) -- ne réimplémente rien.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from wardenbot.calibration_engine import KIND_YOLO, capture_and_analyze
from wardenbot.calibration_wizard import region_spec_for
from wardenbot.config_manager import ConfigManager, ConfigVersionError, ProfileNotCalibratedError, ProfileValidationError, validate_profile
from wardenbot.dpi_awareness import get_current_dpi_scale_percent
from wardenbot.environment_detect import find_window_by_title_hint

# Seul scaling officiellement supporté pour l'instant (cf. README, section
# DPI -- Phase 3 se limite à 100%, 125%/150% viendront plus tard).
SUPPORTED_DPI_PERCENT = 100

SEVERITY_OK = "ok"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"


@dataclass
class DiagnosticCheck:
    name: str
    severity: str  # ok / warning / error
    detail: str
    suggestion: Optional[str] = None

    @property
    def passed(self) -> bool:
        """Un warning n'empêche PAS le bot de fonctionner (ex: pas de GPU --
        juste plus lent) -- seul severity=error est bloquant."""
        return self.severity != SEVERITY_ERROR


@dataclass
class DiagnosticReport:
    checks: list = field(default_factory=list)

    @property
    def all_ok(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list:
        return [c for c in self.checks if not c.passed]

    def as_text(self) -> str:
        icons = {SEVERITY_OK: "✓", SEVERITY_WARNING: "⚠", SEVERITY_ERROR: "✗"}
        lines = []
        for c in self.checks:
            lines.append(f"{icons[c.severity]} {c.name} : {c.detail}")
            if c.suggestion:
                lines.append(f"    -> {c.suggestion}")
        return "\n".join(lines)


def check_game_window(game_window_title_hint: str) -> DiagnosticCheck:
    title = find_window_by_title_hint(game_window_title_hint)
    if title:
        return DiagnosticCheck("Jeu détecté", SEVERITY_OK, f"Fenêtre trouvée : {title!r}")
    return DiagnosticCheck(
        "Jeu détecté", SEVERITY_ERROR, "Aucune fenêtre correspondant au jeu n'est ouverte.",
        suggestion="Lance le jeu avant de démarrer le bot.",
    )


def check_capture() -> DiagnosticCheck:
    try:
        from wardenbot.capture import ScreenCapture
        capturer = ScreenCapture()
        frame = capturer.grab({"left": 0, "top": 0, "width": 10, "height": 10})
        capturer.close()
        if frame is not None and frame.size > 0:
            return DiagnosticCheck("Capture d'écran", SEVERITY_OK, f"Capture test réussie ({frame.shape[1]}x{frame.shape[0]}px).")
        return DiagnosticCheck(
            "Capture d'écran", SEVERITY_ERROR, "La capture a retourné une image vide.",
            suggestion="Vérifie les permissions d'accès à l'écran.",
        )
    except Exception as exc:
        return DiagnosticCheck(
            "Capture d'écran", SEVERITY_ERROR, f"Impossible d'analyser l'écran : {exc}",
            suggestion="Vérifie que 'mss' est installé (pip install -r requirements.txt).",
        )


def check_yolo_loaded() -> DiagnosticCheck:
    try:
        from wardenbot.yolo_detect import _get_model
        _get_model()
        return DiagnosticCheck("Modèle YOLO", SEVERITY_OK, "Modèle chargé avec succès.")
    except Exception as exc:
        return DiagnosticCheck(
            "Modèle YOLO", SEVERITY_ERROR, f"Impossible de charger le modèle : {exc}",
            suggestion="Vérifie que runs/detect/*/weights/best.pt existe et que torch/ultralytics sont installés.",
        )


def check_gpu() -> DiagnosticCheck:
    try:
        import torch
    except ImportError:
        return DiagnosticCheck("GPU (CUDA)", SEVERITY_WARNING, "torch n'est pas installé -- CPU uniquement.", suggestion="pip install -r requirements.txt")
    if torch.cuda.is_available():
        name = torch.cuda.get_device_name(0)
        return DiagnosticCheck("GPU (CUDA)", SEVERITY_OK, f"GPU détecté : {name}")
    # Fallback CPU déjà en place (WARDENBOT_YOLO_DEVICE, cf. yolo_detect.py) --
    # le bot fonctionne quand même, juste plus lentement -- un warning, pas
    # une erreur bloquante.
    return DiagnosticCheck(
        "GPU (CUDA)", SEVERITY_WARNING, "Aucun GPU compatible CUDA détecté -- YOLO/EasyOCR tourneront sur CPU (plus lent).",
    )


def check_dpi() -> DiagnosticCheck:
    percent = get_current_dpi_scale_percent()
    if percent is None:
        return DiagnosticCheck("Scaling DPI", SEVERITY_WARNING, "Impossible de lire le scaling Windows actuel.")
    if percent == SUPPORTED_DPI_PERCENT:
        return DiagnosticCheck("Scaling DPI", SEVERITY_OK, f"Windows à {percent}% -- seul scaling officiellement supporté pour l'instant.")
    return DiagnosticCheck(
        "Scaling DPI", SEVERITY_ERROR,
        f"Windows est à {percent}% -- seul 100% est supporté dans cette version (cf. README).",
        suggestion="Repasse Windows à 100% avant d'utiliser le bot, ou attends le support complet 125%/150% (prévu plus tard).",
    )


def check_configuration(config: ConfigManager, required_regions: "Optional[list[str]]" = None) -> DiagnosticCheck:
    try:
        validate_profile(config, required_regions=required_regions)
        return DiagnosticCheck("Configuration", SEVERITY_OK, f"Profil {config.profile!r} valide et calibré.")
    except ProfileNotCalibratedError as exc:
        return DiagnosticCheck("Configuration", SEVERITY_ERROR, str(exc), suggestion="Lance l'assistant de calibration.")
    except ProfileValidationError as exc:
        return DiagnosticCheck("Configuration", SEVERITY_ERROR, str(exc))


def check_keybinds(config: ConfigManager) -> DiagnosticCheck:
    if not config.keybinds:
        return DiagnosticCheck("Touches configurées", SEVERITY_ERROR, "Aucune touche configurée pour ce personnage.", suggestion="Configure tes touches dans les paramètres.")
    return DiagnosticCheck("Touches configurées", SEVERITY_OK, f"{len(config.keybinds)} touche(s) configurée(s).")


def check_buff_bar_live(config: ConfigManager, game_detected: bool) -> Optional[DiagnosticCheck]:
    """Spot-check EN DIRECT de la barre de buffs (l'exemple donné dans la
    spec Phase 5 : "le jeu est ouvert mais la barre de buffs n'est pas
    détectée") -- ne s'exécute que si le jeu semble ouvert (sinon "0 buff
    trouvé" ne veut rien dire) et qu'une région est calibrée pour ça.
    Retourne None si non applicable (pas d'erreur dans ce cas -- juste pas
    de signal exploitable)."""
    region = config.get_region("tariq_buff_bar")
    if not game_detected or region is None:
        return None
    try:
        from wardenbot.capture import ScreenCapture
        capturer = ScreenCapture()
        spec = dict(region_spec_for("tariq", "tariq_buff_bar"))
        spec.pop("detector_kind", None)
        result = capture_and_analyze(capturer, region, kind=KIND_YOLO, **spec)
        capturer.close()
    except Exception as exc:
        return DiagnosticCheck("Barre de buffs (aperçu direct)", SEVERITY_WARNING, f"Vérification impossible : {exc}")

    if not result.success:
        return DiagnosticCheck(
            "Barre de buffs (aperçu direct)", SEVERITY_WARNING,
            f"Le jeu est ouvert mais la région n'a pas pu être analysée : {result.error}",
            suggestion="Souhaites-tu relancer la calibration ?",
        )
    return DiagnosticCheck("Barre de buffs (aperçu direct)", SEVERITY_OK, f"Analyse réussie -- {result.summary}")


def run_diagnostic(character: str, profile: "Optional[str]" = None) -> DiagnosticReport:
    """Exécute toutes les vérifications pour `character`/`profile` et
    retourne un rapport complet -- ne lève jamais (chaque check capture ses
    propres erreurs), même si la config elle-même est invalide/absente."""
    from wardenbot.game_config import CHARACTERS
    char_cfg = CHARACTERS.get(character, {})
    # .get(..., "") plutôt qu'un défaut "fellowship" en dur ICI (2026-07-26,
    # généricité du moteur) : un personnage sans cette clé ferait échouer la
    # détection de fenêtre plutôt que de mentir sur le jeu supposé.
    game_window_title_hint = char_cfg.get("game_window_title_hint", "")

    checks = [check_game_window(game_window_title_hint), check_capture(), check_yolo_loaded(), check_gpu(), check_dpi()]

    try:
        config = ConfigManager(character, profile=profile)
    except (FileNotFoundError, ConfigVersionError, KeyError) as exc:
        checks.append(DiagnosticCheck("Configuration", SEVERITY_ERROR, f"Configuration invalide : {exc}", suggestion="Réinitialiser les paramètres ?"))
        return DiagnosticReport(checks=checks)

    required_regions = char_cfg.get("required_regions")

    checks.append(check_configuration(config, required_regions))
    checks.append(check_keybinds(config))

    game_detected = checks[0].severity == SEVERITY_OK
    buff_check = check_buff_bar_live(config, game_detected)
    if buff_check is not None:
        checks.append(buff_check)

    return DiagnosticReport(checks=checks)
