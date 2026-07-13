"""
test_calibration_wizard.py — prouve que les parties PURES de l'assistant de
calibration (mapping région -> type de détecteur, sauvegarde de profil)
sont correctes, sans jamais déclencher la boucle interactive réelle
(select_region_interactively()/show_preview_and_confirm() bloquent sur une
vraie interaction souris/clic de bouton -- volontairement PAS appelées ici,
cf. test_region_selector.py pour le même principe).

Lance avec : python test_calibration_wizard.py
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import wardenbot.calibration_wizard as calibration_wizard
from wardenbot.calibration_engine import KIND_BAR, KIND_TEMPLATE, KIND_YOLO
from wardenbot.calibration_wizard import region_spec_for, regions_to_calibrate, save_profile


def test_region_spec_for_tariq_buff_bar_uses_yolo():
    spec = region_spec_for("tariq", "tariq_buff_bar")
    assert spec["detector_kind"] == KIND_YOLO
    print(f"tariq_buff_bar -> {spec}")


def test_region_spec_for_rage_bar_uses_bar_with_the_real_fill_color():
    from wardenbot.state_update import RAGE_BAR_FILL_BGR
    spec = region_spec_for("tariq", "rage_bar")
    assert spec["detector_kind"] == KIND_BAR
    assert spec["fill_bgr"] == RAGE_BAR_FILL_BGR, "doit réutiliser la MÊME constante que state_update.py, pas une copie"
    print(f"rage_bar -> {spec}")


def test_region_spec_for_ability_icon_defaults_to_template_matching():
    spec = region_spec_for("tariq", "heavy_strike")
    assert spec["detector_kind"] == KIND_TEMPLATE
    assert spec["template_name"] == "heavy_strike"
    print(f"heavy_strike (pas de cas spécial) -> {spec}")


def test_regions_to_calibrate_matches_the_real_default_profile():
    """Reprend les clés du profil Default plutôt qu'une liste maintenue à la
    main -- doit inclure toutes les régions réellement utilisées par Tariq."""
    names = regions_to_calibrate("tariq")
    assert "rage_bar" in names
    assert "tariq_buff_bar" in names
    assert "heavy_strike" in names
    assert len(names) == 13, f"attendu les 13 régions connues de Default, reçu {len(names)} : {names}"
    print(f"{len(names)} régions à calibrer pour tariq : {names}")


def _with_temp_profiles_dir():
    """Redirige temporairement PROFILES_DIR vers un dossier jetable -- ne
    JAMAIS écrire de données de test dans le vrai profiles/ (leçon retenue
    plus tôt dans ce projet après avoir écrasé un vrai fichier par erreur)."""
    tmp_dir = Path(tempfile.mkdtemp())
    original = calibration_wizard.PROFILES_DIR
    calibration_wizard.PROFILES_DIR = tmp_dir
    return tmp_dir, original


def test_save_profile_creates_a_new_profile_from_scratch():
    tmp_dir, original = _with_temp_profiles_dir()
    try:
        path = save_profile(
            "TestProfile", "tariq",
            regions={"heavy_strike": {"left": 1, "top": 2, "width": 3, "height": 4}},
            resolution="1920x1080", dpi_scale=100,
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["calibrated"] is True
        assert data["resolution"] == "1920x1080"
        assert data["characters"]["tariq"]["regions"]["heavy_strike"] == {"left": 1, "top": 2, "width": 3, "height": 4}
        assert data["characters"]["tariq"]["keybinds"] == {}, "aucun keybind préexistant -- doit rester vide, pas planter"
        print(f"Nouveau profil créé de zéro : {data}")
    finally:
        calibration_wizard.PROFILES_DIR = original
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_profile_preserves_existing_keybinds_and_other_character():
    """Calibrer les régions de Tariq sur un profil qui a DÉJÀ des keybinds
    (cf. Phase 2 : Ultrawide/Laptop créés avec des keybinds par défaut) et/ou
    une calibration Meiko existante ne doit RIEN écraser d'autre que les
    régions de Tariq."""
    tmp_dir, original = _with_temp_profiles_dir()
    try:
        profile_dir = tmp_dir / "Ultrawide"
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.json").write_text(json.dumps({
            "version": 1,
            "resolution": "3440x1440",
            "calibrated": False,
            "characters": {
                "tariq": {"keybinds": {"heavy_strike": "1"}, "regions": {}},
                "meiko": {"keybinds": {"spirit_palm": "1"}, "regions": {"buff_bar": {"left": 9, "top": 9, "width": 9, "height": 9}}},
            },
        }), encoding="utf-8")

        path = save_profile("Ultrawide", "tariq", regions={"heavy_strike": {"left": 10, "top": 20, "width": 30, "height": 40}})
        data = json.loads(path.read_text(encoding="utf-8"))

        assert data["characters"]["tariq"]["keybinds"] == {"heavy_strike": "1"}, "keybinds tariq existants préservés"
        assert data["characters"]["tariq"]["regions"]["heavy_strike"] == {"left": 10, "top": 20, "width": 30, "height": 40}
        assert data["characters"]["meiko"]["keybinds"] == {"spirit_palm": "1"}, "meiko ne doit PAS être touché"
        assert data["characters"]["meiko"]["regions"]["buff_bar"]["left"] == 9
        assert data["calibrated"] is True
        assert data["resolution"] == "3440x1440", "resolution existante préservée (non fournie à cet appel)"
        print("save_profile() : keybinds existants + autre personnage préservés, seules les régions tariq mises à jour.")
    finally:
        calibration_wizard.PROFILES_DIR = original
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_preview_dialog_widgets_construct_without_error():
    """Ne teste PAS l'interaction utilisateur (nécessite un vrai clic sur
    Valider/Refaire) -- vérifie juste que les widgets tkinter utilisés par
    show_preview_and_confirm() (Label, Frame, Button, PhotoImage à partir
    d'un PNG encodé en mémoire via cv2.imencode) se construisent sans
    erreur d'API, avant de tout détruire immédiatement -- jamais
    root.mainloop()."""
    import cv2
    import numpy as np

    from wardenbot.calibration_wizard import tk_root_for_preview, _make_label, _make_frame, _make_button, _make_photo_image

    crop = np.zeros((48, 51, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", crop)
    assert ok

    root = tk_root_for_preview()
    try:
        photo = _make_photo_image(encoded.tobytes())
        img_label = _make_label(root, image=photo)
        img_label.image = photo
        img_label.pack()
        _make_label(root, text="score=0.94 -- OK").pack()
        frame = _make_frame(root)
        frame.pack()
        _make_button(frame, text="✓ Valider", command=lambda: None, bg="#2ecc71").pack(side="left")
        _make_button(frame, text="↺ Refaire", command=lambda: None, bg="#e74c3c").pack(side="left")
    finally:
        root.destroy()
    print("Dialogue d'aperçu (Label/Frame/Button/PhotoImage depuis un PNG en mémoire) : construction sans erreur.")


if __name__ == "__main__":
    test_region_spec_for_tariq_buff_bar_uses_yolo()
    test_region_spec_for_rage_bar_uses_bar_with_the_real_fill_color()
    test_region_spec_for_ability_icon_defaults_to_template_matching()
    test_regions_to_calibrate_matches_the_real_default_profile()
    test_save_profile_creates_a_new_profile_from_scratch()
    test_save_profile_preserves_existing_keybinds_and_other_character()
    test_preview_dialog_widgets_construct_without_error()
    print("\nTous les tests passent.")
