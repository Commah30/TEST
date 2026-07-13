"""
state_update.py — construit les state_update_fn (lecture d'écran ->
state.buffs/debuffs/resources/flags) par personnage.

Extrait de main.py (qui faisait 750+ lignes en mélangeant wiring d'entrée
et logique de détection) -- ce fichier ne contient QUE la logique "lire
l'écran -> remplir le state", pas le point d'entrée (KEY_MAP/REGIONS ->
wardenbot/game_config.py ; boucle/hotkeys/debug logger -> main.py).

EN: state_update.py — builds the per-character state_update_fn (screen read ->
EN: state.buffs/debuffs/resources/flags).
EN:
EN: Extracted from main.py (which was 750+ lines mixing entry-point wiring and
EN: detection logic) -- this file contains ONLY the "read the screen -> fill
EN: the state" logic, not the entry point (KEY_MAP/REGIONS ->
EN: wardenbot/game_config.py; loop/hotkeys/debug logger -> main.py).
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

    EN: Reads on-screen the template-detectable buffs/debuffs/resources and
    EN: fills state.buffs/state.debuffs/state.resources -- called first of all
    EN: every tick (cf. RotationEngine.state_update_fn). This is the wiring that
    EN: was missing for meiko_pre_tick to see the REAL state of the Spirited
    EN: Vortex/Strikes buffs rather than a permanent False default.
    """
    def state_update_fn(state: State) -> None:
        # Une seule capture de la zone large, réutilisée pour chercher les 3
        # templates dedans -- au lieu d'une capture étroite par icône (fragile
        # dès que la barre de buffs se réorganise et décale les icônes).
        # EN: A single capture of the wide area, reused to search the 3
        # EN: templates inside it -- instead of a narrow capture per icon
        # EN: (fragile as soon as the buff bar reorders and shifts the icons).
        buff_frame = capturer.grab(regions["buff_bar"])
        state.buffs["spirited_vortex"] = matcher.is_match(buff_frame, "spirited_vortex")
        state.buffs["spirited_strikes"] = matcher.is_match(buff_frame, "spirited_strikes")
        state.debuffs["warden_of_the_temple_3"] = matcher.is_match(buff_frame, "warden_of_the_temple_3")

        state.resources["stormfall"] = 1.0 if matcher.is_match(
            capturer.grab(regions["stormfall_lit"]), "stormfall_lit"
        ) else 0.0
        # earthfall_lit pas encore capturé -> state.resources["earthfall"] reste
        # à son défaut (0.0 via .get()) tant que le template n'existe pas.
        # EN: earthfall_lit not captured yet -> state.resources["earthfall"]
        # EN: stays at its default (0.0 via .get()) while the template doesn't
        # EN: exist.

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
# EN: Measured by script on a Game Bar clip (near-full frame, ~95%) -- cf.
# EN: ffmpeg_hammerstorm/023.png. The script-computed bounding box lands on
# EN: left=1123, top=930 -- identical to the coordinates the user read by hand
# EN: via `capture_helper.py mark`, so REGIONS (wardenbot/game_config.py) is
# EN: reliable.
# EN: tolerance=60 (instead of read_fill_pct's default 30): the bar has an
# EN: anti-aliased bevel/glow a few pixels near its edges that departs clearly
# EN: from the pure color -- validated frame by frame on the real clip (with
# EN: tolerance=30 the reading fell back to 0% everywhere, including frames
# EN: clearly well-filled to the eye).
HAMMER_STORM_CAST_BAR_FILL_BGR = (8, 175, 205)   # BGR, or/jaune · EN: BGR, gold/yellow
HAMMER_STORM_CASTING_THRESHOLD = 0.02   # en dessous, on considère la canalisation terminée · EN: below this, the channel is considered finished

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
# EN: BUG FOUND IN-GAME (2026-07-12): "the bot lags" -- yolo_detect.detect()
# EN: was called ON EVERY TICK (no throttle), measured at ~27-30ms/call even on
# EN: GPU (RTX 3060) -- small nano model but incompressible ultralytics
# EN: overhead (letterbox/NMS/results building). At ~30ms/call, THIS call alone
# EN: consumed almost the whole budget of a 30Hz tick (33ms), before even the
# EN: screen capture (mss) and the other detections (chain_lightning,
# EN: culling_strike, cast bar, rage bar) -- the real tick rate collapsed well
# EN: below the targeted 30Hz, hence the reaction latency felt in-game. The
# EN: hammer_storm/skull_crusher/focused_wrath_empowered buffs only change on
# EN: discrete events (proc, cast, expiration), not continuously -- no need to
# EN: re-read them at the engine tick frequency.
YOLO_DETECT_INTERVAL_S = 0.15   # ~6-7 ticks au rythme actuel (~23ms/tick median, cf. tick_timing_test.py) -- desserré depuis 0.1 (~4-5 ticks) pour réduire la fréquence des ticks chers, réactivité encore largement suffisante face au vrai goulot (attente de Fury, plusieurs secondes) · EN: ~6-7 ticks at the current rate (~23ms/tick median, cf. tick_timing_test.py) -- loosened from 0.1 (~4-5 ticks) to reduce the frequency of expensive ticks, reactivity still amply sufficient against the real bottleneck (waiting for Fury, several seconds)

# Seuils par template pour tariq_buff_bar -- PLUS UTILISÉS en détection live
# depuis le passage à YOLO (cf. make_tariq_state_update_fn, wardenbot/yolo_detect.py) :
# hammer_storm_buff/skull_crusher_buff se faisaient confondre par
# cv2.matchTemplate (même artwork), root cause découverte en construisant le
# dataset YOLO. Conservés uniquement pour build_yolo_dataset.py, qui s'en
# sert comme labels faibles (bootstrap semi-assisté) si l'utilisateur élargit le
# dataset avec de nouveaux clips.
# EN: Per-template thresholds for tariq_buff_bar -- NO LONGER USED in live
# EN: detection since switching to YOLO (cf. make_tariq_state_update_fn,
# EN: wardenbot/yolo_detect.py): hammer_storm_buff/skull_crusher_buff were being
# EN: confused by cv2.matchTemplate (same artwork), root cause discovered while
# EN: building the YOLO dataset. Kept only for build_yolo_dataset.py, which uses
# EN: them as weak labels (semi-assisted bootstrap) if the user expands the
# EN: dataset with new clips.
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
# EN: Measured by script on a Game Bar clip (Fury/rage bar) -- fill ANCHORED
# EN: LEFT, decreases/increases with spending/gain (not a simple timer like the
# EN: Hammer Storm cast bar -- a resource that fluctuates both ways in a real
# EN: game, verified frame by frame: sharp ~25% drops consistent with a
# EN: fixed-cost spend, gradual rises = regen/builders). tolerance=50 +
# EN: max_gap=6: the bar has a fixed marker (light hourglass-shaped mark) in the
# EN: middle that breaks the pure color on a few columns, regardless of fill
# EN: level -- max_gap absorbs it as for the Hammer Storm cast bar's border
# EN: bevel.
RAGE_BAR_FILL_BGR = (7, 0, 128)   # BGR, rouge · EN: BGR, red

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
# EN: BUG FOUND IN-GAME (2026-07-12): even after the enclosing capture (~14ms)
# EN: and the loosened YOLO throttle, ~1 tick in 4 stays above the 30Hz budget
# EN: (33ms) when the YOLO throttle re-triggers (~25-30ms more). Architectural
# EN: fix: separate detection (slow, capture+matching+YOLO+OCR) from the
# EN: decision loop (fast, just reads the latest result + picks + presses) onto
# EN: two threads. TariqDetectionSnapshot is the SHARED state, immutable once
# EN: published -- the detection thread builds a fresh instance each cycle then
# EN: publishes it in one go (a single assignment, atomic under the GIL --
# EN: never a partial/inconsistent read on the main thread, no explicit lock
# EN: needed). The main thread only ever COPIES these values into
# EN: state.debuffs/buffs/resources/flags -- never any heavy computation in the
# EN: engine tick.
# EN:
# EN: IMPORTANT (separation of responsibilities): state.flags holds a mix of
# EN: keys owned by DETECTION (hammer_storm_casting -- updated here) and keys
# EN: owned by THE HOOKS (hammer_storm_proc_ready, all the
# EN: _..._prev/_..._absent_ticks -- updated in tariq_pre_tick/post_tick, main
# EN: thread). state_update_fn (lightweight, below) only touches the detection
# EN: keys, never via a wholesale replacement of the dict -- a full replacement
# EN: would overwrite the hooks' keys and reproduce exactly the kind of desync
# EN: bug already seen with warden_stacks (cf. hooks.py).
TARIQ_DETECTION_WORKER_HZ = 15.0   # cf. suggestion "8-15Hz" -- capture+matching tient largement dans ce budget (~15ms sur ~67ms) · EN: cf. the "8-15Hz" suggestion -- capture+matching fits amply in this budget (~15ms out of ~67ms)

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
# EN: GCD VEIL (2026-07-14, cf. the "decreasing black veil matching the spell's
# EN: GCD" discussion) -- each bar icon darkens then re-lights progressively,
# EN: validated by direct extraction over a whole filmed session (771s):
# EN: heavy_strike/wild_swing/face_breaker/skull_crusher/hammer_storm/
# EN: chain_lightning share EXACTLY the same pattern (median gaps ~1.05-1.15s,
# EN: sequences at times identical across different icons despite very different
# EN: real cooldowns) -- this is NOT each spell's individual cooldown, it's the
# EN: SHARED GCD (single resource). culling_strike/thunder_call/focused_wrath
# EN: (off_gcd, don't consume the shared GCD) show a much more spaced pattern
# EN: consistent with THEIR OWN cooldown (4.44s/43s/86s) -- tracked separately,
# EN: one per ability.
# EN:
# EN: This signal is a visual acknowledgement from the GAME, independent of
# EN: YOLO/InputWorker -- used by hooks.py to confirm a press was actually
# EN: processed by the game (cf. PRESS_CONFIRM_WINDOW_S), not just sent by our
# EN: input.
GCD_ICON_REGIONS = ("heavy_strike", "wild_swing", "face_breaker", "skull_crusher", "hammer_storm", "chain_lightning")
OFF_GCD_ICON_REGIONS = ("culling_strike", "thunder_call", "focused_wrath")
ALL_ICON_REGIONS = GCD_ICON_REGIONS + OFF_GCD_ICON_REGIONS

# Validés sur toute la vidéo (cf. discussion) : plateau "prêt" ~90-160 selon
# l'artwork de l'icône, voile ~25-55 -- une marge de 50% du plateau roulant
# récent sépare proprement les deux sans réglage par icône.
# EN: Validated over the whole video (cf. discussion): "ready" plateau ~90-160
# EN: depending on the icon's artwork, veil ~25-55 -- a 50% margin of the recent
# EN: rolling plateau cleanly separates the two without per-icon tuning.
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
    gcd_veil_active: bool = False           # OR sur les 6 icônes GCD_ICON_REGIONS -- voile PARTAGÉ · EN: OR over the 6 GCD_ICON_REGIONS icons -- SHARED veil
    veil_culling_strike: bool = False        # off_gcd -- voile propre à SON cooldown (4.44s) · EN: off_gcd -- veil tied to ITS OWN cooldown (4.44s)
    veil_thunder_call: bool = False          # off_gcd -- voile propre à SON cooldown (43s) · EN: off_gcd -- veil tied to ITS OWN cooldown (43s)
    veil_focused_wrath: bool = False         # off_gcd -- voile propre à SON cooldown (86s) · EN: off_gcd -- veil tied to ITS OWN cooldown (86s)


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

    EN: `capturer` accepted here only to keep the same signature as
    EN: make_meiko_state_update_fn (build_engine calls both generically, cf.
    EN: main.py) -- NOT used to capture anything: the detection thread builds
    EN: its own instance (cf. _run_detection_worker/_detection_cycle below), an
    EN: mss.mss() being valid only on the thread that created it.
    EN:
    EN: chain_lightning: detection back to NORMAL (2026-07-14, the user's choice)
    EN: -- the worker no longer matches its template here. Standard detection
    EN: (is_ready_fn on the decision thread) matches the LIT icon (available)
    EN: directly, like any ability in the project. The worker only keeps
    EN: chain_lightning for the GCD veil (brightness of its region, cf.
    EN: GCD_ICON_REGIONS) -- no more template match nor chain_lightning_dim
    EN: debuff. cf. config/tariq.yaml.
    EN:
    EN: culling_strike, on the other hand, keeps INVERTED detection: template
    EN: captured GRAYED (target above 35% HP, unavailable -- confirmed by
    EN: the user), YAML condition on ITS ABSENCE (not debuff:culling_strike_dim).
    EN: Real 4.44s cooldown on top of that condition (cf. config/tariq.yaml).
    EN:
    EN: hammer_storm_cast_bar: read LTR (fill ANCHORED LEFT -- the fill's right
    EN: edge recedes leftward as the channel progresses -- verified frame by
    EN: frame on a Game Bar clip). The read value thus decreases over time (~1.0
    EN: at cast start, ~0.0 just before the end), but the SCAN ALGORITHM must
    EN: start from the left like a classic bar -- do not set "rtl" here, that was
    EN: tested and fixed. Feeds state.flags["hammer_storm_casting"], read by
    EN: tariq_pre_tick (hooks.py) to lock EVERY press (even off_gcd) while it
    EN: casts -- Hammer Storm is the main damage source and must never be cut.
    EN:
    EN: hammer_storm_empowered / skull_crusher_empowered / focused_wrath_empowered
    EN: (buffs that arm the procs, cf. TARIQ_PROC_BUFFS in hooks.py): detected by
    EN: the trained YOLO model (wardenbot/yolo_detect.py,
    EN: runs/detect/train/weights/best.pt) rather than by template matching --
    EN: hammer_storm_buff and skull_crusher_buff share the same artwork (red
    EN: spiral, only the countdown number differs) and were confused by
    EN: cv2.matchTemplate: the Hammer Storm icon alone ALSO matched the
    EN: skull_crusher_buff template at ~0.64 confidence, above its own prod
    EN: threshold (0.60) -- discovered while building the training dataset
    EN: (build_yolo_dataset.py), cf. TARIQ_BUFF_BAR_THRESHOLDS above (kept only
    EN: for dataset bootstrap). The YOLO model separates the 3 classes correctly,
    EN: including 3 buffs active simultaneously (0.94-0.98 confidence measured on
    EN: a real live capture via yolo_check.py, on 2026-07-12).
    EN:
    EN: No Hammer Storm stack counting (SIMPLIFIED on 2026-07-12, at the user's
    EN: request): the badge never shows "2" in a real game -- each proc (via
    EN: Skull Crusher) must be consumed as soon as it appears, regardless of
    EN: others pending. cf. TARIQ_PROC_BUFFS (hooks.py): a plain rising edge on
    EN: hammer_storm_empowered suffices.
    EN:
    EN: rage_bar -> state.resources["fury"] (YAML condition: fury_above:N).
    EN: Filled ANCHORED LEFT like hammer_storm_cast_bar, but unlike it, it's NOT
    EN: a timer that ONLY decreases -- a real gameplay resource that goes up and
    EN: down (regen/builders vs spend), verified frame by frame on a real clip
    EN: (sharp ~25% drops consistent with a fixed cost, gradual rises in
    EN: between). tolerance=50 + max_gap=6: the bar has a fixed visual marker
    EN: (light hourglass-shaped mark) in the middle that breaks the pure color
    EN: on a few columns regardless of fill level -- max_gap absorbs it, as for
    EN: the Hammer Storm cast bar's border bevel.
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
    # EN: BUG FOUND IN-GAME (2026-07-12): "bot lags" (round 2, after the YOLO
    # EN: throttle) -- measured via tick_timing_test.py: mss.grab() has a FIXED
    # EN: cost of about 7ms PER CALL, regardless of the region size (a 10x10
    # EN: capture costs the same as a 280x60 -- verified). This file did 5-6
    # EN: separate grab() per tick (chain_lightning, culling_strike, cast_bar,
    # EN: buff_bar, rage_bar, badge) = ~35-42ms alone, already above the 30Hz
    # EN: budget (33ms) BEFORE even YOLO/EasyOCR. Fix: ONE single enclosing
    # EN: capture per tick (the cost scales with area beyond a certain size, but
    # EN: stays far below 5-6 separate calls -- measured ~14ms for the enclosing
    # EN: box here, vs ~35-42ms before), then in-memory slicing (numpy slicing,
    # EN: near-free) of each sub-region -- same detection logic everywhere, only
    # EN: the frame source changes.
    _capture_names = [
        "chain_lightning", "culling_strike", "hammer_storm_cast_bar",
        "tariq_buff_bar", "rage_bar", "hammer_storm_stack_badge",
        # Icônes de la barre d'abilities (2026-07-14, voile GCD) -- culling_strike
        # est déjà dans cette liste pour le match "grisé", chain_lightning y reste
        # désormais UNIQUEMENT pour le voile GCD (son match grisé a été retiré,
        # détection repassée en normale, cf. plus haut), les autres s'ajoutent à
        # la boîte englobante ici plutôt que de déclencher des grab() séparés
        # (cf. le bug "5-6 grab() par tick" déjà corrigé plus haut dans ce fichier).
        # EN: Ability-bar icons (2026-07-14, GCD veil) -- culling_strike is
        # EN: already in this list for the "grayed" match, chain_lightning now
        # EN: stays here ONLY for the GCD veil (its grayed match was removed,
        # EN: detection back to normal, cf. above), the others are added to the
        # EN: enclosing box here rather than triggering separate grab() (cf. the
        # EN: "5-6 grab() per tick" bug already fixed earlier in this file).
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
    # EN: Rolling brightness history per icon (GCD veil, cf. constants above) --
    # EN: lives in the closure, never in state (state only stores the final
    # EN: boolean result, not the internal detail of the detection).
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
        de voir si le thread calculait vraiment ou attendait juste.

        EN: A full capture+matching+YOLO+OCR cycle -- runs on the detection
        EN: thread (cf. _run_detection_worker), never on the main thread. Builds
        EN: a FRESH snapshot then publishes it in one go (single, atomic
        EN: assignment) -- never mutate snapshot_box["latest"] in place.
        EN:
        EN: `capturer` received as a parameter (NOT the one from
        EN: make_tariq_state_update_fn, deliberately ignored here): an mss.mss()
        EN: is only valid on the thread that created it (cf.
        EN: wardenbot/capture.py) -- this detection thread must use ITS OWN
        EN: instance (cf. _run_detection_worker), never the one built by
        EN: build_engine() on another thread. Bug found in-game (2026-07-15):
        EN: ScreenShotError('... BitBlt') raised in a loop when the two threads
        EN: shared the same instance.
        EN:
        EN: Instrumented step by step (2026-07-13, "turn the watchdog into a
        EN: diagnostic tool"): each step (grab, each template match, each bar
        EN: read, and ultralytics' internal preprocess/inference/postprocess
        EN: breakdown for the YOLO call) is timed and accumulated in `stages` --
        EN: if the whole cycle exceeds the threshold, we log ALL that detail at
        EN: once (cf. diagnostics.py::log_pipeline_diagnostic) rather than a
        EN: single opaque duration. cpu_time (time.process_time(), not
        EN: time.monotonic()) shows whether the thread was really computing or
        EN: just waiting.
        """
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
        # EN: chain_lightning: no more template match HERE (2026-07-14) --
        # EN: detection back to NORMAL (cf. config/tariq.yaml), so is_ready_fn
        # EN: (decision thread) matches the lit icon directly, like any standard
        # EN: ability. The worker only uses it for the GCD veil (brightness of
        # EN: the region, cf. GCD_ICON_REGIONS), not for a template. The region
        # EN: stays in _capture_names for that veil.

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
            # EN: YOLO (wardenbot/yolo_detect.py) replaces the 3 template matches
            # EN: above since 2026-07-12: hammer_storm_buff and skull_crusher_buff
            # EN: share the same artwork (red spiral) and were confused by
            # EN: cv2.matchTemplate -- confirmed while building the dataset
            # EN: (build_yolo_dataset.py): the Hammer Storm icon alone ALSO
            # EN: matched skull_crusher_buff at ~0.64, above its own prod
            # EN: threshold (0.60). The trained model
            # EN: (runs/detect/train-4/weights/best.pt) separates the 3 classes
            # EN: correctly, including the 3 buffs active simultaneously
            # EN: (validated on a live capture at 0.94-0.98 confidence, cf.
            # EN: yolo_check.py).
            # EN:
            # EN: BUG FOUND IN-GAME (2026-07-12): "bot lags" -- detect() was
            # EN: called ON EVERY TICK, measured ~25-30ms/call even on GPU
            # EN: (incompressible ultralytics overhead for such a small model) --
            # EN: throttled like the OCR (YOLO_DETECT_INTERVAL_S), the 3 buffs
            # EN: only changing on discrete events, not continuously. The frame
            # EN: comes from the enclosing capture (_crop, near-free) -- only the
            # EN: detect() call itself stays throttled.
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
                # EN: FREE breakdown provided by ultralytics itself
                # EN: (results[0].speed) -- preprocess (letterbox/normalize),
                # EN: inference (forward pass), postprocess (NMS + results
                # EN: building -- ultralytics doesn't separate NMS from the rest
                # EN: of postprocess). Compared to yolo_wrapper_total_ms: if the
                # EN: sum of these 3 is WELL below the total measured here, the
                # EN: lost time is OUTSIDE ultralytics (GPU/driver wait that even
                # EN: ultralytics doesn't see).
                for stage_name, stage_ms in get_last_predict_speed_ms().items():
                    stages[f"yolo_{stage_name}_ms"] = stage_ms
                # Plus de log_slow_call individuel ici -- yolo_wrapper_total_ms
                # est déjà dans `stages`, donc un YOLO lent apparaît de toute
                # façon dans le diagnostic consolidé du cycle entier
                # (log_pipeline_diagnostic, en bas de _detection_cycle()) --
                # un check séparé ici ferait double emploi.
                # EN: No more individual log_slow_call here -- yolo_wrapper_total_ms
                # EN: is already in `stages`, so a slow YOLO shows up anyway in
                # EN: the consolidated diagnostic of the whole cycle
                # EN: (log_pipeline_diagnostic, at the bottom of
                # EN: _detection_cycle()) -- a separate check here would be
                # EN: redundant.
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

        snapshot_box["latest"] = snap   # publication atomique -- jamais de mutation en place · EN: atomic publication -- never an in-place mutation

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
        # EN: Instance dedicated to THIS thread (cf. _detection_cycle above) --
        # EN: never reuse the `capturer` received by make_tariq_state_update_fn,
        # EN: built on another thread (build_engine).
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
            # EN: BUG FOUND IN-GAME (2026-07-12): analysis of a real session's
            # EN: debug.log -- 6 episodes of 8 to 24s where the WHOLE snapshot
            # EN: (fury, chain_lightning_dim, culling_strike_dim...) stays frozen
            # EN: identically, while the main thread keeps ticking normally
            # EN: (~60Hz without interruption) -- so not a crash, not an
            # EN: individually-slow _detection_cycle() (NONE of these 6 windows
            # EN: appears in capture_watchdog.log). Blind spot of the previous
            # EN: instrumentation: it only measured the duration of
            # EN: _detection_cycle() itself, never the time spent IN
            # EN: stop_event.wait() nor a possible OS wake-up delay of the thread
            # EN: -- a block there would be invisible. This check compares THIS
            # EN: cycle's start to the previous one's start (thus previous cycle
            # EN: + its wait() included): if the gap far exceeds period (~67ms),
            # EN: something prevented this thread from running, whatever the exact
            # EN: reason -- logged separately from "full cycle" to distinguish
            # EN: "the work took time" from "the thread simply wasn't
            # EN: woken/executed on time".
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
            # EN: ROBUSTNESS (2026-07-12): a Python daemon thread that raises an
            # EN: uncaught exception dies SILENTLY -- no traceback visible
            # EN: in-game (just a possible warning in a console we don't watch
            # EN: while playing), and no more new snapshot published afterward.
            # EN: state_update_fn would keep reading the LAST known snapshot
            # EN: (never refreshed) indefinitely without ever crashing the main
            # EN: thread -- exactly the kind of "bot seems frozen/lagging for no
            # EN: visible reason" hunted down several times in this project (cf.
            # EN: CLAUDE.md). A failing cycle (empty frame, mss glitch, YOLO
            # EN: hiccup) must just be logged and retried on the next cycle, not
            # EN: kill the thread.
            try:
                _detection_cycle(worker_capturer)
            except Exception as exc:
                log_error("tariq _detection_cycle()", exc)
            # Le diagnostic détaillé "cycle complet" (étapes + CPU time + GPU)
            # est désormais journalisé DEPUIS _detection_cycle() elle-même
            # (cf. log_pipeline_diagnostic ci-dessus) -- un 2e check ici
            # ferait doublon sur le même évènement, en moins détaillé.
            # EN: The detailed "full cycle" diagnostic (steps + CPU time + GPU)
            # EN: is now logged FROM _detection_cycle() itself (cf.
            # EN: log_pipeline_diagnostic above) -- a 2nd check here would
            # EN: duplicate the same event, in less detail.
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
    # EN: state_update_fn (main thread, called every engine tick): does NO more
    # EN: capture/detection -- just a copy of the latest known values
    # EN: (snapshot_box["latest"], published by the detection thread above).
    # EN: Copy key by key, NEVER state.flags = {...} wholesale -- cf. the comment
    # EN: above TariqDetectionSnapshot: state.flags also holds keys owned by the
    # EN: hooks (hammer_storm_proc_ready, etc.) that a full replacement would
    # EN: overwrite.
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
