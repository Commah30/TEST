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
from wardenbot.diagnostics import CAPTURE_WATCHDOG_THRESHOLD_S, log_error, log_pipeline_diagnostic, log_slow_call
from wardenbot.engine import State
from wardenbot.matcher import TemplateMatcher
from wardenbot.yolo_detect import detect, get_last_predict_speed_ms


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

# VOILE GCD (2026-07-14, cf. discussion "voile noir décroissant qui
# correspond au GCD du sort") -- chaque icône de la barre s'assombrit puis se
# réclaire progressivement, validé par extraction directe sur toute une
# session filmée (771s) : heavy_strike/wild_swing/face_breaker/skull_crusher/
# hammer_storm/chain_lightning partagent EXACTEMENT le même pattern (écarts
# médians ~1.05-1.15s, séquences par moments identiques entre icônes
# différentes malgré des cooldowns réels très différents) -- ce n'est PAS le
# cooldown individuel de chaque sort, c'est le GCD PARTAGÉ (ressource unique).
# culling_strike/thunder_call/focused_wrath (off_gcd, ne consomment pas le
# GCD partagé) montrent un pattern bien plus espacé et cohérent avec LEUR
# PROPRE cooldown (4.44s/43s/86s) -- suivis séparément, un par ability.
#
# Ce signal est un accusé de réception visuel du JEU, indépendant de YOLO/
# InputWorker -- utilisé par hooks.py pour confirmer qu'une presse a
# réellement été traitée par le jeu (cf. PRESS_CONFIRM_WINDOW_S), pas
# seulement envoyée par notre input.
GCD_ICON_REGIONS = ("heavy_strike", "wild_swing", "face_breaker", "skull_crusher", "hammer_storm", "chain_lightning")
OFF_GCD_ICON_REGIONS = ("culling_strike", "thunder_call", "focused_wrath")
ALL_ICON_REGIONS = GCD_ICON_REGIONS + OFF_GCD_ICON_REGIONS

# Validés sur toute la vidéo (cf. discussion) : plateau "prêt" ~90-160 selon
# l'artwork de l'icône, voile ~25-55 -- une marge de 50% du plateau roulant
# récent sépare proprement les deux sans réglage par icône.
ICON_VEIL_ROLLING_WINDOW_S = 3.0
ICON_VEIL_REL_THRESHOLD = 0.5


@dataclass
class TariqDetectionSnapshot:
    culling_strike_dim: bool = False
    hammer_storm_cast_pct: float = 0.0
    hammer_storm_casting: bool = False
    hammer_storm_empowered: bool = False
    skull_crusher_empowered: bool = False
    focused_wrath_empowered: bool = False
    fury: float = 0.0
    gcd_veil_active: bool = False           # OR sur les 6 icônes GCD_ICON_REGIONS -- voile PARTAGÉ
    veil_culling_strike: bool = False        # off_gcd -- voile propre à SON cooldown (4.44s)
    veil_thunder_call: bool = False          # off_gcd -- voile propre à SON cooldown (43s)
    veil_focused_wrath: bool = False         # off_gcd -- voile propre à SON cooldown (86s)


def make_tariq_state_update_fn(capturer: ScreenCapture, matcher: TemplateMatcher, regions: dict):
    """
    `capturer` accepté ici uniquement pour garder la même signature que
    make_meiko_state_update_fn (build_engine appelle les deux de façon
    générique, cf. main.py) -- PAS utilisé pour capturer quoi que ce soit :
    le thread détection construit sa propre instance (cf.
    _run_detection_worker/_detection_cycle plus bas), un mss.mss() n'étant
    valide que sur le thread qui l'a créé.

    chain_lightning : détection repassée en NORMALE (2026-07-14, choix de
    l'utilisateur) -- ce n'est plus le worker qui matche son template ici. La
    détection standard (is_ready_fn sur le thread de décision) matche l'icône
    ALLUMÉE (disponible) directement, comme n'importe quelle ability du
    projet. Le worker ne garde chain_lightning que pour le voile GCD
    (luminosité de sa région, cf. GCD_ICON_REGIONS) -- plus de match de
    template ni de debuff chain_lightning_dim. cf. config/tariq.yaml.

    culling_strike, lui, garde la détection INVERSÉE : template capturé GRISÉ (cible au-dessus
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
        # Icônes de la barre d'abilities (2026-07-14, voile GCD) -- culling_strike
        # est déjà dans cette liste pour le match "grisé", chain_lightning y reste
        # désormais UNIQUEMENT pour le voile GCD (son match grisé a été retiré,
        # détection repassée en normale, cf. plus haut), les autres s'ajoutent à
        # la boîte englobante ici plutôt que de déclencher des grab() séparés
        # (cf. le bug "5-6 grab() par tick" déjà corrigé plus haut dans ce fichier).
        "heavy_strike", "wild_swing", "face_breaker", "skull_crusher",
        "hammer_storm", "thunder_call", "focused_wrath",
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

    # Historique glissant de luminosité par icône (voile GCD, cf. constantes
    # ci-dessus) -- vit dans la closure, jamais dans state (state ne stocke
    # que le résultat booléen final, pas le détail interne de la détection).
    _icon_brightness_history: dict[str, list[tuple[float, float]]] = {name: [] for name in ALL_ICON_REGIONS}

    def _read_icon_veiled(base_frame, region_name: str, now: float) -> bool:
        region = regions.get(region_name)
        if region is None:
            return False
        frame = _crop(base_frame, region)
        brightness = float(frame.mean())
        history = _icon_brightness_history[region_name]
        history.append((now, brightness))
        while history and now - history[0][0] > ICON_VEIL_ROLLING_WINDOW_S:
            history.pop(0)
        rolling_max = max(b for _, b in history)
        return brightness < ICON_VEIL_REL_THRESHOLD * rolling_max if rolling_max > 0 else False

    snapshot_box = {"latest": TariqDetectionSnapshot()}

    def _detection_cycle(capturer: ScreenCapture) -> None:
        """Un cycle complet de capture+matching+YOLO+OCR -- tourne dans le
        thread détection (cf. _run_detection_worker), jamais dans le thread
        principal. Construit une snapshot FRAÎCHE puis la publie d'un coup
        (affectation unique, atomique) -- ne jamais muter snapshot_box["latest"]
        en place.

        `capturer` reçu en paramètre (PAS celui de make_tariq_state_update_fn,
        volontairement ignoré ici) : un mss.mss() n'est valide que sur le
        thread qui l'a créé (cf. wardenbot/capture.py) -- ce thread détection
        doit utiliser SA PROPRE instance (cf. _run_detection_worker), jamais
        celle construite par build_engine() sur un autre thread. Bug trouvé en
        jeu (2026-07-15) : ScreenShotError('... BitBlt') levé en boucle quand
        les deux threads se partageaient la même instance.

        Instrumenté étape par étape (2026-07-13, "transforme le watchdog en
        outil de diagnostic") : chaque étape (grab, chaque match template,
        chaque lecture de barre, et la décomposition interne d'ultralytics
        preprocess/inference/postprocess pour l'appel YOLO) est chronométrée
        et accumulée dans `stages` -- si le cycle entier dépasse le seuil, on
        journalise TOUT ce détail d'un coup (cf.
        diagnostics.py::log_pipeline_diagnostic) plutôt qu'une seule durée
        opaque. cpu_time (time.process_time(), pas time.monotonic()) permet
        de voir si le thread calculait vraiment ou attendait juste."""
        if _bounds is None:
            return
        _cycle_t0 = time.monotonic()
        _cpu_t0 = time.process_time()
        stages: dict[str, float] = {}

        _t0 = time.monotonic()
        base_frame = capturer.grab(_bounds)
        _capture_dur = time.monotonic() - _t0
        stages["grab_ms"] = _capture_dur * 1000
        snap = TariqDetectionSnapshot()

        # chain_lightning : plus de match du template ICI (2026-07-14) --
        # détection repassée en NORMALE (cf. config/tariq.yaml), donc
        # is_ready_fn (thread de décision) matche l'icône allumée directement,
        # comme pour toute ability standard. Le worker ne s'en sert plus que
        # pour le voile GCD (luminosité de la région, cf. GCD_ICON_REGIONS),
        # pas pour un template. La région reste dans _capture_names pour ce
        # voile.

        culling_strike_region = regions.get("culling_strike")
        if culling_strike_region is not None:
            _t0 = time.monotonic()
            frame = _crop(base_frame, culling_strike_region)
            snap.culling_strike_dim = matcher.is_match(frame, "culling_strike")
            stages["culling_strike_match_ms"] = (time.monotonic() - _t0) * 1000

        cast_bar_region = regions.get("hammer_storm_cast_bar")
        if cast_bar_region is not None:
            _t0 = time.monotonic()
            frame = _crop(base_frame, cast_bar_region)
            pct = read_fill_pct(frame, HAMMER_STORM_CAST_BAR_FILL_BGR, tolerance=60, direction="ltr")
            snap.hammer_storm_cast_pct = pct
            snap.hammer_storm_casting = pct > HAMMER_STORM_CASTING_THRESHOLD
            stages["cast_bar_read_ms"] = (time.monotonic() - _t0) * 1000

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
            # (runs/detect/train-4/weights/best.pt) sépare correctement les 3
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
                stages["yolo_wrapper_total_ms"] = _yolo_dur * 1000
                # Décomposition GRATUITE fournie par ultralytics lui-même
                # (results[0].speed) -- preprocess (letterbox/normalize),
                # inference (forward pass), postprocess (NMS + construction
                # des résultats -- ultralytics ne sépare pas la NMS du reste
                # du postprocess). Comparé à yolo_wrapper_total_ms : si la
                # somme de ces 3 est BIEN inférieure au total mesuré ici, le
                # temps perdu est HORS d'ultralytics (attente GPU/driver que
                # même ultralytics ne voit pas).
                for stage_name, stage_ms in get_last_predict_speed_ms().items():
                    stages[f"yolo_{stage_name}_ms"] = stage_ms
                # Plus de log_slow_call individuel ici -- yolo_wrapper_total_ms
                # est déjà dans `stages`, donc un YOLO lent apparaît de toute
                # façon dans le diagnostic consolidé du cycle entier
                # (log_pipeline_diagnostic, en bas de _detection_cycle()) --
                # un check séparé ici ferait double emploi.
            found = last_yolo_detect["found"]
            snap.hammer_storm_empowered = "hammer_storm_buff" in found
            snap.skull_crusher_empowered = "skull_crusher_buff" in found
            snap.focused_wrath_empowered = "focused_wrath_buff" in found

        rage_bar_region = regions.get("rage_bar")
        if rage_bar_region is not None:
            _t0 = time.monotonic()
            frame = _crop(base_frame, rage_bar_region)
            snap.fury = read_fill_pct(
                frame, RAGE_BAR_FILL_BGR, tolerance=50, direction="ltr", max_gap=6
            )
            stages["rage_bar_read_ms"] = (time.monotonic() - _t0) * 1000

        _t0 = time.monotonic()
        _icon_now = time.monotonic()
        snap.gcd_veil_active = any(_read_icon_veiled(base_frame, name, _icon_now) for name in GCD_ICON_REGIONS)
        snap.veil_culling_strike = _read_icon_veiled(base_frame, "culling_strike", _icon_now)
        snap.veil_thunder_call = _read_icon_veiled(base_frame, "thunder_call", _icon_now)
        snap.veil_focused_wrath = _read_icon_veiled(base_frame, "focused_wrath", _icon_now)
        stages["icon_veil_read_ms"] = (time.monotonic() - _t0) * 1000

        snapshot_box["latest"] = snap   # publication atomique -- jamais de mutation en place

        _cycle_dur = time.monotonic() - _cycle_t0
        if _cycle_dur > CAPTURE_WATCHDOG_THRESHOLD_S:
            _cpu_dur = time.process_time() - _cpu_t0
            log_pipeline_diagnostic(
                "_detection_cycle() (cycle complet)",
                total_duration_s=_cycle_dur,
                stages=stages,
                cpu_time_s=_cpu_dur,
            )

    def _run_detection_worker(stop_event: threading.Event) -> None:
        # Instance dédiée à CE thread (cf. _detection_cycle ci-dessus) -- ne
        # jamais réutiliser le `capturer` reçu par make_tariq_state_update_fn,
        # construit sur un autre thread (build_engine).
        worker_capturer = ScreenCapture()
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
                _detection_cycle(worker_capturer)
            except Exception as exc:
                log_error("tariq _detection_cycle()", exc)
            # Le diagnostic détaillé "cycle complet" (étapes + CPU time + GPU)
            # est désormais journalisé DEPUIS _detection_cycle() elle-même
            # (cf. log_pipeline_diagnostic ci-dessus) -- un 2e check ici
            # ferait doublon sur le même évènement, en moins détaillé.
            elapsed = time.monotonic() - start
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
        state.debuffs["culling_strike_dim"] = snap.culling_strike_dim
        state.resources["hammer_storm_cast_pct"] = snap.hammer_storm_cast_pct
        state.flags["hammer_storm_casting"] = snap.hammer_storm_casting
        state.buffs["hammer_storm_empowered"] = snap.hammer_storm_empowered
        state.buffs["skull_crusher_empowered"] = snap.skull_crusher_empowered
        state.buffs["focused_wrath_empowered"] = snap.focused_wrath_empowered
        state.resources["fury"] = snap.fury
        state.buffs["gcd_veil_active"] = snap.gcd_veil_active
        state.buffs["veil_culling_strike"] = snap.veil_culling_strike
        state.buffs["veil_thunder_call"] = snap.veil_thunder_call
        state.buffs["veil_focused_wrath"] = snap.veil_focused_wrath

    return state_update_fn
