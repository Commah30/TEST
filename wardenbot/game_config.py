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

from wardenbot.hooks import (
    meiko_post_tick, meiko_pre_tick, tariq_post_tick, tariq_pre_tick, vigour_post_tick, vigour_pre_tick,
    vigour_post_tick_early_game, vigour_pre_tick_early_game,
)
from wardenbot.state_update import make_meiko_state_update_fn, make_tariq_state_update_fn, make_vigour_state_update_fn

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
        # REVERT (2026-07-16) : passé à "st" plus tôt dans la session (Earth
        # Fist ne pressait jamais en AoE), puis annulé -- testé en jeu,
        # passer en ST a cassé la génération de stacks Warden of the Temple
        # AU COMPLET : en ST, Earth Fist gagne systématiquement le scan sur
        # Wind Kick, donc SEULE la boucle Earthfall (Rising Earth/Earthfist
        # Barrage) tourne. Or Earthfall n'a JAMAIS eu de détection réelle
        # branchée en jeu (gap déjà documenté plus bas, volontairement
        # reporté) -- state.resources["earthfall"] reste bloqué à 0.0 pour
        # toujours, donc hooks.py ne prend QUE la branche "build" (Rising
        # Earth), jamais "spend" (Earthfist Barrage), qui est la SEULE chose
        # qui accorde un stack de Warden dans cette boucle. Confirmé dans
        # debug.log : state.hooks["warden_stacks"] n'apparaît PAS UNE SEULE
        # FOIS sur toute la session ST -- donc plus aucun stack, plus aucun
        # forçage à 3 stacks, toute l'alternance Vortex/Strikes reste morte.
        # "aoe" reste la seule valeur qui marche tant qu'Earthfall n'a pas de
        # vraie détection écran (cf. Stormfall, qui LUI en a une -- c'est
        # pour ça que la boucle Storm/AoE spend correctement).
        # EN: REVERT (2026-07-16): switched to "st" earlier this session
        # EN: (Earth Fist never pressed in AoE), then undone -- tested in-game,
        # EN: switching to ST completely broke Warden of the Temple stack
        # EN: generation: in ST, Earth Fist always wins the scan over Wind
        # EN: Kick, so ONLY the Earthfall loop (Rising Earth/Earthfist
        # EN: Barrage) runs. But Earthfall has NEVER had real detection wired
        # EN: in-game (gap already documented below, deliberately deferred) --
        # EN: state.resources["earthfall"] stays stuck at 0.0 forever, so
        # EN: hooks.py only ever takes the "build" branch (Rising Earth),
        # EN: never "spend" (Earthfist Barrage), which is the ONLY thing that
        # EN: grants a Warden stack in that loop. Confirmed in debug.log:
        # EN: state.hooks["warden_stacks"] does NOT appear ONCE across the
        # EN: whole ST session -- so no stacks at all, no 3-stack forcing,
        # EN: the entire Vortex/Strikes alternation stays dead. "aoe" remains
        # EN: the only value that works until Earthfall gets real screen
        # EN: detection (cf. Stormfall, which DOES have one -- why the
        # EN: Storm/AoE loop spends correctly).
        "default_mode": "aoe",   # bascule "st"/"aoe" à la main pour l'instant -- PAS de hotkey réelle, cf. commentaire ci-dessus
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
    "vigour": {
        "config": "config/vigour.yaml",
        "templates": "templates/vigour",
        # EARLY GAME (avant le palier 4 des talents, 2026-07-16) -- câblé sur
        # les variantes _early_game (cf. hooks.py, commentaire de tête juste
        # avant vigour_pre_tick_early_game) plutôt que sur vigour_pre_tick/
        # vigour_post_tick (rotation Legendary Cloak, post-4e-ligne). Choix
        # STATIQUE, à repasser sur vigour_pre_tick/vigour_post_tick (juste en
        # dessous, en commentaire) une fois le palier 4 atteint -- pas de
        # bascule automatique, ça ne se produit qu'une fois dans la vie du
        # personnage.
        # EN: EARLY GAME (before talent tier 4, 2026-07-16) -- wired to the
        # EN: _early_game variants (cf. hooks.py, header comment right before
        # EN: vigour_pre_tick_early_game) rather than vigour_pre_tick/vigour_
        # EN: post_tick (Legendary Cloak rotation, post-4th-row). STATIC
        # EN: choice, switch back to vigour_pre_tick/vigour_post_tick (just
        # EN: below, commented out) once tier 4 is reached -- no automatic
        # EN: switch, this only happens once in a character's lifetime.
        "pre_tick_hook": vigour_pre_tick_early_game,
        "post_tick_hook": vigour_post_tick_early_game,
        # "pre_tick_hook": vigour_pre_tick,       # <- build Legendary Cloak, post-palier-4
        # "post_tick_hook": vigour_post_tick,     # <- build Legendary Cloak, post-palier-4
        "default_mode": "st",
        "make_state_update_fn": make_vigour_state_update_fn,
        # Phase 1 (2026-07-15, cf. discussion architecture) : lecture
        # SYNCHRONE, pas de thread de détection dédié -- cf.
        # state_update.py::make_vigour_state_update_fn pour la justification
        # (Meiko/Tariq n'en ont un que depuis qu'un problème réel l'a
        # justifié, pas dès le premier jour). 30Hz comme Meiko à l'origine.
        # EN: Phase 1 (2026-07-15, cf. architecture discussion): SYNCHRONOUS
        # EN: read, no dedicated detection thread -- cf.
        # EN: state_update.py::make_vigour_state_update_fn for the
        # EN: justification (Meiko/Tariq only got one once a real problem
        # EN: justified it, not from day one). 30Hz like Meiko originally.
        "tick_hz": 30.0,
        # required_regions (cf. wardenbot/config_manager.py::validate_profile) :
        # au moins UNE barre de vie de groupe doit être calibrée pour que le
        # personnage ait un sens -- liste volontairement courte (pas les 4
        # slots ni les régions de debuff), même principe que Meiko/Tariq.
        # EN: required_regions (cf. wardenbot/config_manager.py::
        # EN: validate_profile): at least ONE party health bar must be
        # EN: calibrated for the character to make sense -- deliberately short
        # EN: list (not all 4 slots nor the debuff regions), same principle as
        # EN: Meiko/Tariq.
        "required_regions": ["party_1_hp"],
    },
}
