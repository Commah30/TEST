"""
test_diagnostic_report.py — prouve que chaque vérification du diagnostic
(Phase 5) fonctionne réellement sur cette machine, et que run_diagnostic()
ne lève JAMAIS (même config absente/invalide) -- l'utilisateur doit toujours
obtenir un rapport lisible, pas une exception Python.

Contrairement à test_wardenbot.py/test_tariq.py, ce fichier dépend d'OpenCV/
ultralytics/torch/ctypes.windll comme test_calibration_engine.py/
test_dpi_awareness.py -- inhérent à ce qu'il teste (les vraies vérifications
système), pas une régression de philosophie du projet.

Lance avec : python test_diagnostic_report.py
"""
from __future__ import annotations

from wardenbot.config_manager import ConfigManager
from wardenbot.diagnostic_report import (
    SEVERITY_ERROR,
    SEVERITY_OK,
    check_buff_bar_live,
    check_capture,
    check_configuration,
    check_dpi,
    check_game_window,
    check_gpu,
    check_keybinds,
    check_yolo_loaded,
    run_diagnostic,
)


def test_check_game_window_never_raises_regardless_of_whether_game_is_open():
    """Ne présuppose PAS que le jeu est ouvert (dépend de la machine qui
    lance le test) -- vérifie juste que le check répond, avec le bon
    niveau de sévérité dans les deux cas. check_game_window(hint) prend
    désormais un hint explicite (2026-07-26, généricité du moteur -- plus de
    défaut "fellowship" en dur, cf. wardenbot/diagnostics.py)."""
    check = check_game_window("fellowship")
    assert check.severity in (SEVERITY_OK, SEVERITY_ERROR)
    if check.severity == SEVERITY_ERROR:
        assert check.suggestion is not None
    print(f"check_game_window('fellowship') : {check.severity} -- {check.detail}")


def test_check_capture_succeeds_on_a_real_windows_machine():
    check = check_capture()
    assert check.severity == SEVERITY_OK, f"attendu une capture réussie sur cette machine, reçu : {check.detail}"
    print(f"check_capture() : {check.detail}")


def test_check_yolo_loaded_succeeds_with_the_trained_model_present():
    check = check_yolo_loaded()
    assert check.severity == SEVERITY_OK, f"attendu un chargement réussi (runs/detect/*/weights/best.pt existe), reçu : {check.detail}"
    print(f"check_yolo_loaded() : {check.detail}")


def test_check_gpu_reports_a_real_status_without_crashing():
    """Ne présuppose pas la présence d'un GPU (portabilité du test) -- juste
    que warning/ok sont les seules issues possibles, jamais error (un GPU
    absent dégrade, ne bloque jamais, cf. fallback CPU déjà en place)."""
    check = check_gpu()
    assert check.severity in (SEVERITY_OK, "warning")
    print(f"check_gpu() : {check.severity} -- {check.detail}")


def test_check_dpi_reflects_the_real_current_scaling():
    check = check_dpi()
    print(f"check_dpi() : {check.severity} -- {check.detail}")
    # Pas d'assertion stricte sur la valeur (dépend de la machine qui lance
    # le test) -- juste que ça ne lève jamais et donne un niveau cohérent.
    assert check.severity in (SEVERITY_OK, SEVERITY_ERROR, "warning")


def test_check_configuration_passes_for_the_calibrated_default_profile():
    config = ConfigManager("tariq", profile="Default")
    check = check_configuration(config, required_regions=["rage_bar", "tariq_buff_bar"])
    assert check.severity == SEVERITY_OK
    print(f"check_configuration() [Default] : {check.detail}")


def test_check_configuration_fails_clearly_for_uncalibrated_ultrawide():
    config = ConfigManager("tariq", profile="Ultrawide")
    check = check_configuration(config, required_regions=["rage_bar", "tariq_buff_bar"])
    assert check.severity == SEVERITY_ERROR
    assert check.suggestion is not None
    print(f"check_configuration() [Ultrawide, non calibré] : {check.detail} -> {check.suggestion}")


def test_check_keybinds_passes_for_default_and_fails_for_empty():
    default_config = ConfigManager("tariq", profile="Default")
    assert check_keybinds(default_config).severity == SEVERITY_OK

    class _FakeEmptyKeybindsConfig:
        keybinds = {}

    check = check_keybinds(_FakeEmptyKeybindsConfig())
    assert check.severity == SEVERITY_ERROR
    print("check_keybinds() : Default=ok, config sans touche=error, comme attendu.")


def test_check_buff_bar_live_returns_none_when_game_not_detected():
    """Pas la peine d'analyser 'aucun buff détecté' si le jeu n'est même
    pas ouvert -- ce ne serait pas un signal exploitable."""
    config = ConfigManager("tariq", profile="Default")
    result = check_buff_bar_live(config, game_detected=False)
    assert result is None
    print("check_buff_bar_live() : game_detected=False -> None (pas de check inutile).")


def test_check_buff_bar_live_returns_none_when_region_missing():
    config = ConfigManager("tariq", profile="Ultrawide")  # regions vides
    result = check_buff_bar_live(config, game_detected=True)
    assert result is None
    print("check_buff_bar_live() : région non calibrée -> None (pas d'erreur fabriquée).")


def test_run_diagnostic_never_raises_for_a_valid_profile():
    report = run_diagnostic("tariq", profile="Default")
    names = [c.name for c in report.checks]
    assert "Jeu détecté" in names
    assert "Capture d'écran" in names
    assert "Modèle YOLO" in names
    assert "GPU (CUDA)" in names
    assert "Scaling DPI" in names
    assert "Configuration" in names
    assert "Touches configurées" in names
    print(f"run_diagnostic('tariq', 'Default') -- {len(report.checks)} vérifications, all_ok={report.all_ok} :")
    print(report.as_text())


def test_run_diagnostic_never_raises_for_an_uncalibrated_profile():
    report = run_diagnostic("tariq", profile="Ultrawide")
    assert not report.all_ok, "Ultrawide n'est pas calibré -- au moins un check doit échouer"
    config_check = next(c for c in report.checks if c.name == "Configuration")
    assert config_check.severity == SEVERITY_ERROR
    print(f"run_diagnostic('tariq', 'Ultrawide') -- all_ok={report.all_ok}, {len(report.failed_checks)} échec(s).")


def test_run_diagnostic_never_raises_for_a_nonexistent_profile():
    """Un profil qui n'existe pas du tout (faute de frappe utilisateur,
    dossier supprimé...) ne doit JAMAIS faire planter le diagnostic --
    juste un rapport avec 'Configuration' en erreur."""
    report = run_diagnostic("tariq", profile="CeProfilNexistePas")
    assert not report.all_ok
    config_check = next(c for c in report.checks if c.name == "Configuration")
    assert config_check.severity == SEVERITY_ERROR
    print(f"run_diagnostic() avec un profil inexistant : pas de crash, Configuration=error ({config_check.detail}).")


if __name__ == "__main__":
    test_check_game_window_never_raises_regardless_of_whether_game_is_open()
    test_check_capture_succeeds_on_a_real_windows_machine()
    test_check_yolo_loaded_succeeds_with_the_trained_model_present()
    test_check_gpu_reports_a_real_status_without_crashing()
    test_check_dpi_reflects_the_real_current_scaling()
    test_check_configuration_passes_for_the_calibrated_default_profile()
    test_check_configuration_fails_clearly_for_uncalibrated_ultrawide()
    test_check_keybinds_passes_for_default_and_fails_for_empty()
    test_check_buff_bar_live_returns_none_when_game_not_detected()
    test_check_buff_bar_live_returns_none_when_region_missing()
    test_run_diagnostic_never_raises_for_a_valid_profile()
    test_run_diagnostic_never_raises_for_an_uncalibrated_profile()
    test_run_diagnostic_never_raises_for_a_nonexistent_profile()
    print("\nTous les tests passent.")
