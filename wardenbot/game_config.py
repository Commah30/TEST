"""
game_config.py — configuration par personnage : touches (KEY_MAP), régions
écran (REGIONS), et le registre CHARACTERS qui relie chaque personnage à
son YAML, ses templates, ses hooks et sa fabrique de state_update_fn.

Extrait de main.py (qui mélangeait ça avec le wiring du point d'entrée et
la logique de détection) -- ce fichier est PURE DONNÉE + wiring déclaratif,
aucune boucle, aucun I/O.

KEY_MAP et REGIONS sont édités À LA MAIN par l'utilisateur au fur et à mesure des
captures -- ne JAMAIS écraser ce fichier en entier, toujours diff/merge.
"""
from __future__ import annotations

from wardenbot.hooks import meiko_post_tick, meiko_pre_tick, tariq_post_tick, tariq_pre_tick
from wardenbot.state_update import make_meiko_state_update_fn, make_tariq_state_update_fn

# --- À REMPLIR : nom logique -> touche réelle en jeu, PAR PERSONNAGE -------
KEY_MAP = {
    "meiko": {
        "stone_shield": "5",
        "shatter_earth": "6",
        "earth_fist": "3",
        "wind_kick": "2",
        "spirit_palm": "1",
        "earth_fist_2": "3",     # même touche physique que earth_fist
        "wind_kick_2": "2",      # même touche physique que wind_kick
        "spirit_palm_2": "1",    # même touche physique que spirit_palm
        "fighting_techniques": "4",
        "serenity": "7",
    },
    "tariq": {
        "heavy_strike": "1",
        "wild_swing": "2",
        "face_breaker": "3",
        "skull_crusher": "4",
        "hammer_storm_proc": "5",   # nom de l'ability = hammer_storm_proc (template aliasé "hammer_storm", cf. config/tariq.yaml -- mais KEY_MAP se base sur le nom d'ability, pas le template)
        "thunder_call": "6",
        "chain_lightning": "7",
        "culling_strike": "shift+3",   # combinaison -- cf. InputSimulator.press() (wardenbot/input_sim.py)
        "focused_wrath": "q",

        # Même touche physique que skull_crusher -- pas explicitement donné,
        # suppose la même convention que les paires *_2 chez Meiko (variante
        # empowered = même bouton). À CONFIRMER.
        "skull_crusher_proc": "4",
    },
}

# --- À REMPLIR : nom logique -> région écran (icône à surveiller), PAR
# PERSONNAGE -----------------------------------------------------------------
REGIONS = {
    "meiko": {
        "spirit_palm": {'left': 1032, 'top': 1331, 'width': 51, 'height': 50},
        "wind_kick": {'left': 1095, 'top': 1329, 'width': 52, 'height': 53},
        "earth_fist": {'left': 1161, 'top': 1332, 'width': 45, 'height': 47},
        "fighting_techniques": {'left': 1224, 'top': 1329, 'width': 48, 'height': 50},
        "stormfall_lit": {'left': 572, 'top': 864, 'width': 10, 'height': 10},

        # Zone LARGE englobant tout l'espace où spirited_vortex / spirited_strikes /
        # warden_of_the_temple_3 peuvent apparaître. Ces icônes ne sont PAS à
        # position fixe (la barre de buffs se réorganise selon ce qui est actif),
        # donc on ne capture plus une petite zone par icône : on capture une zone
        # large UNE fois par tick, et cv2.matchTemplate (via TemplateMatcher, déjà
        # une recherche par balayage) trouve chaque template dedans, où qu'il soit.
        # PLACEHOLDER -- à recalculer avec `python capture_helper.py mark` : coin
        # haut-gauche = position la PLUS À GAUCHE possible d'une de ces icônes,
        # coin bas-droite = position la PLUS À DROITE/BAS possible. Prévois une
        # marge généreuse.
        "buff_bar": {'left': 2096, 'top': 1183, 'width': 223, 'height': 47},
    },
    "tariq": {
        "heavy_strike": {'left': 1023, 'top': 1239, 'width': 51, 'height': 48},
        "wild_swing": {'left': 1089, 'top': 1240, 'width': 49, 'height': 51},
        "face_breaker": {'left': 1152, 'top': 1240, 'width': 51, 'height': 49},
        "skull_crusher": {'left': 1217, 'top': 1240, 'width': 50, 'height': 50},
        "hammer_storm": {'left': 1280, 'top': 1241, 'width': 49, 'height': 48},
        "thunder_call": {'left': 1344, 'top': 1240, 'width': 50, 'height': 50},
        "chain_lightning": {'left': 1408, 'top': 1239, 'width': 49, 'height': 51},
        "focused_wrath": {'left': 1216, 'top': 1175, 'width': 50, 'height': 53},
        "culling_strike": {'left': 1152, 'top': 1176, 'width': 51, 'height': 50},

        "rage_bar": {'left': 446, 'top': 924, 'width': 141, 'height': 5},

        # Zone LARGE près de la barre d'abilities où apparaissent les buffs
        # hammer_storm_empowered / skull_crusher_empowered (badge x2 +
        # compte à rebours) -- l'utilisateur a indiqué {1166,1322,272,48}, élargie
        # de quelques px (272->280, 48->60) car le template skull_crusher.png
        # fait 50px de haut, plus grand que la zone d'origine -- sans marge
        # TemplateMatcher.confidence() retourne 0.0 par sécurité (région
        # plus petite que le template). Comme pour buff_bar chez Meiko :
        # zone large + recherche dedans, cette zone se réorganise aussi
        # selon ce qui est actif.
        "tariq_buff_bar": {'left': 1162, 'top': 1316, 'width': 280, 'height': 60},

        # PLUS UTILISÉE (2026-07-12) : badge de stack de l'icône
        # hammer_storm_buff, DANS tariq_buff_bar. Le comptage de stacks a été
        # retiré (l'utilisateur a confirmé que ce badge n'affiche jamais "2" en jeu
        # réel -- chaque proc doit être consommé dès qu'il apparaît, cf.
        # wardenbot/hooks.py). Coordonnées gardées (calibrées sur un vrai
        # clip) au cas où un futur besoin de lecture de badge se présente.
        "hammer_storm_stack_badge": {'left': 1182, 'top': 1354, 'width': 28, 'height': 20},

        # Bande ÉTROITE (4px) juste sous le bord haut de la barre complète
        # (mesurée à left=1123, top=930, width=296, height=20 -- identique
        # aux coordonnées relevées à la main par l'utilisateur via capture_helper.py
        # mark). Volontairement PAS la barre entière : le label "Hammer
        # Storm" traverse pile sa ligne centrale et casse le scan (vérifié
        # sur un vrai clip Game Bar -- lisait 0% partout tant qu'on scannait
        # la hauteur complète). Cette bande haute reste hors texte.
        "hammer_storm_cast_bar": {'left': 1123, 'top': 931, 'width': 296, 'height': 4},
    },
}

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
    },
}
