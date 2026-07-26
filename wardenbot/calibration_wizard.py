"""
calibration_wizard.py — assemble les briques des Phases 4a-4c en un
assistant complet (Phase 4d de l'industrialisation, 2026-07-14) :

  pour chaque région requise par le personnage :
    select_region_interactively() (4c, overlay)
      -> capture_and_analyze() (4b, crop + résultat du détecteur)
        -> show_preview_and_confirm() (aperçu + boutons Valider/Refaire)
  puis save_profile() écrit automatiquement profiles/<nom>/config.json.

Découpage testabilité (même principe que 4c) :
  - region_spec_for() / save_profile() : PURE logique, testables sans écran.
  - show_preview_and_confirm() / run_calibration_wizard() : bloquent sur
    root.mainloop() jusqu'à une VRAIE interaction souris -- jamais appelées
    automatiquement dans les tests, à essayer en conditions réelles.

LIMITE CONNUE (documentée, pas cachée) : le template matching (icônes
d'ability/debuff) n'est PAS invariant à l'échelle (cf. wardenbot/matcher.py
et la confusion hammer_storm/skull_crusher déjà rencontrée sur ce projet).
Calibrer un NOUVEAU profil (résolution différente de Default) avec les
templates PNG existants (capturés à 2560x1440) donnera probablement un score
bas -- ce n'est pas un bug de ce module, c'est un signal que ces templates
devront aussi être recapturés à la nouvelle échelle (capture_helper.py snap),
un chantier séparé non couvert ici.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import cv2

from wardenbot.calibration_engine import CalibrationResult, KIND_BAR, KIND_TEMPLATE, KIND_YOLO, capture_and_analyze
from wardenbot.capture import ScreenCapture
from wardenbot.config_manager import PROFILES_DIR, SUPPORTED_CONFIG_VERSION, ConfigManager
from wardenbot.matcher import TemplateMatcher
from wardenbot.region_selector import select_region_interactively
from wardenbot.state_update import HAMMER_STORM_CAST_BAR_FILL_BGR, RAGE_BAR_FILL_BGR
from wardenbot.yolo_detect import TARIQ_YOLO_WEIGHTS_PATH

# Régions Tariq dont l'aperçu de calibration N'EST PAS un simple template
# match (cas par défaut) -- réutilise les MÊMES constantes que
# state_update.py, jamais dupliquées, pour ne jamais diverger de ce que le
# moteur utilise réellement en jeu.
_TARIQ_SPECIAL_REGIONS: dict = {
    # weights_path OBLIGATOIRE désormais (2026-07-26, généricité du moteur,
    # cf. wardenbot/yolo_detect.py -- analyze_yolo_crop ne défaute plus sur
    # les poids Tariq en interne).
    "tariq_buff_bar": {"detector_kind": KIND_YOLO, "conf": 0.4, "weights_path": TARIQ_YOLO_WEIGHTS_PATH},
    "rage_bar": {"detector_kind": KIND_BAR, "fill_bgr": RAGE_BAR_FILL_BGR, "direction": "ltr", "tolerance": 50, "max_gap": 6},
    "hammer_storm_cast_bar": {"detector_kind": KIND_BAR, "fill_bgr": HAMMER_STORM_CAST_BAR_FILL_BGR, "direction": "ltr", "tolerance": 60},
}
DEFAULT_TEMPLATE_THRESHOLD = 0.70


def region_spec_for(character: str, region_name: str) -> dict:
    """Quel détecteur utiliser pour l'aperçu live de CETTE région, et avec
    quels paramètres -- kwargs prêts à passer à capture_and_analyze() (cf.
    wardenbot/calibration_engine.py). Par défaut : template matching
    (le cas le plus courant, une icône d'ability/debuff), template_name =
    le nom de la région lui-même (convention déjà utilisée partout dans ce
    projet, cf. config/tariq.yaml/Ability.template_name())."""
    if character == "tariq" and region_name in _TARIQ_SPECIAL_REGIONS:
        return dict(_TARIQ_SPECIAL_REGIONS[region_name])
    return {"detector_kind": KIND_TEMPLATE, "template_name": region_name, "threshold": DEFAULT_TEMPLATE_THRESHOLD}


def regions_to_calibrate(character: str, reference_profile: str = "Default") -> list[str]:
    """Liste des noms de région à calibrer pour `character` -- reprend les
    clés déjà définies dans le profil Default plutôt que de maintenir une
    2e liste à la main (une seule source de vérité pour "quelles régions ce
    personnage utilise")."""
    reference = ConfigManager(character, profile=reference_profile)
    return sorted(reference.regions.keys())


def save_profile(
    profile_name: str,
    character: str,
    regions: dict,
    resolution: Optional[str] = None,
    dpi_scale: Optional[float] = None,
    game_ui_scale: float = 1.0,
) -> Path:
    """Écrit/complète profiles/<profile_name>/config.json avec les régions
    fraîchement calibrées pour `character`, marque calibrated=true. Si le
    profil existe déjà (cf. Phase 2, Ultrawide/Laptop créés avec des
    keybinds par défaut mais regions vides) : PRÉSERVE les keybinds
    existants et les données de l'AUTRE personnage -- calibrer Tariq ne doit
    jamais effacer une calibration Meiko déjà faite sur ce même profil."""
    path = PROFILES_DIR / profile_name / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"version": SUPPORTED_CONFIG_VERSION, "characters": {}}

    if resolution is not None:
        data["resolution"] = resolution
    if dpi_scale is not None:
        data["dpi_scale"] = dpi_scale
    data["game_ui_scale"] = game_ui_scale
    data["calibrated"] = True

    data.setdefault("characters", {})
    existing_char_cfg = data["characters"].get(character, {})
    data["characters"][character] = {
        "keybinds": existing_char_cfg.get("keybinds", {}),  # préservés, cette étape ne calibre QUE les régions
        "regions": regions,
    }

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def show_preview_and_confirm(result: CalibrationResult, region_name: str) -> bool:
    """Fenêtre tkinter : affiche le crop capturé + le résumé du détecteur,
    avec deux boutons Valider/Refaire (cf. spec Phase 4). BLOQUANT
    (root.mainloop()) -- jamais appelée automatiquement dans les tests.

    PNG encodé en mémoire (cv2.imencode) puis chargé via tk.PhotoImage(data=...)
    -- Tk 8.6+ décode le PNG nativement, zéro dépendance supplémentaire
    (pas de Pillow, cf. évaluation déjà faite ce projet -- rejetée faute de
    bénéfice réel)."""
    root = tk_root_for_preview()
    root.title(f"Calibration -- {region_name}")
    root.attributes("-topmost", True)

    choice = {"validated": False}
    photo = None  # référence gardée pour éviter le garbage-collect avant l'affichage

    if result.crop is not None and result.crop.size > 0:
        ok, encoded = cv2.imencode(".png", result.crop)
        if ok:
            photo = _make_photo_image(encoded.tobytes())
            img_label = _make_label(root, image=photo)
            img_label.image = photo
            img_label.pack(pady=10)

    summary_text = result.summary if result.success else f"Erreur : {result.error}"
    _make_label(root, text=summary_text, font=("Segoe UI", 12)).pack(pady=5)

    def on_validate():
        choice["validated"] = True
        root.destroy()

    def on_redo():
        choice["validated"] = False
        root.destroy()

    button_frame = _make_frame(root)
    button_frame.pack(pady=10)
    _make_button(button_frame, text="✓ Valider", command=on_validate, bg="#2ecc71").pack(side="left", padx=10)
    _make_button(button_frame, text="↺ Refaire", command=on_redo, bg="#e74c3c").pack(side="left", padx=10)

    root.mainloop()
    return choice["validated"]


# Petits alias vers tkinter -- indirection volontaire pour que
# show_preview_and_confirm() reste lisible ET que les tests puissent, si un
# jour nécessaire, injecter un faux toolkit sans toucher à la logique.
def tk_root_for_preview():
    import tkinter as tk
    return tk.Tk()


def _make_label(parent, **kwargs):
    import tkinter as tk
    return tk.Label(parent, **kwargs)


def _make_frame(parent, **kwargs):
    import tkinter as tk
    return tk.Frame(parent, **kwargs)


def _make_button(parent, **kwargs):
    import tkinter as tk
    return tk.Button(parent, **kwargs)


def _make_photo_image(png_bytes: bytes):
    import tkinter as tk
    return tk.PhotoImage(data=png_bytes)


def run_calibration_wizard(character: str, profile_name: str) -> Optional[Path]:
    """Boucle complète : pour chaque région requise par `character`,
    sélection (overlay) -> aperçu+détecteur -> Valider/Refaire ; sauvegarde
    automatique du profil à la fin. Retourne le chemin du profil sauvegardé,
    ou None si l'utilisateur annule (Échap pendant une sélection)."""
    capturer = ScreenCapture()
    matcher = TemplateMatcher(f"templates/{character}")

    collected_regions: dict = {}
    for region_name in regions_to_calibrate(character):
        while True:
            region = select_region_interactively(instructions=f"Sélectionne la région : {region_name}")
            if region is None:
                print(f"[calibration] Annulé pendant la sélection de {region_name!r}.")
                return None

            spec = dict(region_spec_for(character, region_name))
            kind = spec.pop("detector_kind")
            if kind == KIND_TEMPLATE:
                spec["matcher"] = matcher
            result = capture_and_analyze(capturer, region, kind=kind, **spec)

            if show_preview_and_confirm(result, region_name):
                collected_regions[region_name] = region
                break
            # sinon : refaire cette région, la boucle recommence

    saved_path = save_profile(profile_name, character, collected_regions)
    print(f"[calibration] Profil sauvegardé : {saved_path}")
    return saved_path
