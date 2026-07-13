"""
test_config_manager.py — prouve, sans écran ni jeu, que ConfigManager charge
un profil fidèlement et que son contrat (get_region/get_key, erreurs de
version/personnage, sélection de profil) est correct.

PHASE 1 industrialisation (2026-07-14) : REGIONS/KEY_MAP ont déménagé de
wardenbot/game_config.py (dicts Python codés en dur) vers un fichier JSON
-- ce fichier vérifie que la migration n'a introduit AUCUNE régression de
valeur (comparaison directe avec les valeurs connues avant migration) et que
le contrat d'erreur (version incompatible, personnage absent, région absente)
est fiable pour la suite du projet.

PHASE 2 industrialisation (2026-07-14) : ce même fichier JSON est devenu le
profil "Default" (profiles/Default/config.json, déplacé pas dupliqué --
vérifié identique par diff programmatique au moment du commit). Ajout de
profiles/Ultrawide et profiles/Laptop, volontairement NON calibrés (regions
vides, calibrated:false) -- aucune coordonnée inventée sans preuve.

PHASE 2 (suite, 2026-07-14) : validate_profile() -- un profil non calibré ne
doit JAMAIS pouvoir démarrer le bot pour de vrai (échec explicite plutôt
qu'un fonctionnement "à l'aveugle" silencieux, cf. discussion). Vérifié ici
directement sur validate_profile() ET via build_engine() (dry_run permissif,
dry_run=False strict).

Lance avec : python test_config_manager.py
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from wardenbot.config_manager import (
    ConfigManager,
    ConfigVersionError,
    ProfileNotCalibratedError,
    ProfileValidationError,
    list_profiles,
    validate_profile,
)


def test_tariq_regions_match_known_pre_migration_values():
    """Valeurs figées ici comme référence -- IDENTIQUES à celles qui étaient
    codées en dur dans wardenbot/game_config.py::REGIONS['tariq'] avant la
    migration Phase 1 (vérifié par diff programmatique au moment du commit).
    Si ce test casse un jour, c'est que config/default.json a dérivé des
    valeurs de référence -- à traiter comme une régression, pas un détail."""
    config = ConfigManager("tariq")
    assert config.get_region("heavy_strike") == {"left": 1023, "top": 1239, "width": 51, "height": 48}
    assert config.get_region("hammer_storm") == {"left": 1280, "top": 1241, "width": 49, "height": 48}
    assert config.get_region("tariq_buff_bar") == {"left": 970, "top": 1280, "width": 350, "height": 150}
    assert config.get_region("hammer_storm_cast_bar") == {"left": 1123, "top": 931, "width": 296, "height": 4}
    print("ConfigManager('tariq') : régions identiques aux valeurs de référence pré-migration.")


def test_tariq_keybinds_match_known_pre_migration_values():
    config = ConfigManager("tariq")
    assert config.get_key("culling_strike") == "shift+3"
    assert config.get_key("hammer_storm_proc") == "5"
    assert config.get_key("skull_crusher_proc") == "4"   # même touche physique que skull_crusher, cf. game_config.py historique
    print("ConfigManager('tariq') : touches identiques aux valeurs de référence pré-migration.")


def test_meiko_regions_and_keybinds_match_known_pre_migration_values():
    config = ConfigManager("meiko")
    assert config.get_region("buff_bar") == {"left": 2096, "top": 1183, "width": 223, "height": 47}
    assert config.get_key("fighting_techniques") == "4"
    print("ConfigManager('meiko') : régions/touches identiques aux valeurs de référence pré-migration.")


def test_unknown_region_or_key_returns_none_not_raises():
    """Même contrat que l'ancien REGIONS[character].get(name)/KEY_MAP[character].get(name)
    -- une entrée absente ne doit jamais lever, juste retourner None (main.py
    s'appuie dessus : `if region is None: return True` / `if key is None: ...`)."""
    config = ConfigManager("tariq")
    assert config.get_region("ability_qui_nexiste_pas") is None
    assert config.get_key("ability_qui_nexiste_pas") is None
    print("Région/touche inconnue -> None, pas d'exception (contrat préservé).")


def test_incompatible_version_raises_config_version_error():
    data = {"version": 99, "characters": {"tariq": {"regions": {}, "keybinds": {}}}}
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        try:
            ConfigManager("tariq", path=path)
            assert False, "aurait dû lever ConfigVersionError"
        except ConfigVersionError:
            pass
    finally:
        path.unlink()
    print("Version de config incompatible -> ConfigVersionError levée comme attendu.")


def test_missing_character_raises_key_error():
    data = {"version": 1, "characters": {"tariq": {"regions": {}, "keybinds": {}}}}
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        try:
            ConfigManager("meiko", path=path)
            assert False, "aurait dû lever KeyError"
        except KeyError:
            pass
    finally:
        path.unlink()
    print("Personnage absent de la config -> KeyError levée comme attendu.")


def test_missing_file_raises_file_not_found_error():
    try:
        ConfigManager("tariq", path="config/ce_fichier_nexiste_pas.json")
        assert False, "aurait dû lever FileNotFoundError"
    except FileNotFoundError:
        pass
    print("Fichier de config introuvable -> FileNotFoundError levée comme attendu.")


def test_main_module_still_exposes_regions_and_key_map_for_backward_compat():
    """4 scripts de dev (build_yolo_dataset.py, detect_check.py,
    tick_timing_test.py, yolo_check.py) font `from main import REGIONS`
    (et pour un, aussi CHARACTERS) -- doit continuer à marcher sans qu'ils
    aient besoin de changer une seule ligne, cf. Phase 1 industrialisation.
    Continue de résoudre vers profiles/Default/config.json depuis la Phase 2
    (déplacement, pas duplication) -- transparent pour cet import."""
    from main import CHARACTERS, KEY_MAP, REGIONS
    assert REGIONS["tariq"]["heavy_strike"] == {"left": 1023, "top": 1239, "width": 51, "height": 48}
    assert KEY_MAP["tariq"]["culling_strike"] == "shift+3"
    assert "tariq" in CHARACTERS and "meiko" in CHARACTERS
    print("main.REGIONS / main.KEY_MAP / main.CHARACTERS toujours importables (rétrocompatibilité outillage dev).")


def test_list_profiles_finds_all_three_profiles():
    profiles = list_profiles()
    assert profiles == ["Default", "Laptop", "Ultrawide"], f"attendu les 3 profils triés, reçu {profiles}"
    print(f"list_profiles() trouve bien les 3 profils : {profiles}")


def test_default_profile_exposes_resolution_metadata():
    """Métadonnées de profil (Phase 2) -- exposées en lecture seule, jamais
    consommées par le moteur (cf. docstring config_manager.py)."""
    config = ConfigManager("tariq", profile="Default")
    assert config.resolution == "2560x1440"
    assert config.dpi_scale == 100
    assert config.game_ui_scale == 1.0
    assert config.calibrated is True
    print(f"Profil Default : resolution={config.resolution} dpi_scale={config.dpi_scale} calibrated={config.calibrated}")


def test_uncalibrated_profiles_have_empty_regions_and_calibrated_false():
    """Ultrawide/Laptop n'ont jamais été capturés en vrai -- regions VIDES
    volontairement (pas de coordonnées inventées) + calibrated:false pour
    qu'une future UI puisse avertir l'utilisateur avant usage."""
    for profile_name, expected_resolution in [("Ultrawide", "3440x1440"), ("Laptop", "1920x1080")]:
        config = ConfigManager("tariq", profile=profile_name)
        assert config.regions == {}, f"{profile_name} devrait avoir des regions vides (non calibré)"
        assert config.calibrated is False
        assert config.resolution == expected_resolution
        # Le contrat get_region()->None déjà couvert (test_unknown_region_or_key_returns_none_not_raises)
        # continue de s'appliquer : aucune region définie = aucun crash, juste "pas de template".
        assert config.get_region("heavy_strike") is None
    print("Ultrawide/Laptop : regions vides + calibrated=False, comme attendu pour des profils non calibrés.")


def test_explicit_profile_selection_loads_the_right_file():
    """ConfigManager(character, profile=X) doit charger PRÉCISÉMENT
    profiles/X/config.json, pas un autre profil ni le Default par défaut."""
    default = ConfigManager("tariq", profile="Default")
    ultrawide = ConfigManager("tariq", profile="Ultrawide")
    assert default.resolution != ultrawide.resolution
    assert str(default.path) != str(ultrawide.path)
    assert "Default" in str(default.path) and "Ultrawide" in str(ultrawide.path)
    print("La sélection explicite de profil charge bien des fichiers distincts.")


def test_validate_profile_passes_for_calibrated_default_profile():
    config = ConfigManager("tariq", profile="Default")
    validate_profile(config, required_regions=["rage_bar", "tariq_buff_bar"])  # ne doit PAS lever
    print("validate_profile() : le profil Default (calibré) passe sans erreur.")


def test_validate_profile_raises_not_calibrated_for_uncalibrated_profiles():
    """Coeur du correctif demandé (2026-07-14) : un profil non calibré ne
    doit JAMAIS pouvoir passer validate_profile(), peu importe le reste --
    échec explicite plutôt qu'un bot qui tourne 'à l'aveugle'."""
    for profile_name in ("Ultrawide", "Laptop"):
        config = ConfigManager("tariq", profile=profile_name)
        try:
            validate_profile(config, required_regions=["rage_bar", "tariq_buff_bar"])
            assert False, f"{profile_name} n'aurait pas dû passer validate_profile()"
        except ProfileNotCalibratedError:
            pass
    print("validate_profile() : Ultrawide/Laptop (non calibrés) lèvent bien ProfileNotCalibratedError.")


def test_validate_profile_raises_for_missing_required_region():
    """Un profil marqué calibré=true mais qui n'a en réalité pas toutes les
    régions requises doit quand même être rejeté -- calibrated=true seul ne
    suffit pas à garantir l'exhaustivité."""
    data = {
        "version": 1,
        "calibrated": True,
        "characters": {"tariq": {"keybinds": {"heavy_strike": "1"}, "regions": {"rage_bar": {"left": 0, "top": 0, "width": 1, "height": 1}}}},
    }
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        config = ConfigManager("tariq", path=path)
        try:
            validate_profile(config, required_regions=["rage_bar", "tariq_buff_bar"])
            assert False, "aurait dû lever ProfileValidationError (tariq_buff_bar manquante)"
        except ProfileNotCalibratedError:
            assert False, "aurait dû lever ProfileValidationError, pas ProfileNotCalibratedError (calibrated=true ici)"
        except ProfileValidationError:
            pass
    finally:
        path.unlink()
    print("validate_profile() : région requise manquante -> ProfileValidationError, même avec calibrated=true.")


def test_validate_profile_raises_for_empty_keybinds():
    data = {
        "version": 1,
        "calibrated": True,
        "characters": {"tariq": {"keybinds": {}, "regions": {}}},
    }
    path = Path(tempfile.mktemp(suffix=".json"))
    path.write_text(json.dumps(data), encoding="utf-8")
    try:
        config = ConfigManager("tariq", path=path)
        try:
            validate_profile(config)
            assert False, "aurait dû lever ProfileValidationError (aucune touche configurée)"
        except ProfileValidationError:
            pass
    finally:
        path.unlink()
    print("validate_profile() : aucune touche configurée -> ProfileValidationError.")


def test_build_engine_dry_run_stays_permissive_on_uncalibrated_profile():
    """dry_run=True = flux de dev existant, ne doit PAS être cassé par
    l'ajout de la validation stricte -- cf. discussion (2026-07-14) :
    'en développement -> comportement permissif possible'."""
    from main import build_engine
    engine = build_engine("tariq", dry_run=True, profile="Ultrawide")
    assert engine is not None
    print("build_engine(dry_run=True) reste permissif sur un profil non calibré (Ultrawide).")


def test_build_engine_real_run_rejects_uncalibrated_profile():
    """dry_run=False = 'production' -- doit échouer explicitement plutôt que
    de démarrer un bot qui ne détectera jamais rien."""
    from main import build_engine
    try:
        build_engine("tariq", dry_run=False, profile="Ultrawide")
        assert False, "aurait dû lever ProfileNotCalibratedError avant toute construction réelle"
    except ProfileNotCalibratedError:
        pass
    print("build_engine(dry_run=False) rejette bien un profil non calibré (Ultrawide) avant de démarrer.")


if __name__ == "__main__":
    test_tariq_regions_match_known_pre_migration_values()
    test_tariq_keybinds_match_known_pre_migration_values()
    test_meiko_regions_and_keybinds_match_known_pre_migration_values()
    test_unknown_region_or_key_returns_none_not_raises()
    test_incompatible_version_raises_config_version_error()
    test_missing_character_raises_key_error()
    test_missing_file_raises_file_not_found_error()
    test_main_module_still_exposes_regions_and_key_map_for_backward_compat()
    test_list_profiles_finds_all_three_profiles()
    test_default_profile_exposes_resolution_metadata()
    test_uncalibrated_profiles_have_empty_regions_and_calibrated_false()
    test_explicit_profile_selection_loads_the_right_file()
    test_validate_profile_passes_for_calibrated_default_profile()
    test_validate_profile_raises_not_calibrated_for_uncalibrated_profiles()
    test_validate_profile_raises_for_missing_required_region()
    test_validate_profile_raises_for_empty_keybinds()
    test_build_engine_dry_run_stays_permissive_on_uncalibrated_profile()
    test_build_engine_real_run_rejects_uncalibrated_profile()
    print("\nTous les tests passent.")
