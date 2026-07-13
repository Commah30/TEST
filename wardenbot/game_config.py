"""
game_config.py — registre CHARACTERS qui relie chaque personnage à son YAML,
ses templates, ses hooks et sa fabrique de state_update_fn.

Extrait de main.py (qui mélangeait ça avec le wiring du point d'entrée et
la logique de détection) -- ce fichier est PURE DONNÉE + wiring déclaratif,
aucune boucle, aucun I/O.

PHASE 1 industrialisation (2026-07-14) : KEY_MAP/REGIONS (coordonnées écran
et touches) ont déménagé dans config/default.json, chargées via
wardenbot/config_manager.py::ConfigManager -- elles n'étaient QUE de la
donnée (jamais de callable/hook), contrairement à CHARACTERS ci-dessous qui
référence des fonctions Python (pre_tick_hook, etc.) et ne peut donc pas
être sérialisé en JSON. Valeurs vérifiées identiques par diff programmatique
avant suppression (cf. commit) -- aucun changement de comportement.
"""
from __future__ import annotations

from wardenbot.hooks import meiko_post_tick, meiko_pre_tick, tariq_post_tick, tariq_pre_tick
from wardenbot.state_update import make_meiko_state_update_fn, make_tariq_state_update_fn

# --- Registre par personnage : config YAML, dossier de templates, hooks,
# mode par défaut, et fabrique de state_update_fn (None = rien à lire à
# l'écran pour l'instant). C'est CE dict qui pilote build_engine() (main.py)
# -- ajouter un personnage revient à ajouter une entrée ici + KEY_MAP/REGIONS
# ci-dessus.
CHARACTERS = {
    "meiko": {
        "config": "config/warden.yaml",
        "templates": "templates/meiko",
        "pre_tick_hook": meiko_pre_tick,
        "post_tick_hook": meiko_post_tick,
        "default_mode": "aoe",   # bascule "st"/"aoe" à la main pour l'instant
        "make_state_update_fn": make_meiko_state_update_fn,
        # state_update_fn de Meiko fait encore plusieurs grab() séparés par
        # tick (pas de capture englobante/thread détection comme Tariq) --
        # reste à 30Hz, une fréquence plus haute rendrait chaque tick plus
        # coûteux en proportion, pas l'inverse.
        "tick_hz": 30.0,
        # required_regions (2026-07-14, cf. wardenbot/config_manager.py::validate_profile) :
        # le MINIMUM sans lequel le personnage ne peut pas fonctionner
        # correctement -- buff_bar gère toute la décision du combo défensif
        # (spirited_vortex/spirited_strikes, cf. hooks.py::meiko_pre_tick).
        # Liste volontairement courte : pas une liste exhaustive de TOUTES
        # les régions, juste celles dont l'absence casserait une décision
        # centrale plutôt qu'un simple raffinement.
        "required_regions": ["buff_bar"],
    },
    "tariq": {
        "config": "config/tariq.yaml",
        "templates": "templates/tariq",
        "pre_tick_hook": tariq_pre_tick,
        "post_tick_hook": tariq_post_tick,
        "default_mode": "st",
        "make_state_update_fn": make_tariq_state_update_fn,
        # focused_wrath est off_gcd EN JEU -- doit rester listé ici pour que
        # RotationEngine ne pose pas un GCD à tort quand tariq_pre_tick la
        # force (cf. hooks.py, combo Focused Wrath -> Hammer Storm). Sans
        # ça, le GCD posé bloquerait Hammer Storm juste après, cassant
        # l'enchaînement "dans la foulée" voulu.
        "off_gcd_hook_abilities": frozenset({"focused_wrath"}),
        # state_update_fn de Tariq ne fait plus que copier la dernière
        # snapshot du thread détection (make_tariq_state_update_fn) --
        # aucune capture/matching/YOLO/OCR dans le tick de l'engine lui-même,
        # donc plus besoin d'être bridé à 30Hz. 60Hz = "boucle ultra-légère"
        # (le tick lui-même est trivial, ~qq microsecondes hors press_fn).
        "tick_hz": 60.0,
        # required_regions (2026-07-14, cf. wardenbot/config_manager.py::validate_profile) :
        # rage_bar (Fury -- gate quasi toutes les abilities) et
        # tariq_buff_bar (YOLO -- arme TOUS les procs, hammer_storm_proc en
        # tête, la source de dégâts principale) sont les deux seules dont
        # l'absence casse la rotation plutôt que de la dégrader. Les icônes
        # individuelles (heavy_strike, etc.) ne sont volontairement PAS
        # listées ici : la plupart des abilities Tariq ont skip_detection:true
        # (cf. config/tariq.yaml) et ne dépendent donc PAS d'une région pour
        # partir -- seul le voile GCD (diagnostic, cf. hooks.py) s'en sert,
        # et son absence dégrade l'observabilité, ne casse rien.
        "required_regions": ["rage_bar", "tariq_buff_bar"],
    },
}
