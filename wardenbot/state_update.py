"""
state_update.py — construit les state_update_fn (lecture d'écran ->
state.buffs/debuffs/resources/flags) par personnage.

Extrait de main.py (qui faisait 750+ lignes en mélangeant wiring d'entrée
et logique de détection) -- ce fichier ne contient QUE la logique "lire
l'écran -> remplir le state", pas le point d'entrée (KEY_MAP/REGIONS ->
wardenbot/game_config.py ; boucle/hotkeys/debug logger -> main.py).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from wardenbot.bar_reader import read_fill_pct
from wardenbot.capture import ScreenCapture
from wardenbot.diagnostics import CAPTURE_WATCHDOG_THRESHOLD_S, log_error, log_slow_call
from wardenbot.engine import State
from wardenbot.matcher import TemplateMatcher
from wardenbot.yolo_detect import detect


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
# l'utilisateur via `capture_helper.py mark`, donc REGIONS (wardenbot/game_config.py)
# est fiable.
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
            log_slow_call("capturer.grab (boîte englobante tariq)", _capture_dur)
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
                    log_slow_call("yolo_detect.detect()", _yolo_dur)
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
        last_cycle_start = None
        while not stop_event.is_set():
            start = time.monotonic()
            # BUG TROUVÉ EN JEU (2026-07-12) : analyse de debug.log d'une vraie
            # session -- 6 épisodes de 8 à 24s où TOUT le snapshot (fury,
            # chain_lightning_dim, culling_strike_dim...) reste figé à
            # l'identique, alors que le thread principal continue de ticker
            # normalement (~60Hz sans interruption) -- donc pas un crash, pas
            # un _detection_cycle() individuellement lent (AUCUNE de ces 6
            # fenêtres n'apparaît dans capture_watchdog.log). Angle mort de
            # l'instrumentation précédente : elle ne mesurait QUE la durée de
            # _detection_cycle() lui-même, jamais le temps passé DANS
            # stop_event.wait() ni un éventuel retard de réveil du thread par
            # l'OS -- un blocage à cet endroit serait invisible. Ce check compare le
            # début de CE cycle au début du précédent (donc cycle précédent +
            # son wait() inclus) : si l'écart dépasse largement period
            # (~67ms), quelque chose a empêché ce thread de tourner, quelle
            # que soit la raison exacte -- journalisé séparément de "cycle
            # complet" pour distinguer "le travail a pris du temps" de "le
            # thread n'a simplement pas été réveillé/exécuté à temps".
            if last_cycle_start is not None:
                gap = start - last_cycle_start
                if gap > CAPTURE_WATCHDOG_THRESHOLD_S:
                    log_slow_call("_run_detection_worker (intervalle entre cycles)", gap)
            last_cycle_start = start

            # ROBUSTESSE (2026-07-12) : un thread daemon Python qui lève une
            # exception non rattrapée meurt SILENCIEUSEMENT -- pas de
            # traceback visible en jeu (juste un warning éventuel dans une
            # console qu'on ne regarde pas pendant qu'on joue), et plus aucune
            # publication de nouvelle snapshot ensuite. state_update_fn
            # continuerait à lire indéfiniment le DERNIER snapshot connu
            # (jamais rafraîchi) sans jamais planter le thread principal --
            # exactement le genre de "bot semble figé/à la ramasse sans
            # raison visible" déjà chassé plusieurs fois dans ce projet (cf.
            # CLAUDE.md). Un cycle qui échoue (frame vide, glitch mss, hoquet
            # YOLO) doit juste être journalisé et retenté au prochain cycle,
            # pas tuer le thread.
            try:
                _detection_cycle()
            except Exception as exc:
                log_error("tariq _detection_cycle()", exc)
            elapsed = time.monotonic() - start
            # Filet de sécurité en plus des mesures ciblées (capture/YOLO/OCR)
            # ci-dessus -- si un cycle complet est anormalement long sans
            # qu'aucune des mesures individuelles ne l'explique (ex: un
            # matcher.is_match ou une opération numpy qui bloque, pas
            # instrumentés séparément), au moins la durée totale est
            # journalisée pour ne pas repartir de zéro la prochaine fois.
            if elapsed > CAPTURE_WATCHDOG_THRESHOLD_S:
                log_slow_call("_detection_cycle() (cycle complet)", elapsed)
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
