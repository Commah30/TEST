"""
main.py — point d'entrée. Assemble capture d'écran, template matching,
moteur de rotation, hooks par personnage et simulation de touches.

Deux personnages supportés (cf. CHARACTERS plus bas) : "meiko" et "tariq".
Au lancement, l'exe/le script demande lequel piloter -- chacun a son propre
dossier de templates (templates/<perso>/), son propre YAML (config/<perso>.yaml)
et son propre KEY_MAP/REGIONS (nichés par perso ci-dessous).

SETUP AVANT DE LANCER EN VRAI (dry_run=False) :
  1. pip install -r requirements.txt
  2. Remplis KEY_MAP[<perso>] ci-dessous avec tes vrais binds.
  3. Capture les templates PNG (une icône par ability + les debuffs/badges
     nécessaires) dans templates/<perso>/, nommés exactement comme dans
     KEY_MAP[<perso>].
  4. Remplis REGIONS[<perso>] avec les coordonnées (left, top, width, height)
     de chaque icône à l'écran (clic droit sur ta capture d'écran / un outil
     comme ShareX pour lire les coordonnées, ou `python capture_helper.py mark`).
  5. Lance d'abord avec dry_run=True pour vérifier dans la console que les
     bonnes abilities sortent au bon moment, AVANT de laisser le bot presser
     de vraies touches en jeu.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# La console Windows par défaut est en cp1252 -- EasyOCR (utilisé par
# wardenbot/ocr.py) imprime une barre de progression Unicode (blocs "█") au
# tout premier lancement, le temps de télécharger ses poids de modèle.
# Sans ce reconfigure, ce print plante avec UnicodeEncodeError et le bot ne
# démarre jamais -- vécu en testant l'intégration EasyOCR sur cette machine.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from wardenbot.bar_reader import read_fill_pct
from wardenbot.capture import ScreenCapture
from wardenbot.matcher import TemplateMatcher
from wardenbot.engine import RotationEngine, State, load_abilities
from wardenbot.hooks import meiko_pre_tick, meiko_post_tick, tariq_pre_tick, tariq_post_tick
from wardenbot.input_sim import InputSimulator
from wardenbot.yolo_detect import detect

DEBUG_LOG_PATH = Path("debug.log")

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


def make_meiko_state_update_fn(capturer: ScreenCapture, matcher: TemplateMatcher, regions: dict):
    """
    Lit à l'écran les buffs/debuffs/ressources détectables par template et
    remplit state.buffs/state.debuffs/state.resources -- appelé en tout
    premier à chaque tick (cf. RotationEngine.state_update_fn). C'est ce
    câblage qui manquait pour que meiko_pre_tick voie l'état RÉEL des buffs
    Spirited Vortex/Strikes plutôt qu'un défaut à False en permanence.
    """
    def state_update_fn(state: State) -> None:
        # Une seule capture de la zone large, réutilisée pour chercher les 3
        # templates dedans -- au lieu d'une capture étroite par icône (fragile
        # dès que la barre de buffs se réorganise et décale les icônes).
        buff_frame = capturer.grab(regions["buff_bar"])
        state.buffs["spirited_vortex"] = matcher.is_match(buff_frame, "spirited_vortex")
        state.buffs["spirited_strikes"] = matcher.is_match(buff_frame, "spirited_strikes")
        state.debuffs["warden_of_the_temple_3"] = matcher.is_match(buff_frame, "warden_of_the_temple_3")

        state.resources["stormfall"] = 1.0 if matcher.is_match(
            capturer.grab(regions["stormfall_lit"]), "stormfall_lit"
        ) else 0.0
        # earthfall_lit pas encore capturé -> state.resources["earthfall"] reste
        # à son défaut (0.0 via .get()) tant que le template n'existe pas.

    return state_update_fn


# Mesurés par script sur un clip Game Bar (frame quasi pleine, ~95%) --
# cf. ffmpeg_hammerstorm/023.png. Bounding box calculée par script tombe sur
# left=1123, top=930 -- identique aux coordonnées relevées à la main par
# l'utilisateur via `capture_helper.py mark`, donc REGIONS ci-dessous est fiable.
# tolerance=60 (au lieu du défaut 30 de read_fill_pct) : la barre a un
# biseau/glow anti-aliasé de quelques pixels près de ses bords qui s'écarte
# nettement de la couleur pure -- validé image par image sur le vrai clip
# (avec tolerance=30 la lecture retombait à 0% partout, y compris sur des
# frames clairement bien remplies à l'oeil).
HAMMER_STORM_CAST_BAR_FILL_BGR = (8, 175, 205)   # BGR, or/jaune
HAMMER_STORM_CASTING_THRESHOLD = 0.02   # en dessous, on considère la canalisation terminée

# BUG TROUVÉ EN JEU (2026-07-12) : "le bot est à la ramasse" -- yolo_detect.detect()
# était appelé À CHAQUE TICK (aucun throttle), mesuré à ~27-30ms/appel même
# sur GPU (RTX 3060) -- modèle nano petit mais overhead ultralytics
# (letterbox/NMS/construction des résultats) incompressible. À ~30ms/appel,
# CET appel à lui seul consommait quasiment tout le budget d'un tick à 30Hz
# (33ms), avant même la capture d'écran (mss) et les autres détections
# (chain_lightning, culling_strike, cast bar, rage bar) -- le vrai taux de
# tick réel s'effondrait très en dessous des 30Hz visés, d'où la latence de
# réaction perçue en jeu. Les buffs hammer_storm/skull_crusher/
# focused_wrath_empowered ne changent que sur des événements discrets (proc,
# cast, expiration), pas en continu -- inutile de les relire à la fréquence
# du tick engine.
YOLO_DETECT_INTERVAL_S = 0.15   # ~6-7 ticks au rythme actuel (~23ms/tick median, cf. tick_timing_test.py) -- desserré depuis 0.1 (~4-5 ticks) pour réduire la fréquence des ticks chers, réactivité encore largement suffisante face au vrai goulot (attente de Fury, plusieurs secondes)

# Seuils par template pour tariq_buff_bar -- PLUS UTILISÉS en détection live
# depuis le passage à YOLO (cf. make_tariq_state_update_fn, wardenbot/yolo_detect.py) :
# hammer_storm_buff/skull_crusher_buff se faisaient confondre par
# cv2.matchTemplate (même artwork), root cause découverte en construisant le
# dataset YOLO. Conservés uniquement pour build_yolo_dataset.py, qui s'en
# sert comme labels faibles (bootstrap semi-assisté) si l'utilisateur élargit le
# dataset avec de nouveaux clips.
TARIQ_BUFF_BAR_THRESHOLDS = {
    "hammer_storm_buff": 0.55,
    "skull_crusher_buff": 0.60,
    "focused_wrath_buff": 0.50,
}

# Mesurés par script sur un clip Game Bar (barre de Fury/rage) -- rempli
# ANCRÉ À GAUCHE, décroît/croît selon dépense/regain (pas un simple minuteur
# comme la cast bar Hammer Storm -- une ressource qui fluctue dans les deux
# sens en jeu réel, vérifié frame par frame : chutes nettes ~25% cohérentes
# avec une dépense à coût fixe, hausses graduelles = regen/builders).
# tolerance=50 + max_gap=6 : la barre a un repère fixe (marqueur clair, forme
# de sablier) au milieu qui casse la couleur pure sur quelques colonnes,
# quel que soit le niveau de remplissage -- max_gap l'absorbe comme pour le
# biseau de bordure de la cast bar Hammer Storm.
RAGE_BAR_FILL_BGR = (7, 0, 128)   # BGR, rouge

# BUG TROUVÉ EN JEU (2026-07-12) : même après la capture englobante (~14ms)
# et le throttle YOLO desserré, un tick sur ~4 reste au-dessus du budget 30Hz
# (33ms) quand le throttle YOLO se redéclenche (~25-30ms de plus). Fix
# architectural : séparer la détection (lente, capture+matching+YOLO+OCR) de
# la boucle de décision (rapide, lit juste le dernier résultat + choisit +
# presse) sur deux threads. TariqDetectionSnapshot est l'état PARTAGÉ,
# immuable une fois publié -- le thread détection construit une instance
# fraîche à chaque cycle puis la publie d'un coup (une seule affectation,
# atomique sous le GIL -- jamais de lecture partielle/incohérente côté
# thread principal, pas besoin de verrou explicite). Le thread principal ne
# fait jamais qu'un COPIER ces valeurs dans state.debuffs/buffs/resources/flags
# -- jamais de calcul lourd dans le tick de l'engine.
#
# IMPORTANT (séparation des responsabilités) : state.flags contient un
# mélange de clés possédées par LA DÉTECTION (hammer_storm_casting -- mise à
# jour ici) et de clés possédées par LES HOOKS (hammer_storm_proc_ready, tous
# les _..._prev/_..._absent_ticks -- mises à jour dans
# tariq_pre_tick/post_tick, thread principal). state_update_fn (léger,
# ci-dessous) ne touche QUE les clés de détection, jamais par un
# remplacement en bloc du dict -- un remplacement complet écraserait les
# clés des hooks et reproduirait exactement le genre de bug de
# désynchronisation déjà vu avec warden_stacks (cf. hooks.py).
TARIQ_DETECTION_WORKER_HZ = 15.0   # cf. suggestion "8-15Hz" -- capture+matching tient largement dans ce budget (~15ms sur ~67ms)

# BUG TROUVÉ EN JEU (2026-07-12) : au moins un blocage total de ~45s observé
# (aucun tick, aucune presse, sur les deux threads) sans condition YAML ni
# cooldown qui l'explique -- jamais reproduit depuis pour capturer l'état
# exact au moment du blocage. Recherche (cf. réponse à l'utilisateur, sources
# incluses) : mss/GDI (BitBlt/GetDIBits, utilisé par ScreenCapture) est un
# point de blocage synchrone connu sous Windows -- rapports de latence/lag
# pendant que le jeu tourne (github.com/BoboTiG/python-mss#71), et les jeux
# en PLEIN ÉCRAN EXCLUSIF (pas "sans bordure"/"fenêtré") peuvent faire
# échouer la capture GDI en contournant le compositeur DWM. Impossible de
# confirmer un blocage exact de mss.grab() sans le mesurer directement --
# CAPTURE_WATCHDOG_THRESHOLD_S détecte et journalise tout appel anormalement
# lent (largement au-dessus du ~15ms mesuré en conditions normales) dans un
# fichier dédié, séparé de debug.log pour ne pas dépendre du thread
# principal -- si le blocage se reproduit, ce fichier dira enfin si c'est
# bien la capture (ou le matching/YOLO/OCR) qui bloque, et depuis combien de
# temps.
CAPTURE_WATCHDOG_THRESHOLD_S = 0.2
CAPTURE_WATCHDOG_LOG_PATH = Path("capture_watchdog.log")


def _log_slow_call(label: str, duration_s: float) -> None:
    line = f"{time.strftime('%H:%M:%S')} {label} a pris {duration_s:.3f}s (seuil {CAPTURE_WATCHDOG_THRESHOLD_S}s)\n"
    with CAPTURE_WATCHDOG_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


@dataclass
class TariqDetectionSnapshot:
    chain_lightning_dim: bool = False
    culling_strike_dim: bool = False
    hammer_storm_cast_pct: float = 0.0
    hammer_storm_casting: bool = False
    hammer_storm_empowered: bool = False
    skull_crusher_empowered: bool = False
    focused_wrath_empowered: bool = False
    fury: float = 0.0


def make_tariq_state_update_fn(capturer: ScreenCapture, matcher: TemplateMatcher, regions: dict):
    """
    Le template "chain_lightning" capturé est l'icône GRISÉE (indisponible),
    pas l'icône allumée (bleu clair, activée par thunder_call -- aucune
    condition buff/flag ne modélise cette relation dans le YAML d'origine,
    cf. discussion). On détecte donc l'état grisé et config/tariq.yaml
    caste sur SON ABSENCE (not debuff:chain_lightning_dim), pas sa présence.

    Même schéma pour "culling_strike" : template capturé GRISÉ (cible au-dessus
    de 35% PV, indisponible -- confirmé par l'utilisateur), condition YAML sur SON
    ABSENCE (not debuff:culling_strike_dim). Vrai cooldown 4.44s en plus de
    cette condition (cf. config/tariq.yaml).

    hammer_storm_cast_bar : lue en sens LTR (rempli ANCRÉ À GAUCHE -- le
    bord droit du rempli recule vers la gauche à mesure que la canalisation
    avance -- vérifié image par image sur un clip Game Bar). La valeur lue
    décroît donc bien avec le temps (~1.0 en début de cast, ~0.0 juste avant
    la fin), mais l'ALGORITHME DE SCAN doit partir de la gauche comme une
    barre classique -- ne pas remettre "rtl" ici, ça a été testé et corrigé.
    Alimente state.flags["hammer_storm_casting"], lu par tariq_pre_tick
    (hooks.py) pour verrouiller TOUTE presse (même off_gcd) pendant que ça
    caste -- Hammer Storm est la source de dégâts principale et ne doit
    jamais être coupée.

    hammer_storm_empowered / skull_crusher_empowered / focused_wrath_empowered
    (buffs qui arment les procs, cf. TARIQ_PROC_BUFFS dans hooks.py) : détectés
    par le modèle YOLO entraîné (wardenbot/yolo_detect.py,
    runs/detect/train/weights/best.pt) plutôt que par template matching --
    hammer_storm_buff et skull_crusher_buff partagent le même artwork
    (spirale rouge, juste le chiffre du compte à rebours diffère) et se
    faisaient confondre par cv2.matchTemplate : l'icône Hammer Storm seule
    matchait AUSSI le template skull_crusher_buff à ~0.64 de confiance,
    au-dessus de son propre seuil de prod (0.60) -- découvert en construisant
    le dataset d'entraînement (build_yolo_dataset.py), cf. TARIQ_BUFF_BAR_THRESHOLDS
    ci-dessus (conservé uniquement pour le bootstrap de dataset). Le modèle
    YOLO sépare correctement les 3 classes, y compris 3 buffs actifs
    simultanément (confiance 0.94-0.98 mesurée sur une capture live réelle
    via yolo_check.py, le 2026-07-12).

    Pas de comptage de stacks Hammer Storm (SIMPLIFIÉ le 2026-07-12, à la
    demande de l'utilisateur) : le badge n'affiche jamais "2" en jeu réel -- chaque
    proc (via Skull Crusher) doit être consommé dès qu'il apparaît, peu
    importe s'il y en a d'autres en attente. cf. TARIQ_PROC_BUFFS (hooks.py) :
    un simple front montant sur hammer_storm_empowered suffit.

    rage_bar -> state.resources["fury"] (condition YAML : fury_above:N).
    Rempli ANCRÉ À GAUCHE comme hammer_storm_cast_bar, mais contrairement à
    elle ce n'est PAS un minuteur qui ne fait QUE décroître -- une vraie
    ressource de gameplay qui monte et descend (regen/builders vs dépense),
    vérifié frame par frame sur un clip réel (chutes nettes ~25% cohérentes
    avec un coût fixe, hausses graduelles entre temps). tolerance=50 +
    max_gap=6 : la barre a un repère visuel fixe (marqueur clair, forme de
    sablier) au milieu qui casse la couleur pure sur quelques colonnes quel
    que soit le niveau de remplissage -- max_gap l'absorbe, comme pour le
    biseau de bordure de la cast bar Hammer Storm.
    """
    last_yolo_detect = {"t": 0.0, "found": {}}

    # BUG TROUVÉ EN JEU (2026-07-12) : "bot à la ramasse" (round 2, après le
    # throttle YOLO) -- mesuré via tick_timing_test.py : mss.grab() coûte un
    # coût FIXE d'environ 7ms PAR APPEL, quelle que soit la taille de la
    # région (une capture 10x10 coûte pareil qu'une 280x60 -- vérifié). Ce
    # fichier faisait 5-6 grab() séparés par tick (chain_lightning,
    # culling_strike, cast_bar, buff_bar, rage_bar, badge) = ~35-42ms seul,
    # déjà au-dessus du budget 30Hz (33ms) AVANT même YOLO/EasyOCR. Fix :
    # UNE seule capture englobante par tick (le coût scale avec la surface
    # au-delà d'une certaine taille, mais reste très inférieur à 5-6 appels
    # séparés -- mesuré ~14ms pour la boîte englobante ici, contre ~35-42ms
    # avant), puis découpage en mémoire (slicing numpy, quasi gratuit) de
    # chaque sous-région -- même logique de détection partout, juste la
    # source du frame qui change.
    _capture_names = [
        "chain_lightning", "culling_strike", "hammer_storm_cast_bar",
        "tariq_buff_bar", "rage_bar", "hammer_storm_stack_badge",
    ]
    _present_regions = [regions[n] for n in _capture_names if regions.get(n) is not None]
    if _present_regions:
        _bounds_left = min(r["left"] for r in _present_regions)
        _bounds_top = min(r["top"] for r in _present_regions)
        _bounds = {
            "left": _bounds_left,
            "top": _bounds_top,
            "width": max(r["left"] + r["width"] for r in _present_regions) - _bounds_left,
            "height": max(r["top"] + r["height"] for r in _present_regions) - _bounds_top,
        }
    else:
        _bounds = None

    def _crop(base_frame, region):
        x = region["left"] - _bounds["left"]
        y = region["top"] - _bounds["top"]
        return base_frame[y : y + region["height"], x : x + region["width"]]

    snapshot_box = {"latest": TariqDetectionSnapshot()}

    def _detection_cycle() -> None:
        """Un cycle complet de capture+matching+YOLO+OCR -- tourne dans le
        thread détection (cf. _run_detection_worker), jamais dans le thread
        principal. Construit une snapshot FRAÎCHE puis la publie d'un coup
        (affectation unique, atomique) -- ne jamais muter snapshot_box["latest"]
        en place."""
        if _bounds is None:
            return
        _t0 = time.monotonic()
        base_frame = capturer.grab(_bounds)
        _capture_dur = time.monotonic() - _t0
        if _capture_dur > CAPTURE_WATCHDOG_THRESHOLD_S:
            _log_slow_call("capturer.grab (boîte englobante tariq)", _capture_dur)
        snap = TariqDetectionSnapshot()

        chain_lightning_region = regions.get("chain_lightning")
        if chain_lightning_region is not None:
            frame = _crop(base_frame, chain_lightning_region)
            snap.chain_lightning_dim = matcher.is_match(frame, "chain_lightning")

        culling_strike_region = regions.get("culling_strike")
        if culling_strike_region is not None:
            frame = _crop(base_frame, culling_strike_region)
            snap.culling_strike_dim = matcher.is_match(frame, "culling_strike")

        cast_bar_region = regions.get("hammer_storm_cast_bar")
        if cast_bar_region is not None:
            frame = _crop(base_frame, cast_bar_region)
            pct = read_fill_pct(frame, HAMMER_STORM_CAST_BAR_FILL_BGR, tolerance=60, direction="ltr")
            snap.hammer_storm_cast_pct = pct
            snap.hammer_storm_casting = pct > HAMMER_STORM_CASTING_THRESHOLD

        buff_bar_region = regions.get("tariq_buff_bar")
        if buff_bar_region is not None:
            now = time.monotonic()
            # YOLO (wardenbot/yolo_detect.py) remplace les 3 template matches
            # ci-dessus depuis le 2026-07-12 : hammer_storm_buff et
            # skull_crusher_buff partagent le même artwork (spirale rouge) et
            # se faisaient confondre par cv2.matchTemplate -- confirmé en
            # construisant le dataset (build_yolo_dataset.py) : l'icône
            # Hammer Storm seule matchait AUSSI skull_crusher_buff à ~0.64,
            # au-dessus de son propre seuil de prod (0.60). Le modèle entraîné
            # (runs/detect/train-2/weights/best.pt) sépare correctement les 3
            # classes, y compris les 3 buffs actifs simultanément (validé sur
            # une capture live à 0.94-0.98 de confiance, cf. yolo_check.py).
            #
            # BUG TROUVÉ EN JEU (2026-07-12) : "bot à la ramasse" -- detect()
            # était appelé À CHAQUE TICK, mesuré ~25-30ms/appel même sur GPU
            # (overhead ultralytics incompressible pour un si petit modèle) --
            # throttlé comme l'OCR (YOLO_DETECT_INTERVAL_S), les 3 buffs ne
            # changeant que sur des événements discrets, pas en continu. Le
            # frame vient de la capture englobante (_crop, quasi gratuit) --
            # seul l'appel detect() lui-même reste throttlé.
            if now - last_yolo_detect["t"] >= YOLO_DETECT_INTERVAL_S:
                last_yolo_detect["t"] = now
                frame = _crop(base_frame, buff_bar_region)
                _t0 = time.monotonic()
                last_yolo_detect["found"] = detect(frame)
                _yolo_dur = time.monotonic() - _t0
                if _yolo_dur > CAPTURE_WATCHDOG_THRESHOLD_S:
                    _log_slow_call("yolo_detect.detect()", _yolo_dur)
            found = last_yolo_detect["found"]
            snap.hammer_storm_empowered = "hammer_storm_buff" in found
            snap.skull_crusher_empowered = "skull_crusher_buff" in found
            snap.focused_wrath_empowered = "focused_wrath_buff" in found

        rage_bar_region = regions.get("rage_bar")
        if rage_bar_region is not None:
            frame = _crop(base_frame, rage_bar_region)
            snap.fury = read_fill_pct(
                frame, RAGE_BAR_FILL_BGR, tolerance=50, direction="ltr", max_gap=6
            )

        snapshot_box["latest"] = snap   # publication atomique -- jamais de mutation en place

    def _run_detection_worker(stop_event: threading.Event) -> None:
        period = 1.0 / TARIQ_DETECTION_WORKER_HZ
        while not stop_event.is_set():
            start = time.monotonic()
            _detection_cycle()
            elapsed = time.monotonic() - start
            # Filet de sécurité en plus des mesures ciblées (capture/YOLO/OCR)
            # ci-dessus -- si un cycle complet est anormalement long sans
            # qu'aucune des mesures individuelles ne l'explique (ex: un
            # matcher.is_match ou une opération numpy qui bloque, pas
            # instrumentés séparément), au moins la durée totale est
            # journalisée pour ne pas repartir de zéro la prochaine fois.
            if elapsed > CAPTURE_WATCHDOG_THRESHOLD_S:
                _log_slow_call("_detection_cycle() (cycle complet)", elapsed)
            stop_event.wait(max(0.0, period - elapsed))

    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=_run_detection_worker, args=(stop_event,), daemon=True, name="tariq-detection-worker"
    )
    worker_thread.start()

    # state_update_fn (thread principal, appelé à chaque tick de l'engine) :
    # ne fait plus AUCUNE capture/détection -- juste une copie des dernières
    # valeurs connues (snapshot_box["latest"], publiées par le thread
    # détection ci-dessus). Copie clé par clé, JAMAIS state.flags = {...} en
    # bloc -- cf. commentaire au-dessus de TariqDetectionSnapshot : state.flags
    # contient aussi des clés possédées par les hooks (hammer_storm_proc_ready,
    # etc.) qu'un remplacement complet écraserait.
    def state_update_fn(state: State) -> None:
        snap = snapshot_box["latest"]
        state.debuffs["chain_lightning_dim"] = snap.chain_lightning_dim
        state.debuffs["culling_strike_dim"] = snap.culling_strike_dim
        state.resources["hammer_storm_cast_pct"] = snap.hammer_storm_cast_pct
        state.flags["hammer_storm_casting"] = snap.hammer_storm_casting
        state.buffs["hammer_storm_empowered"] = snap.hammer_storm_empowered
        state.buffs["skull_crusher_empowered"] = snap.skull_crusher_empowered
        state.buffs["focused_wrath_empowered"] = snap.focused_wrath_empowered
        state.resources["fury"] = snap.fury

    return state_update_fn


# --- Registre par personnage : config YAML, dossier de templates, hooks,
# mode par défaut, et fabrique de state_update_fn (None = rien à lire à
# l'écran pour l'instant). C'est CE dict qui pilote build_engine() plus bas
# -- ajouter un personnage revient à ajouter une entrée ici + KEY_MAP/REGIONS.
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


def make_debug_logger(state: State) -> Callable[[Optional[str]], None]:
    """
    Écrit une ligne dans debug.log (écrasé à chaque lancement) à chaque tick
    où quelque chose a changé : une touche pressée, ou l'état buffs/debuffs/
    resources différent du tick précédent. Sert à rejouer après coup ce que
    le bot voyait/décidait au moment où un bug apparaît en jeu -- branché via
    RotationEngine.run_forever(on_tick=...).
    """
    DEBUG_LOG_PATH.write_text("", encoding="utf-8")
    last_snapshot: dict = {}

    def log_tick(pressed: Optional[str]) -> None:
        nonlocal last_snapshot
        now = time.monotonic()
        # BUG TROUVÉ EN JEU (2026-07-12) : 47s sans une seule presse (même
        # wild_swing, censée quasiment toujours pouvoir partir) sans qu'aucun
        # buff/debuff/resource logué n'explique le blocage -- gcd_until et
        # hard_lock_until (les deux seuls verrous qui bloquent TOUT, cf.
        # RotationEngine._pick_ability) n'étaient PAS dans ce snapshot,
        # impossible de confirmer si l'un des deux était resté coincé dans le
        # futur. Ajoutés en "secondes restantes depuis maintenant" (0 ou
        # négatif = déjà expiré, donc pas bloquant) pour être lisibles direct
        # dans le log sans dédution manuelle.
        snapshot = {
            "buffs": dict(state.buffs),
            "debuffs": dict(state.debuffs),
            "resources": dict(state.resources),
            # Tout state.flags plutôt qu'un champ Meiko codé en dur
            # (warden_stacks) -- generique pour marcher pareil avec les
            # flags de proc de Tariq (hammer_storm_proc_ready, etc.).
            "flags": dict(state.flags),
            "locks": {
                "gcd_remaining_s": round(state.gcd_until - now, 3),
                "hard_lock_remaining_s": round(state.hard_lock_until - now, 3),
            },
            "cooldowns_remaining_s": {
                name: round(until - now, 2)
                for name, until in state.cooldown_until.items()
                if until - now > 0.05   # n'affiche que les cooldowns réellement actifs -- pas de bruit
            },
        }
        if pressed is None and snapshot == last_snapshot:
            return  # rien de nouveau -> pas la peine de spammer le fichier
        last_snapshot = snapshot
        # skip_trace HORS du dict comparé pour le dedup (snapshot) -- il
        # contient des durées qui changent à chaque tick (ex: "cooldown actif
        # encore 0.38s"), l'inclure dans la comparaison anéantirait le dedup
        # (jamais deux ticks identiques). Affiché seulement quand pressed est
        # None -- cf. State.debug_skip_trace (engine.py), rempli par
        # _pick_ability : pourquoi CHAQUE ability a été écartée ce tick.
        skip_trace = state.debug_skip_trace if pressed is None else []
        line = (
            f"{time.strftime('%H:%M:%S')} "
            f"pressed={pressed!r} last_cast={state.last_cast!r} "
            f"buffs={snapshot['buffs']} debuffs={snapshot['debuffs']} "
            f"resources={snapshot['resources']} flags={snapshot['flags']} "
            f"locks={snapshot['locks']} cooldowns_remaining_s={snapshot['cooldowns_remaining_s']} "
            f"skip_trace={skip_trace}\n"
        )
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)

    return log_tick


VK_F9 = 0x78


def make_debug_reset_hotkey(state: State) -> Callable[[], None]:
    """
    F9 en jeu : remet state.flags["warden_stacks"] à 0 à la main. Sert de
    secours si le compteur de stacks Warden of the Temple (cf. hooks.py) se
    désynchronise de l'état réel -- typiquement si tu relances le bot en
    plein combat alors que des stacks étaient déjà posées avant.
    """
    pressed_last = False

    def check_reset_hotkey() -> None:
        nonlocal pressed_last
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F9) & 0x8000)
        if down and not pressed_last:
            state.flags["warden_stacks"] = 0
            state.flags.pop("warden_stacks_since", None)
            print("[main] F9 -- warden_stacks remis à 0 manuellement")
        pressed_last = down

    return check_reset_hotkey


VK_C = 0x43


def make_pause_toggle() -> tuple[Callable[[], bool], Callable[[], None]]:
    """
    C en jeu : active/désactive la rotation sans relancer le script. En
    pause, RotationEngine.run_forever() saute complètement tick() (cf.
    engine.py) -- aucune touche pressée, aucun état modifié, donc rien ne
    dérive pendant que c'est arrêté.
    Retourne (is_paused, check_toggle_hotkey) : is_paused se passe à
    run_forever(is_paused=...), check_toggle_hotkey se branche sur on_tick
    (il doit tourner MÊME en pause pour pouvoir détecter la reprise).
    """
    toggle_state = {"paused": False}
    pressed_last = False

    def is_paused() -> bool:
        return toggle_state["paused"]

    def check_toggle_hotkey() -> None:
        nonlocal pressed_last
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_C) & 0x8000)
        if down and not pressed_last:
            toggle_state["paused"] = not toggle_state["paused"]
            print(f"[main] C -- rotation {'EN PAUSE' if toggle_state['paused'] else 'ACTIVE'}")
        pressed_last = down

    return is_paused, check_toggle_hotkey


def build_engine(character: str, dry_run: bool = True) -> RotationEngine:
    cfg = CHARACTERS[character]
    key_map = KEY_MAP[character]
    regions = REGIONS[character]

    abilities, meta = load_abilities(cfg["config"])
    state = State(mode=cfg["default_mode"])

    matcher = TemplateMatcher(cfg["templates"])
    capturer = ScreenCapture() if not dry_run else None
    input_sim = InputSimulator(dry_run=dry_run)

    def press_fn(name: str) -> None:
        key = key_map.get(name)
        if key is None:
            print(f"[main] pas de touche mappée pour {name!r} -- ignoré")
            return
        # Humanisation allégée (wardenbot/input_sim.py, HUMANIZE_HOLD_S_LIGHT)
        # pour les fillers/spam de Tariq -- wild_swing (filler pur, cf.
        # config/tariq.yaml) et skull_crusher (spam voulu pour faire proc
        # Hammer Storm). Tout le reste garde la fourchette normale, y compris
        # les procs/cooldowns où la marge anti-GCD-raté compte plus que
        # quelques ms économisés.
        light = name in ("wild_swing", "skull_crusher")
        input_sim.press(key, label=name, light=light)

    def is_ready_fn(name: str, confidence_threshold: Optional[float] = None) -> bool:
        if dry_run:
            return True  # en dry-run on considère tout "ready" pour tester la logique
        region = regions.get(name)
        if region is None:
            return True  # pas de template pour cette entrée -> on ne bloque pas dessus
        frame = capturer.grab(region)
        threshold = confidence_threshold if confidence_threshold is not None else 0.70
        return matcher.is_match(frame, name, threshold)

    make_state_update_fn = cfg["make_state_update_fn"]
    state_update_fn = (
        make_state_update_fn(capturer, matcher, regions)
        if (not dry_run and make_state_update_fn is not None)
        else None
    )

    engine_kwargs = dict(
        abilities=abilities,
        state=state,
        press_fn=press_fn,
        is_ready_fn=is_ready_fn,
        gcd_ms=meta.get("gcd_ms", 1100),
        pre_tick_hook=cfg["pre_tick_hook"],
        post_tick_hook=cfg["post_tick_hook"],
        state_update_fn=state_update_fn,
    )
    if "off_gcd_hook_abilities" in cfg:
        engine_kwargs["off_gcd_hook_abilities"] = cfg["off_gcd_hook_abilities"]

    return RotationEngine(**engine_kwargs)


def choose_character() -> str:
    """Demande en console quel personnage piloter. Accepte le nom ou son
    numéro dans la liste -- redemande tant que la réponse n'est pas valide."""
    names = list(CHARACTERS.keys())
    print("[main] Personnages disponibles :")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {name}")
    while True:
        choice = input("Choisis un personnage (numéro ou nom) : ").strip().lower()
        if choice in CHARACTERS:
            return choice
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return names[int(choice) - 1]
        print(f"Choix invalide ({choice!r}) -- réessaie.")


if __name__ == "__main__":
    character = choose_character()
    engine = build_engine(character, dry_run=False)  # mettre à True pour tester sans presser de touches
    print(f"[main] Personnage : {character}")
    print(f"[main] debug -> {DEBUG_LOG_PATH.resolve()}")
    print("[main] C = pause/reprise rotation -- F9 = reset manuel warden_stacks")

    log_tick = make_debug_logger(engine.state)
    check_reset_hotkey = make_debug_reset_hotkey(engine.state)
    is_paused, check_pause_hotkey = make_pause_toggle()

    def on_tick(pressed: Optional[str]) -> None:
        log_tick(pressed)
        check_reset_hotkey()
        check_pause_hotkey()

    tick_hz = CHARACTERS[character].get("tick_hz", 30.0)
    engine.run_forever(tick_hz=tick_hz, on_tick=on_tick, is_paused=is_paused)
