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
from typing import Optional

from wardenbot.bar_reader import read_fill_pct, read_fill_pct_colorless
from wardenbot.capture import ScreenCapture
from wardenbot.diagnostics import CAPTURE_WATCHDOG_THRESHOLD_S, log_error, log_pipeline_diagnostic, log_slow_call
from wardenbot.engine import CombatState
from wardenbot.freshness import DetectionMeta, FreshnessPolicy
from wardenbot.matcher import TemplateMatcher
from wardenbot.ocr import read_countdown_seconds
from wardenbot.yolo_detect import MEIKO_YOLO_WEIGHTS_PATH, detect, detect_with_boxes, get_last_predict_speed_ms

# =============================================================================
# Fraîcheur (Phase 0, 2026-07-14, cf. discussion architecture) : une politique
# PAR SOURCE, pas une seule -- un buff YOLO et une lecture OCR ne vieillissent
# pas à la même vitesse (cf. wardenbot/freshness.py). fresh_ms/stale_ms calés
# sur les intervalles de throttle RÉELS mesurés dans ce fichier
# (MEIKO_YOLO_DETECT_INTERVAL_S/MEIKO_OCR_INTERVAL_S/YOLO_DETECT_INTERVAL_S/
# TARIQ_DETECTION_WORKER_HZ ci-dessous) -- valeurs de départ raisonnables, PAS
# encore validées empiriquement en jeu réel (même statut que d'autres seuils
# du projet avant confirmation, cf. HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
# et son historique de calibration). Rien ne consomme encore ces politiques
# pour décider quoi que ce soit (cf. wardenbot/freshness.py -- ce module ne
# fait QUE classifier, jamais agir) : c'est le socle mesurable, pas encore le
# câblage dans hooks.py.
# EN: Freshness (Phase 0, 2026-07-14, cf. architecture discussion): a policy
# EN: PER SOURCE, not a single one -- a YOLO buff and an OCR reading don't age
# EN: at the same speed (cf. wardenbot/freshness.py). fresh_ms/stale_ms are
# EN: calibrated on the REAL throttle intervals measured in this file
# EN: (MEIKO_YOLO_DETECT_INTERVAL_S/MEIKO_OCR_INTERVAL_S/YOLO_DETECT_INTERVAL_S/
# EN: TARIQ_DETECTION_WORKER_HZ below) -- reasonable starting values, NOT yet
# EN: empirically validated in a real game (same status as other thresholds in
# EN: this project before confirmation, cf.
# EN: HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED and its calibration history).
# EN: Nothing consumes these policies yet to decide anything (cf.
# EN: wardenbot/freshness.py -- that module ONLY classifies, never acts): this
# EN: is the measurable foundation, not yet the wiring into hooks.py.
MEIKO_YOLO_FRESHNESS_POLICY = FreshnessPolicy(fresh_ms=200, stale_ms=600)     # throttle 150ms, cf. MEIKO_YOLO_DETECT_INTERVAL_S plus bas · EN: 150ms throttle, cf. MEIKO_YOLO_DETECT_INTERVAL_S below
MEIKO_OCR_FRESHNESS_POLICY = FreshnessPolicy(fresh_ms=600, stale_ms=2000)     # throttle 400ms + seulement quand spirited_strikes est détecté, cf. MEIKO_OCR_INTERVAL_S plus bas · EN: 400ms throttle + only when spirited_strikes is detected, cf. MEIKO_OCR_INTERVAL_S below
TARIQ_YOLO_FRESHNESS_POLICY = FreshnessPolicy(fresh_ms=200, stale_ms=600)     # throttle 150ms, cf. YOLO_DETECT_INTERVAL_S plus bas · EN: 150ms throttle, cf. YOLO_DETECT_INTERVAL_S below
TARIQ_BARS_FRESHNESS_POLICY = FreshnessPolicy(fresh_ms=150, stale_ms=400)     # rafraîchi à CHAQUE cycle du thread détection (~67ms à 15Hz, cf. TARIQ_DETECTION_WORKER_HZ) -- une valeur STALE/EXPIRED ici signalerait le thread lui-même bloqué (cf. CLAUDE.md, épisodes de snapshot figé), pas juste un throttle normal · EN: refreshed on EVERY detection thread cycle (~67ms at 15Hz, cf. TARIQ_DETECTION_WORKER_HZ) -- a STALE/EXPIRED value here would signal the thread itself stuck (cf. CLAUDE.md, frozen-snapshot episodes), not just normal throttling

# BUG TROUVÉ EN JEU (2026-07-16, rapporté par l'utilisateur) : "la rotation ne fait
# que spam le combo Spirited Vortex/Strikes" -- root cause réelle : la
# détection tournait sur cv2.matchTemplate (matcher.py), dont la confiance
# mesurée pour spirited_vortex/spirited_strikes oscille entre 0.55 et 0.73
# selon le chiffre de décompte affiché sur l'icône (sous ou pile au seuil de
# prod 0.70, cf. discussion) -- state.buffs["spirited_vortex"/"spirited_strikes"]
# scintillait False par intermittence même buff up, empêchant
# meiko_defense_primed (hooks.py) de jamais se stabiliser à True -> le combo
# défensif se rouvrait en boucle. Fix : détection par le modèle YOLO entraîné
# spécifiquement pour Meiko (cf. build_yolo_dataset.py --character meiko,
# train_yolo.py) -- P>=0.994/R=1.0 sur validation -- au lieu du template
# matching. Même architecture que Tariq (thread de détection dédié, cf.
# _run_meiko_detection_worker plus bas) : YOLO/OCR sont trop coûteux pour
# tourner dans le tick principal (~60Hz).
MEIKO_DETECTION_WORKER_HZ = 10.0
MEIKO_YOLO_DETECT_INTERVAL_S = 0.15   # même easing que Tariq (YOLO_DETECT_INTERVAL_S) -- cf. state_update.py plus bas
MEIKO_YOLO_CONF = 0.5

# DEMANDE DE L'UTILISATEUR (2026-07-16) : refresh proactif de Spirited Strikes 5s
# avant expiration (buff de 30s), au lieu d'attendre qu'il tombe à False --
# nécessite de lire le VRAI compte à rebours affiché sur l'icône (OCR), pas
# une estimation dérivée d'un timer local (cf. CLAUDE.md, principe "jamais
# estimer un état détectable à l'écran" -- ici on détecte la vraie valeur
# affichée par le jeu, on ne l'estime pas). OCR nettement plus coûteux que
# YOLO (cf. ocr.py) -- throttlé plus fort, et seulement quand
# spirited_strikes est déjà détecté présent (rien à lire sinon).
MEIKO_OCR_INTERVAL_S = 0.4

# BUG TROUVÉ EN JEU (2026-07-16, comparaison log/vidéo avec l'utilisateur) : l'OCR
# lisait "4.0" en continu pendant 19s+ alors que la vidéo montre clairement
# "26" affiché sur l'icône au même instant -- la boîte YOLO complète (toute
# l'icône, ~70x73px) inclut trop de l'artwork environnant (spirale/éclair
# violet à fort contraste, cf. templates/meiko/spirited_strikes.png) qui
# survit à la binarisation Otsu (ocr.py::_preprocess) et perturbe EasyOCR.
# Mesuré sur le template capturé : le chiffre n'occupe qu'une fraction de la
# boîte, environ x:[20%,82%] y:[45%,80%] -- on resserre le crop OCR à cette
# sous-région plutôt que toute la boîte détectée par YOLO.
# EN: BUG FOUND IN-GAME (2026-07-16, log/video comparison with the user): OCR
# EN: kept reading "4.0" for 19s+ while the video clearly shows "26" displayed
# EN: on the icon at that same instant -- the full YOLO box (the whole icon,
# EN: ~70x73px) includes too much of the surrounding artwork (high-contrast
# EN: purple spiral/lightning, cf. templates/meiko/spirited_strikes.png) which
# EN: survives Otsu binarization (ocr.py::_preprocess) and confuses EasyOCR.
# EN: Measured on the captured template: the number only occupies a fraction
# EN: of the box, roughly x:[20%,82%] y:[45%,80%] -- the OCR crop is narrowed
# EN: to this sub-region instead of the whole YOLO-detected box.
STRIKES_COUNTDOWN_CROP_X = (0.20, 0.82)
STRIKES_COUNTDOWN_CROP_Y = (0.45, 0.80)


@dataclass
class MeikoDetectionSnapshot:
    spirited_vortex: bool = False
    spirited_strikes: bool = False
    warden_of_the_temple_3: bool = False
    spirited_strikes_remaining_s: Optional[float] = None
    stormfall: float = 0.0
    # Fraîcheur (Phase 0, 2026-07-14) : 3 horodatages, PAS un seul -- ce
    # snapshot mélange 3 cadences réelles différentes (cf. commentaire
    # MEIKO_YOLO_FRESHNESS_POLICY plus haut) : stormfall (recalculé à CHAQUE
    # cycle du thread, ~100ms), vortex/strikes/warden_3 (YOLO, throttlé
    # ~150ms), strikes_remaining_s (OCR, throttlé ~400ms ET seulement quand
    # spirited_strikes est détecté). created_at reste MUTABLE ici (comme tout
    # le reste du dataclass) -- pas de frozen=True, ce Snapshot est construit
    # champ par champ pendant tout le cycle avant publication atomique (cf.
    # wardenbot/freshness.py::DetectionMeta pour l'objet-valeur figé qui, lui,
    # encapsule un seul de ces horodatages une fois recopié dans CombatState).
    # EN: Freshness (Phase 0, 2026-07-14): 3 timestamps, NOT a single one --
    # EN: this snapshot mixes 3 genuinely different cadences (cf. the
    # EN: MEIKO_YOLO_FRESHNESS_POLICY comment above): stormfall (recomputed on
    # EN: EVERY thread cycle, ~100ms), vortex/strikes/warden_3 (YOLO, throttled
    # EN: ~150ms), strikes_remaining_s (OCR, throttled ~400ms AND only when
    # EN: spirited_strikes is detected). created_at stays MUTABLE here (like
    # EN: the rest of the dataclass) -- no frozen=True, this Snapshot is built
    # EN: field by field throughout the cycle before atomic publication (cf.
    # EN: wardenbot/freshness.py::DetectionMeta for the frozen value object
    # EN: that, itself, wraps a single one of these timestamps once copied
    # EN: into CombatState).
    created_at: float = 0.0        # cycle entier (stormfall) · EN: whole cycle (stormfall)
    yolo_created_at: float = 0.0   # vortex/strikes/warden_3 · EN: vortex/strikes/warden_3
    ocr_created_at: Optional[float] = None   # strikes_remaining_s -- None tant qu'aucune lecture OCR n'a jamais eu lieu · EN: strikes_remaining_s -- None as long as no OCR reading has ever happened


def _run_meiko_detection_worker(regions: dict, snapshot_box: dict, stop_event: threading.Event) -> None:
    # Instance dédiée à CE thread -- un mss.mss() n'est valide que sur le
    # thread qui l'a créé (cf. wardenbot/capture.py, même bug BitBlt déjà
    # rencontré côté Tariq). Jamais le `capturer` reçu par
    # make_meiko_state_update_fn, construit sur un autre thread.
    worker_capturer = ScreenCapture()
    worker_matcher = TemplateMatcher("templates/meiko")   # cf. wardenbot/game_config.py CHARACTERS["meiko"]["templates"]
    period = 1.0 / MEIKO_DETECTION_WORKER_HZ
    last_yolo_t = 0.0
    last_ocr_t = 0.0
    last_boxes: list = []

    while not stop_event.is_set():
        start = time.monotonic()
        try:
            buff_frame = worker_capturer.grab(regions["buff_bar"])
            snap = MeikoDetectionSnapshot()

            now = time.monotonic()
            if now - last_yolo_t >= MEIKO_YOLO_DETECT_INTERVAL_S:
                last_yolo_t = now
                last_boxes = detect_with_boxes(buff_frame, conf=MEIKO_YOLO_CONF, weights_path=MEIKO_YOLO_WEIGHTS_PATH)

            best: dict[str, tuple[float, tuple[float, float, float, float]]] = {}
            for class_name, confidence, box in last_boxes:
                if confidence > best.get(class_name, (0.0, None))[0]:
                    best[class_name] = (confidence, box)

            snap.spirited_vortex = "spirited_vortex" in best
            snap.spirited_strikes = "spirited_strikes" in best
            snap.warden_of_the_temple_3 = "warden_of_the_temple_3" in best

            # Compte à rebours de Spirited Strikes (OCR sur une sous-région de
            # la boîte YOLO -- seule façon de savoir OÙ lire, la barre étant
            # dynamique/centrée, cf. commentaire module plus haut). Sous-crop
            # (cf. STRIKES_COUNTDOWN_CROP_X/Y plus haut) : le chiffre n'occupe
            # qu'une fraction de l'icône, le reste de l'artwork perturbe l'OCR.
            if snap.spirited_strikes and now - last_ocr_t >= MEIKO_OCR_INTERVAL_S:
                last_ocr_t = now
                _, (cx, cy, bw, bh) = best["spirited_strikes"]
                fh, fw = buff_frame.shape[:2]
                box_x1 = (cx - bw / 2) * fw
                box_y1 = (cy - bh / 2) * fh
                box_w = bw * fw
                box_h = bh * fh
                x1 = max(0, int(box_x1 + STRIKES_COUNTDOWN_CROP_X[0] * box_w))
                y1 = max(0, int(box_y1 + STRIKES_COUNTDOWN_CROP_Y[0] * box_h))
                x2 = min(fw, int(box_x1 + STRIKES_COUNTDOWN_CROP_X[1] * box_w))
                y2 = min(fh, int(box_y1 + STRIKES_COUNTDOWN_CROP_Y[1] * box_h))
                if x2 > x1 and y2 > y1:
                    ocr_crop = buff_frame[y1:y2, x1:x2]
                    snap.spirited_strikes_remaining_s = read_countdown_seconds(ocr_crop)

            # BUG TROUVÉ EN JEU (2026-07-16, rapporté par l'utilisateur) : stormfall
            # restait bloqué à 0.0 en continu -- la région stormfall_lit
            # (unique, binaire) datait d'avant l'alignement de l'UI de Meiko
            # sur celle de Tariq, elle pointait dans le vide. Root cause plus
            # profonde : Stormfall a en réalité DEUX charges/orbes distincts
            # (confirmé par l'utilisateur, "pour bénéficier des deux orbes") -- un
            # seul indicateur binaire ne peut pas représenter "1 orbe sur 2"
            # correctement, il fallait deux régions/templates, un par orbe.
            # 0 orbe allumé -> 0.0, 1 -> 0.5, 2 -> 1.0 (>= 0.999, cf. hooks.py :
            # Lashing Stormkick ne doit spend QUE quand les deux orbes sont
            # allumés).
            orb_1_lit = worker_matcher.is_match(worker_capturer.grab(regions["stormfall_orb_1"]), "stormfall_orb_1")
            orb_2_lit = worker_matcher.is_match(worker_capturer.grab(regions["stormfall_orb_2"]), "stormfall_orb_2")
            snap.stormfall = (int(orb_1_lit) + int(orb_2_lit)) / 2.0

            # Fraîcheur (Phase 0, 2026-07-14) : réutilise last_yolo_t/last_ocr_t
            # (déjà suivis ci-dessus pour le throttle lui-même) -- ce sont
            # EXACTEMENT les horodatages du dernier vrai appel YOLO/OCR, pas
            # besoin d'un 3e mécanisme de suivi. last_ocr_t reste à 0.0 tant
            # qu'aucune lecture OCR n'a jamais eu lieu cette session -> None
            # plutôt qu'un faux horodatage (cf. state.gcd_until, même
            # convention 0.0="jamais" ailleurs dans le projet).
            # EN: Freshness (Phase 0, 2026-07-14): reuses last_yolo_t/last_ocr_t
            # EN: (already tracked above for the throttle itself) -- these are
            # EN: EXACTLY the timestamps of the last real YOLO/OCR call, no need
            # EN: for a 3rd tracking mechanism. last_ocr_t stays at 0.0 as long
            # EN: as no OCR reading has ever happened this session -> None
            # EN: rather than a fake timestamp (cf. state.gcd_until, same
            # EN: 0.0="never" convention elsewhere in the project).
            snap.created_at = now
            snap.yolo_created_at = last_yolo_t
            snap.ocr_created_at = last_ocr_t if last_ocr_t > 0.0 else None

            snapshot_box["latest"] = snap   # publication atomique -- jamais de mutation en place
        except Exception as exc:
            # ROBUSTESSE (même principe que _run_detection_worker côté Tariq,
            # cf. plus bas) : un cycle qui échoue est journalisé et retenté,
            # plutôt que de tuer silencieusement ce thread daemon.
            log_error("meiko _run_meiko_detection_worker()", exc)

        elapsed = time.monotonic() - start
        if elapsed > CAPTURE_WATCHDOG_THRESHOLD_S:
            log_slow_call("_run_meiko_detection_worker (cycle)", elapsed)
        stop_event.wait(max(0.0, period - elapsed))


def make_meiko_state_update_fn(capturer: ScreenCapture, matcher: TemplateMatcher, regions: dict):
    """
    Lit à l'écran les buffs/debuffs/ressources et remplit
    state.buffs/state.debuffs/state.resources -- appelé en tout premier à
    chaque tick (cf. RotationEngine.state_update_fn).

    `capturer`/`matcher` reçus ici uniquement pour garder la même signature
    que make_tariq_state_update_fn (build_engine appelle les deux de façon
    générique, cf. main.py) -- PAS utilisés directement : le thread de
    détection (_run_meiko_detection_worker) construit ses propres instances,
    un mss.mss() n'étant valide que sur le thread qui l'a créé.

    DÉTECTION PAR YOLO (2026-07-16, remplace le template matching) : cf.
    commentaire MEIKO_DETECTION_WORKER_HZ plus haut pour le bug que ça
    corrige (spam du combo défensif dû à une détection peu fiable). Même
    architecture que Tariq : thread dédié qui publie un instantané immuable,
    cette fonction ne fait que le copier dans `state` -- jamais de calcul
    lourd dans le tick principal.

    EN: Reads on-screen buffs/debuffs/resources and fills
    EN: state.buffs/state.debuffs/state.resources -- called first of all every
    EN: tick (cf. RotationEngine.state_update_fn).
    EN:
    EN: `capturer`/`matcher` received here only to keep the same signature as
    EN: make_tariq_state_update_fn (build_engine calls both generically, cf.
    EN: main.py) -- NOT used directly: the detection thread
    EN: (_run_meiko_detection_worker) builds its own instances, an mss.mss()
    EN: being valid only on the thread that created it.
    EN:
    EN: YOLO-BASED DETECTION (2026-07-16, replaces template matching): cf. the
    EN: MEIKO_DETECTION_WORKER_HZ comment above for the bug this fixes
    EN: (defensive combo spam caused by unreliable detection). Same
    EN: architecture as Tariq: a dedicated thread publishes an immutable
    EN: snapshot, this function only copies it into `state` -- never any heavy
    EN: computation in the main tick.
    """
    snapshot_box = {"latest": MeikoDetectionSnapshot()}
    stop_event = threading.Event()
    worker_thread = threading.Thread(
        target=_run_meiko_detection_worker, args=(regions, snapshot_box, stop_event),
        daemon=True, name="meiko-detection-worker",
    )
    worker_thread.start()

    def state_update_fn(state: CombatState) -> None:
        snap = snapshot_box["latest"]
        state.buffs["spirited_vortex"] = snap.spirited_vortex
        state.buffs["spirited_strikes"] = snap.spirited_strikes
        state.debuffs["warden_of_the_temple_3"] = snap.warden_of_the_temple_3
        state.resources["stormfall"] = snap.stormfall
        if snap.spirited_strikes_remaining_s is not None:
            state.resources["spirited_strikes_remaining_s"] = snap.spirited_strikes_remaining_s
        elif not snap.spirited_strikes:
            # Buff absent -> pas de compte à rebours à lire, on efface la
            # dernière valeur connue pour ne pas laisser une valeur périmée
            # déclencher un refresh proactif à tort.
            # EN: Buff absent -> no countdown to read, clear the last known
            # EN: value so a stale value doesn't wrongly trigger a proactive
            # EN: refresh.
            state.resources.pop("spirited_strikes_remaining_s", None)
        # earthfall_lit pas encore capturé -> state.resources["earthfall"] reste
        # à son défaut (0.0 via .get()) tant que le template n'existe pas.
        # EN: earthfall_lit not captured yet -> state.resources["earthfall"]
        # EN: stays at its default (0.0 via .get()) while the template doesn't
        # EN: exist.

        # Fraîcheur (Phase 0, 2026-07-14) : copie les horodatages du snapshot
        # dans state.detection_meta, SÉPARÉMENT des valeurs détectées
        # ci-dessus (cf. CombatState::detection_meta, engine.py). ocr n'est
        # copié QUE s'il a déjà eu lieu au moins une fois (cf.
        # MeikoDetectionSnapshot.ocr_created_at, None tant que jamais lu).
        # EN: Freshness (Phase 0, 2026-07-14): copies the snapshot's
        # EN: timestamps into state.detection_meta, SEPARATELY from the
        # EN: detected values above (cf. CombatState::detection_meta,
        # EN: engine.py). ocr is only copied IF it has happened at least once
        # EN: (cf. MeikoDetectionSnapshot.ocr_created_at, None as long as never
        # EN: read).
        state.detection_meta["meiko_yolo"] = DetectionMeta(created_at=snap.yolo_created_at)
        if snap.ocr_created_at is not None:
            state.detection_meta["meiko_ocr"] = DetectionMeta(created_at=snap.ocr_created_at)

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
# IMPORTANT (séparation des responsabilités) : AVANT la migration CombatState
# (2026-07-14), state.flags était un seul dict fourre-tout mélangeant des clés
# possédées par LA DÉTECTION (hammer_storm_casting -- mise à jour ici) et des
# clés possédées par LES HOOKS (hammer_storm_proc_ready, tous les
# _..._prev/_..._absent_ticks -- mises à jour dans tariq_pre_tick/post_tick,
# thread principal), avec pour seule garantie un commentaire "ne jamais
# écraser en bloc". C'est maintenant STRUCTUREL : state.detection et
# state.hooks sont deux dicts distincts, state_update_fn (léger, ci-dessous)
# n'écrit QUE dans state.detection (hammer_storm_casting) -- il ne peut plus,
# même par erreur, écraser une clé possédée par les hooks (cf. hooks.py).
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
# EN: IMPORTANT (separation of responsibilities): BEFORE the CombatState
# EN: migration (2026-07-14), state.flags was a single catch-all dict mixing
# EN: keys owned by DETECTION (hammer_storm_casting -- updated here) and keys
# EN: owned by THE HOOKS (hammer_storm_proc_ready, all the
# EN: _..._prev/_..._absent_ticks -- updated in tariq_pre_tick/post_tick, main
# EN: thread), with only a comment "never overwrite wholesale" as a guarantee.
# EN: This is now STRUCTURAL: state.detection and state.hooks are two distinct
# EN: dicts, state_update_fn (lightweight, below) only writes into
# EN: state.detection (hammer_storm_casting) -- it can no longer, even by
# EN: mistake, overwrite a key owned by the hooks (cf. hooks.py).
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
    # Fraîcheur (Phase 0, 2026-07-14) : 2 horodatages -- created_at pour tout
    # ce qui est rafraîchi À CHAQUE cycle du thread (cast_bar/rage_bar/
    # culling_strike/voiles GCD, cf. TARIQ_BARS_FRESHNESS_POLICY plus haut) ;
    # yolo_created_at spécifiquement pour les 3 buffs YOLO (throttlés
    # indépendamment, ~150ms, cf. TARIQ_YOLO_FRESHNESS_POLICY). Mutable comme
    # le reste de ce dataclass -- cf. commentaire équivalent sur
    # MeikoDetectionSnapshot plus haut (même raison : construction
    # incrémentale avant publication atomique, frozen=True casserait ça).
    # EN: Freshness (Phase 0, 2026-07-14): 2 timestamps -- created_at for
    # EN: everything refreshed on EVERY thread cycle (cast_bar/rage_bar/
    # EN: culling_strike/GCD veils, cf. TARIQ_BARS_FRESHNESS_POLICY above);
    # EN: yolo_created_at specifically for the 3 YOLO buffs (throttled
    # EN: independently, ~150ms, cf. TARIQ_YOLO_FRESHNESS_POLICY). Mutable like
    # EN: the rest of this dataclass -- cf. the equivalent comment on
    # EN: MeikoDetectionSnapshot above (same reason: incremental construction
    # EN: before atomic publication, frozen=True would break that).
    created_at: float = 0.0
    yolo_created_at: float = 0.0


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
    Alimente state.detection["hammer_storm_casting"], lu par tariq_pre_tick
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
    EN: tested and fixed. Feeds state.detection["hammer_storm_casting"], read by
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

        # Fraîcheur (Phase 0, 2026-07-14) : réutilise last_yolo_detect["t"]
        # (déjà suivi plus haut pour le throttle YOLO lui-même) -- reste à 0.0
        # tant qu'aucun appel YOLO n'a jamais eu lieu cette session, ce qui
        # donne naturellement un âge énorme (EXPIRED) sans traitement
        # spécial, cf. wardenbot/freshness.py.
        # EN: Freshness (Phase 0, 2026-07-14): reuses last_yolo_detect["t"]
        # EN: (already tracked above for the YOLO throttle itself) -- stays at
        # EN: 0.0 as long as no YOLO call has ever happened this session, which
        # EN: naturally yields a huge age (EXPIRED) with no special handling,
        # EN: cf. wardenbot/freshness.py.
        snap.created_at = _icon_now
        snap.yolo_created_at = last_yolo_detect["t"]

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
    # détection ci-dessus). Copie clé par clé dans state.detection -- ce dict
    # n'est écrit QUE par ce thread (cf. CombatState, engine.py) ; les clés des
    # hooks (hammer_storm_proc_ready, etc.) vivent dans state.hooks, un dict
    # séparé qu'aucun remplacement en bloc ici ne peut plus écraser.
    # EN: state_update_fn (main thread, called every engine tick): does NO more
    # EN: capture/detection -- just a copy of the latest known values
    # EN: (snapshot_box["latest"], published by the detection thread above).
    # EN: Copy key by key into state.detection -- this dict is written ONLY by
    # EN: this thread (cf. CombatState, engine.py); the hooks' keys
    # EN: (hammer_storm_proc_ready, etc.) live in state.hooks, a separate dict
    # EN: that no wholesale replacement here can overwrite anymore.
    def state_update_fn(state: CombatState) -> None:
        snap = snapshot_box["latest"]
        state.debuffs["culling_strike_dim"] = snap.culling_strike_dim
        state.resources["hammer_storm_cast_pct"] = snap.hammer_storm_cast_pct
        state.detection["hammer_storm_casting"] = snap.hammer_storm_casting
        state.buffs["hammer_storm_empowered"] = snap.hammer_storm_empowered
        state.buffs["skull_crusher_empowered"] = snap.skull_crusher_empowered
        state.buffs["focused_wrath_empowered"] = snap.focused_wrath_empowered
        state.resources["fury"] = snap.fury
        state.buffs["gcd_veil_active"] = snap.gcd_veil_active
        state.buffs["veil_culling_strike"] = snap.veil_culling_strike
        state.buffs["veil_thunder_call"] = snap.veil_thunder_call
        state.buffs["veil_focused_wrath"] = snap.veil_focused_wrath

        # Fraîcheur (Phase 0, 2026-07-14) : cf. commentaire équivalent côté
        # Meiko plus haut -- created_at couvre tout ce qui est lu à chaque
        # cycle (bars/culling_strike/voiles GCD), yolo_created_at les 3 buffs
        # YOLO spécifiquement (throttlés indépendamment).
        # EN: Freshness (Phase 0, 2026-07-14): cf. the equivalent comment on
        # EN: the Meiko side above -- created_at covers everything read every
        # EN: cycle (bars/culling_strike/GCD veils), yolo_created_at the 3 YOLO
        # EN: buffs specifically (throttled independently).
        state.detection_meta["tariq_bars"] = DetectionMeta(created_at=snap.created_at)
        state.detection_meta["tariq_yolo"] = DetectionMeta(created_at=snap.yolo_created_at)

    return state_update_fn


# =============================================================================
# VIGOUR (Healer) -- Phase 1 de l'industrialisation Vigour (2026-07-15, cf.
# discussion architecture : Targeting/CombatState/Hooks/YAML). Lecture des 4
# emplacements du cadre de groupe (party frames) : HP% via barre de
# remplissage (wardenbot/bar_reader.py, même fonction déjà utilisée pour
# rage_bar/hammer_storm_cast_bar), cleanse via template d'icône de debuff.
#
# SYNCHRONE, PAS de thread de détection dédié comme Meiko/Tariq : ceux-ci n'en
# ont un que parce qu'un problème réel de perf/fiabilité l'a justifié après
# coup (cf. CLAUDE.md, bug #3 -- spam défensif dû à une ESTIMATION au lieu
# d'une détection réelle ; le thread YOLO Meiko est venu encore plus tard,
# 2026-07-16, pour un problème de fiabilité différent). Rien ne prouve qu'un
# thread séparé soit nécessaire ici avant même la première calibration en jeu
# -- à réévaluer via capture_watchdog.log si les cycles s'avèrent trop lents.
# EN: VIGOUR (Healer) -- Phase 1 of the Vigour industrialization (2026-07-15,
# EN: cf. architecture discussion: Targeting/CombatState/Hooks/YAML). Reads
# EN: the 4 party-frame slots: HP% via fill bar (wardenbot/bar_reader.py, the
# EN: same function already used for rage_bar/hammer_storm_cast_bar), cleanse
# EN: via a debuff-icon template.
# EN:
# EN: SYNCHRONOUS, NO dedicated detection thread like Meiko/Tariq: those only
# EN: got one because a real perf/reliability problem justified it after the
# EN: fact (cf. CLAUDE.md, bug #3 -- defensive spam caused by an ESTIMATE
# EN: instead of a real detection; Meiko's YOLO thread came even later,
# EN: 2026-07-16, for a different reliability problem). Nothing proves a
# EN: separate thread is needed here before even the first in-game
# EN: calibration -- re-evaluate via capture_watchdog.log if cycles prove too
# EN: slow.
# =============================================================================
VIGOUR_PARTY_FRESHNESS_POLICY = FreshnessPolicy(fresh_ms=100, stale_ms=400)   # lecture synchrone à chaque tick (~30Hz, cf. CHARACTERS["vigour"]["tick_hz"]) -- valeurs de départ, PAS encore validées en jeu (même statut que les autres FreshnessPolicy de ce fichier) · EN: synchronous read every tick (~30Hz, cf. CHARACTERS["vigour"]["tick_hz"]) -- starting values, NOT yet validated in-game (same status as this file's other FreshnessPolicy)

PARTY_SLOTS = (1, 2, 3, 4)

# MESURÉ (2026-07-15, analyse de C:\Users\Herma\Videos\Captures\fellowship
# 2026-07-15 19-23-21.mp4, 2560x1440, cf. templates/vigour/HP_party*.png) --
# PAS un placeholder : chaque emplacement de groupe a sa PROPRE couleur de
# remplissage (thème par personnage/rôle : Buxtonox=orange, SoW=bleu,
# Merim=rouge, Commah=blanc-crème pour le joueur lui-même), contrairement à
# rage_bar/hammer_storm_cast_bar (Tariq) qui n'ont qu'une seule couleur fixe.
# UNE VALEUR PAR SLOT NUMÉRIQUE (1-4), PAS par nom de joueur -- l'ordre des
# slots dans le cadre de groupe est un fait d'UI (position gauche->droite),
# jamais lié à qui est effectivement dans le groupe ce jour-là.
#
# RISQUE CONNU (pas caché) : party_3 (rouge sombre, BGR~(25,17,67)) a une
# luminosité globale proche du fond d'écran sombre (~(40-50,35-40,38-40) BGR
# mesuré sur cette même vidéo) -- moins de marge de séparation que
# party_1/2/4, contrairement à ces derniers nettement plus saturés/lumineux.
# À valider en jeu avant de faire confiance à sa lecture dans un vrai combat
# (cf. checklist calibration -- action manuelle, pas résolu ici).
# EN: MEASURED (2026-07-15, analysis of C:\Users\Herma\Videos\Captures\
# EN: fellowship 2026-07-15 19-23-21.mp4, 2560x1440, cf.
# EN: templates/vigour/HP_party*.png) -- NOT a placeholder: each party slot
# EN: has its OWN fill color (per-character/role theme: Buxtonox=orange,
# EN: SoW=blue, Merim=red, Commah=cream-white for the player himself), unlike
# EN: rage_bar/hammer_storm_cast_bar (Tariq) which only have one fixed color.
# EN: ONE VALUE PER NUMERIC SLOT (1-4), NOT per player name -- slot order in
# EN: the party frame is a UI fact (left->right position), never tied to who
# EN: actually is in the group on a given day.
# EN:
# EN: KNOWN RISK (not hidden): party_3 (dark red, BGR~(25,17,67)) has overall
# EN: brightness close to the dark screen background (~(40-50,35-40,38-40)
# EN: BGR measured on this same video) -- less separation margin than
# EN: party_1/2/4, which are clearly more saturated/bright by comparison. To
# EN: validate in-game before trusting its reading in a real fight (cf.
# EN: calibration checklist -- manual action, not resolved here).
# DÉCOUVERTE CRITIQUE (2026-07-15, 2e vidéo analysée : fellowship
# 2026-07-15 22-45-35.mp4, même résolution, groupe DIFFÉRENT) : ces couleurs
# NE SONT PAS FIXES PAR SLOT. Vidéo 1 (Buxtonox/SoW/Merim/Commah) : slot1
# orange, slot2 bleu, slot3 rouge, slot4 blanc. Vidéo 2 (Elayra/Kaschy/Roxo/
# Commah) : slot1 beige, slot2 orange, slot3 bleu, slot4 blanc -- l'orange et
# le bleu ont changé de SLOT d'une session à l'autre. Seul slot4 (le joueur
# lui-même) reste blanc-crème dans les deux vidéos -- probablement une
# convention stable ("ma propre frame"), pas une coïncidence.
#
# Conséquence directe : les valeurs ci-dessous ne sont fiables QUE pour une
# session avec exactement CETTE composition de groupe -- une nouvelle
# composition change les couleurs et casse ces lectures silencieusement
# (aucune erreur levée, juste un mauvais %). Essayé un détecteur générique
# par SATURATION (cf. wardenbot/bar_reader.py::read_fill_pct_by_saturation,
# ajouté pour cette raison) à la place d'une couleur fixe : fonctionne bien
# pour les couleurs vives (orange/bleu) mais PAS pour le rouge sombre (marge
# de saturation trop faible, cf. RISQUE CONNU plus bas) ni pour le blanc/crème
# du joueur (saturation nulle PAR DÉFINITION -- indiscernable du fond
# désaturé). Aucune des deux approches ne couvre les 4 slots de façon fiable
# à ce stade -- cf. la recommandation OCR plus bas (VIGOUR_HP_ICON_OFFSET_FRAC),
# qui reste la vraie piste plutôt que continuer à ajuster des seuils au cas
# par cas.
# EN: CRITICAL DISCOVERY (2026-07-15, 2nd video analyzed: fellowship
# EN: 2026-07-15 22-45-35.mp4, same resolution, DIFFERENT group): these
# EN: colors are NOT fixed per slot. Video 1 (Buxtonox/SoW/Merim/Commah):
# EN: slot1 orange, slot2 blue, slot3 red, slot4 white. Video 2 (Elayra/
# EN: Kaschy/Roxo/Commah): slot1 tan, slot2 orange, slot3 blue, slot4 white --
# EN: orange and blue swapped SLOTS between sessions. Only slot4 (the player
# EN: himself) stays cream-white in both videos -- likely a stable
# EN: convention ("my own frame"), not a coincidence.
# EN:
# EN: Direct consequence: the values below are ONLY reliable for a session
# EN: with exactly THIS group composition -- a new composition changes the
# EN: colors and silently breaks these readings (no error raised, just a
# EN: wrong %). Tried a generic SATURATION-based detector (cf.
# EN: wardenbot/bar_reader.py::read_fill_pct_by_saturation, added for this
# EN: reason) instead of a fixed color: works well for vivid colors (orange/
# EN: blue) but NOT for dark red (saturation margin too thin, cf. KNOWN RISK
# EN: below) nor for the player's own white/cream (zero saturation BY
# EN: DEFINITION -- indistinguishable from the desaturated background).
# EN: Neither approach reliably covers all 4 slots at this stage -- cf. the
# EN: OCR recommendation below (VIGOUR_HP_ICON_OFFSET_FRAC), which remains the
# EN: real path forward rather than continuing to tune thresholds case by
# EN: case.
VIGOUR_HP_BAR_FILL_BGR: "dict[int, tuple[int, int, int]]" = {
    1: (41, 77, 158),    # orange/or
    2: (174, 44, 22),    # bleu
    3: (25, 17, 67),     # rouge sombre -- cf. RISQUE CONNU ci-dessus
    4: (153, 172, 171),  # blanc-crème (frame du joueur lui-même)
}

# BUG TROUVÉ EN TESTANT CE MODULE CONTRE DE VRAIS PIXELS (2026-07-15, pas en
# jeu -- avant tout envoi) : un premier passage lisait 0.0% pour les 4 barres
# alors que la vidéo montrait 85/100/93/100%. Root cause : le portrait/icône
# de chaque membre est dessiné PAR-DESSUS le début (~25% gauche) de sa propre
# barre de vie -- read_fill_pct (direction="ltr") scanne depuis la gauche et
# rencontre immédiatement les couleurs de l'icône, pas celles de la barre,
# et abandonne dès que max_gap est dépassé (bien avant la vraie fin du
# remplissage). Confirmé par calcul sur 3 valeurs connues de la vidéo
# (party_1 à 85%/47%/45% -- cf. rapport d'analyse) : la frontière colorée
# réelle se déplace de façon cohérente avec le %, mais SEULEMENT dans la
# portion visible après l'icône.
#
# ICON_OFFSET_FRAC = fraction de la largeur totale couverte par l'icône,
# mesurée sur party_1 (~24-27% selon les 3 points de calibration) -- PAS
# revérifiée indépendamment sur party_2/3/4 (même gabarit d'UI supposé,
# jamais confirmé). CONSÉQUENCE CONNUE, PAS CACHÉE : toute vraie valeur de vie
# EN DESSOUS de ~25% est indiscernable (la frontière de remplissage recule
# derrière l'icône, invisible) -- la lecture plafonne à ICON_OFFSET_FRAC au
# lieu de continuer à baisser. C'est exactement la plage la plus critique
# pour Vigour (burst_heal se déclenche sous 30%, cf. hooks.py::vigour_pre_tick)
# -- limitation réelle à corriger via OCR du pourcentage affiché (même
# principe que wardenbot/ocr.py::read_countdown_seconds pour Meiko) plutôt
# que de prétendre que cette lecture par couleur suffit dans cette zone.
# EN: BUG FOUND WHILE TESTING THIS MODULE AGAINST REAL PIXELS (2026-07-15,
# EN: not in-game -- before shipping). A first pass read 0.0% for all 4 bars
# EN: while the video showed 85/100/93/100%. Root cause: each member's
# EN: portrait/icon is drawn OVER the start (~left 25%) of their own health
# EN: bar -- read_fill_pct (direction="ltr") scans from the left and
# EN: immediately hits the icon's colors, not the bar's, and gives up once
# EN: max_gap is exceeded (well before the real end of the fill). Confirmed
# EN: by computing against 3 known values from the video (party_1 at
# EN: 85%/47%/45% -- cf. analysis report): the real color boundary moves
# EN: consistently with %, but ONLY in the portion visible past the icon.
# EN:
# EN: ICON_OFFSET_FRAC = fraction of the total width covered by the icon,
# EN: measured on party_1 (~24-27% across the 3 calibration points) -- NOT
# EN: independently re-verified on party_2/3/4 (same UI template assumed,
# EN: never confirmed). KNOWN CONSEQUENCE, NOT HIDDEN: any true HP value
# EN: BELOW ~25% is indistinguishable (the fill boundary retreats behind the
# EN: icon, invisible) -- the reading floors at ICON_OFFSET_FRAC instead of
# EN: continuing to drop. This is exactly Vigour's most critical range
# EN: (burst_heal fires under 30%, cf. hooks.py::vigour_pre_tick) -- a real
# EN: limitation to fix via OCR of the displayed percentage (same principle
# EN: as wardenbot/ocr.py::read_countdown_seconds for Meiko) rather than
# EN: pretending this color read is sufficient in that zone.
# MIS À JOUR (2026-07-15, 2e itération -- read_fill_pct_colorless, cf.
# bar_reader.py) : le paragraphe ci-dessus décrivait les limites de
# l'ANCIENNE approche (VIGOUR_HP_BAR_FILL_BGR, une couleur fixe par slot) --
# obsolète depuis que _read_party_hp_pct utilise read_fill_pct_colorless
# (saturation OU luminance OU dominance rouge). État réel, testé sur les 2
# vidéos (12 lectures, 4 slots x 3 frames par vidéo) après ce changement :
# - party_1 : fiable, ±6pt.
# - party_2 : fiable dans la majorité des cas testés, quelques lectures
#   bloquées (~76-77%) sur certaines frames de la 1re vidéo -- pas
#   entièrement résolu, mais nettement moins cassé qu'avant (n'était JAMAIS
#   correct auparavant).
# - party_3 (rouge sombre) : DÉBLOQUÉ par la condition R-G>20 (cf.
#   bar_reader.py) -- passé de "bloqué en permanence au plancher icône" à
#   des lectures correctes à quelques points près (93/100/75/66% mesurés).
# - party_4 (blanc, le joueur) : fiable sur les cas à vie haute ; sur un
#   joueur réellement MORT (0%), lit le plancher icône (~25%) -- PAS un
#   faux positif "plein de vie" comme le donnait un scan droite->gauche
#   naïf testé en parallèle (95%+ mesuré sur un mort, cf. bar_reader.py).
#
# Reste un angle mort partiel sur party_2 dans certains cas -- pas un blocage
# total comme avant, mais pas encore une garantie à 100%. Si un signal de
# correction périodique s'avère nécessaire pour ce cas résiduel, le pattern
# à suivre est un OCR CIBLÉ ET ASYNCHRONE déclenché seulement quand la
# lecture pixel est suspecte (ex: bloquée pile au plancher ICON_OFFSET_FRAC,
# ou saut brutal d'un tick à l'autre) -- pas un OCR systématique sur les 4
# slots à chaque tick : un flag dans CombatState (ex.
# state.hooks["party_N_needs_ocr_check"]) posé par le scan rapide, consommé
# par un thread OCR dédié qui ne réévalue QUE le(s) slot(s) suspect(s), à
# basse fréquence -- même philosophie que les threads de détection Meiko/
# Tariq (rapide et continu) + OCR (rare et ciblé, jamais sur le chemin
# critique de décision). Pas implémenté à ce stade -- le gain net face au
# taux de réussite actuel n'a pas encore justifié cette complexité
# supplémentaire.
# EN: UPDATED (2026-07-15, 2nd iteration -- read_fill_pct_colorless, cf.
# EN: bar_reader.py): the paragraph above described the limits of the OLD
# EN: approach (VIGOUR_HP_BAR_FILL_BGR, one fixed color per slot) --
# EN: obsolete since _read_party_hp_pct uses read_fill_pct_colorless
# EN: (saturation OR luminance OR red dominance). Real state, tested on both
# EN: videos (12 readings, 4 slots x 3 frames per video) after this change:
# EN: - party_1: reliable, within ±6pt.
# EN: - party_2: reliable in most tested cases, a few readings stuck
# EN:   (~76-77%) on some frames of the 1st video -- not fully resolved, but
# EN:   markedly less broken than before (was NEVER correct before).
# EN: - party_3 (dark red): UNBLOCKED by the R-G>20 condition (cf.
# EN:   bar_reader.py) -- went from "permanently stuck at the icon floor" to
# EN:   readings correct within a few points (93/100/75/66% measured).
# EN: - party_4 (white, the player): reliable at high HP; on a genuinely DEAD
# EN:   player (0%), reads the icon floor (~25%) -- NOT a "full health" false
# EN:   positive like a naive right-to-left scan gave in parallel testing
# EN:   (95%+ measured on a dead player, cf. bar_reader.py).
# EN:
# EN: A partial blind spot remains on party_2 in some cases -- not a total
# EN: block like before, but not yet a 100% guarantee either. If a periodic
# EN: correction signal turns out to be needed for this residual case, the
# EN: pattern to follow is a TARGETED, ASYNCHRONOUS OCR triggered only when
# EN: the pixel reading looks suspect (e.g. stuck exactly at the
# EN: ICON_OFFSET_FRAC floor, or a sudden jump between ticks) -- not a
# EN: systematic OCR on all 4 slots every tick: a flag in CombatState (e.g.
# EN: state.hooks["party_N_needs_ocr_check"]) set by the fast scan, consumed
# EN: by a dedicated OCR thread that only re-evaluates the suspect slot(s),
# EN: at low frequency -- same philosophy as the Meiko/Tariq detection
# EN: threads (fast and continuous) + OCR (rare and targeted, never on the
# EN: critical decision path). Not implemented at this stage -- the net gain
# EN: over the current success rate hasn't justified this extra complexity
# EN: yet.
VIGOUR_HP_ICON_OFFSET_FRAC = 0.25


def _read_party_hp_pct(frame) -> float:
    """Lit le % de vie réel en corrigeant l'icône qui recouvre le début de
    la barre (cf. VIGOUR_HP_ICON_OFFSET_FRAC ci-dessus) : ignore cette
    portion pour le scan, puis reprojette le résultat sur l'échelle complète
    -- PAS un recadrage de la capture (une seule région calibrée par slot,
    cf. profiles/*/config.json), juste un slice numpy sur le frame déjà
    capturé.

    read_fill_pct_colorless (2026-07-15, 2e itération -- cf. bar_reader.py
    pour le détail complet) plutôt qu'une couleur fixe par slot (1re
    itération, VIGOUR_HP_BAR_FILL_BGR, ci-dessus) : les couleurs de classe ne
    sont PAS stables d'une session à l'autre (vérifié sur 2 vidéos réelles),
    donc une couleur codée en dur casse silencieusement dès que la
    composition du groupe change. VIGOUR_HP_BAR_FILL_BGR reste dans ce
    fichier pour référence/diagnostic (comparer une lecture couleur-fixe à
    une lecture générique peut aider à débugger un cas litigieux), mais
    n'est plus utilisé pour la décision.

    EN: Reads the real HP% correcting for the icon covering the bar's start
    EN: (cf. VIGOUR_HP_ICON_OFFSET_FRAC above): skips that portion for the
    EN: scan, then reprojects the result onto the full scale -- NOT a recrop
    EN: of the capture (one calibrated region per slot, cf.
    EN: profiles/*/config.json), just a numpy slice on the already-captured
    EN: frame.

    EN: read_fill_pct_colorless (2026-07-15, 2nd iteration -- cf.
    EN: bar_reader.py for the full detail) rather than a fixed color per slot
    EN: (1st iteration, VIGOUR_HP_BAR_FILL_BGR, above): class colors are NOT
    EN: stable across sessions (verified on 2 real videos), so a hardcoded
    EN: color silently breaks the moment the group composition changes.
    EN: VIGOUR_HP_BAR_FILL_BGR stays in this file for reference/diagnostics
    EN: (comparing a fixed-color read against the generic one can help debug
    EN: a disputed case), but is no longer used for the decision.
    """
    icon_px = int(frame.shape[1] * VIGOUR_HP_ICON_OFFSET_FRAC)
    narrow_frac = read_fill_pct_colorless(
        frame[:, icon_px:], saturation_threshold=50, luminance_threshold=90, direction="ltr", max_gap=4
    )
    return VIGOUR_HP_ICON_OFFSET_FRAC + (1 - VIGOUR_HP_ICON_OFFSET_FRAC) * narrow_frac

# MESURÉ (même vidéo, cf. templates/vigour/orb_bar.png) -- 6 pips discrets
# (PAS une barre à remplissage continu comme HP/mana), qui s'allument un par
# un de gauche à droite. Centres en x trouvés par profil de luminosité
# colonne par colonne (pics nets, espacement régulier ~28px) -- PAS une
# extrapolation, mesurés directement sur les 6 positions. y=900-932 englobe
# à la fois le halo d'un pip allumé (pic mesuré à y=908) et l'icône plus
# sombre d'un pip éteint (pic mesuré vers y=919-930) -- fenêtre large
# délibérément pour couvrir les deux cas plutôt qu'une bande fine qui
# fonctionnerait pour l'un et raterait l'autre.
# EN: MEASURED (same video, cf. templates/vigour/orb_bar.png) -- 6 discrete
# EN: pips (NOT a continuous fill bar like HP/mana), lit one by one left to
# EN: right. X centers found via column-by-column brightness profiling (clean
# EN: peaks, regular ~28px spacing) -- NOT an extrapolation, measured
# EN: directly at all 6 positions. y=900-932 covers both a lit pip's glow
# EN: (peak measured at y=908) and an unlit pip's darker icon (peak measured
# EN: around y=919-930) -- deliberately wide window to cover both cases
# EN: rather than a thin band that would work for one and miss the other.
VIGOUR_ORB_PIP_X_CENTERS = (10, 40, 68, 96, 123, 151)   # relatifs au coin haut-gauche de la région orb_bar calibrée (absolus mesurés : 436,466,494,522,549,577, region.left=426) · EN: relative to the calibrated orb_bar region's top-left corner (measured absolutes: 436,466,494,522,549,577, region.left=426)
VIGOUR_ORB_PIP_HALF_WIDTH = 10
VIGOUR_ORB_PIP_HEIGHT = 32   # top vient de la région calibrée elle-même (base_top) -- pas de 2e constante à tenir synchronisée · EN: top comes from the calibrated region itself (base_top) -- no 2nd constant to keep in sync
# MOYENNE de luminosité sur toute la fenêtre du pip, PAS le maximum -- testé
# en jeu réel (frame vidéo) : un pip ÉTEINT peut contenir un unique pixel
# parasite (reflet/artefact de compression) dépassant 150 en luminosité
# max alors que sa MOYENNE reste ~44 -- un seuil sur le max seul donnait un
# faux positif. Pip allumé mesuré : moyenne ~84 ; pips éteints mesurés :
# moyenne ~43-45 -- marge large, un seuil à 60 sépare proprement les deux.
# EN: AVERAGE brightness over the whole pip window, NOT the maximum -- tested
# EN: on a real video frame: an UNLIT pip can contain a single stray pixel
# EN: (reflection/compression artifact) exceeding 150 in max brightness while
# EN: its AVERAGE stays ~44 -- a max-only threshold gave a false positive.
# EN: Lit pip measured: average ~84; unlit pips measured: average ~43-45 --
# EN: wide margin, a threshold of 60 cleanly separates the two.
VIGOUR_ORB_LIT_BRIGHTNESS_THRESHOLD = 60

# MESURÉ depuis templates/vigour/mana_bar.png (dégradé cyan/blanc, glossy --
# valeur prise près du bord, pas au centre du reflet le plus clair). Couleur
# fiable ; PAS sa POSITION à l'écran -- non localisée avec confiance dans la
# vidéo fournie (zone testée autour des pips d'orbes : rien trouvé à cette
# frame précise, probablement une jauge proche de vide à ce moment précis).
# region absente tant que non calibrée (cf. capture_helper.py mark) --
# state_update_fn saute la lecture plutôt que de deviner une position.
# EN: MEASURED from templates/vigour/mana_bar.png (cyan/white glossy
# EN: gradient -- value taken near the edge, not the brightest highlight).
# EN: Color is reliable; its ON-SCREEN POSITION is NOT -- not confidently
# EN: located in the supplied video (area tested around the orb pips: nothing
# EN: found at that specific frame, likely a near-empty gauge at that
# EN: moment). Region absent until calibrated (cf. capture_helper.py mark) --
# EN: state_update_fn skips the read rather than guessing a position.
VIGOUR_MANA_BAR_FILL_BGR = (253, 190, 0)


def count_lit_orb_pips(capturer: ScreenCapture, base_left: int, base_top: int) -> int:
    """Compte les pips allumés parmi les 6 (cf. VIGOUR_ORB_PIP_X_CENTERS) --
    PAS un read_fill_pct (ceux-ci sont discrets, pas un dégradé continu).
    base_left/base_top : coin haut-gauche de la région orb_bar calibrée (cf.
    profiles/*/config.json) -- les centres de pips mesurés ci-dessus sont
    relatifs au cadrage de capture original, recalés ici par différence
    plutôt que codés en dur sur des coordonnées absolues, pour rester valable
    si la région calibrée bouge légèrement.

    EN: Counts lit pips among the 6 (cf. VIGOUR_ORB_PIP_X_CENTERS) -- NOT a
    EN: read_fill_pct (these are discrete, not a continuous gradient).
    EN: base_left/base_top: top-left corner of the calibrated orb_bar region
    EN: (cf. profiles/*/config.json) -- the pip centers measured above are
    EN: relative to the original capture framing, re-anchored here by
    EN: difference rather than hardcoded on absolute coordinates, to stay
    EN: valid if the calibrated region shifts slightly.
    """
    count = 0
    for x_center in VIGOUR_ORB_PIP_X_CENTERS:
        region = {
            "left": base_left + (x_center - VIGOUR_ORB_PIP_HALF_WIDTH),
            "top": base_top,
            "width": VIGOUR_ORB_PIP_HALF_WIDTH * 2,
            "height": VIGOUR_ORB_PIP_HEIGHT,
        }
        frame = capturer.grab(region)
        if frame.mean() >= VIGOUR_ORB_LIT_BRIGHTNESS_THRESHOLD:
            count += 1
        else:
            # Les pips s'allument dans l'ordre -- un pip éteint signifie que
            # tous les suivants le sont aussi (cf. vidéo analysée : jamais vu
            # un pip N allumé avec N-1 éteint). Arrêt anticipé, pas un
            # balayage complet des 6 à chaque tick.
            # EN: Pips light up in order -- an unlit pip means every following
            # EN: one is unlit too (cf. analyzed video: never seen pip N lit
            # EN: with N-1 unlit). Early exit, not a full sweep of all 6 every
            # EN: tick.
            break
    return count


def make_vigour_state_update_fn(capturer: ScreenCapture, matcher: TemplateMatcher, regions: dict):
    """
    Lit les 4 emplacements du cadre de groupe et remplit state.party (cf.
    CombatState.party, engine.py) -- HP% via barre de remplissage PAR SLOT
    (cf. VIGOUR_HP_BAR_FILL_BGR), cleanse_needed via template d'icône de
    debuff. hot_active N'EST PAS encore lu (Phase 1 du plan d'intégration --
    cf. discussion architecture) : reste à False tant que le template du HoT
    n'a pas été capturé et cette fonction mise à jour en conséquence.

    Ressources personnelles (mana, orbes) écrites dans state.resources --
    même dict générique que stormfall/earthfall (Meiko) et fury (Tariq), pas
    un champ dédié : ce sont des ressources DU JOUEUR, pas du groupe,
    exactement la même nature d'information que ce que ce dict contient déjà
    ailleurs dans ce fichier.

    Région absente (regions.get(...) is None) -> lecture sautée (slot marqué
    absent, ou ressource laissée à sa valeur par défaut), même convention que
    rage_bar/hammer_storm_cast_bar (Tariq, plus haut) : un profil non calibré
    ne doit pas faire planter la lecture, seulement produire "rien à voir
    ici". C'est le cas aujourd'hui de mana_bar (position non localisée avec
    confiance, cf. VIGOUR_MANA_BAR_FILL_BGR) -- party_*_debuff sont bien
    présentes dans le profil (cf. docstring plus bas pour leur statut réel :
    présentes et recalibrées, mais pas encore garanties viser le bon type
    d'icône).

    party_N_debuff : RECALIBRÉ le 2026-07-16 (2e vidéo analysée). La 1re
    calibration (offset 91px depuis le bord gauche du cadre, mesurée UNE
    SEULE FOIS sur party_1 dans la 1re vidéo, puis extrapolée par corrélation
    aux 3 autres) était imprécise. Cette fois, DEUX mesures indépendantes
    (Elayra/party_1 ET Kaschy/party_2, icônes différentes mais même
    emplacement) donnent un offset cohérent de 109-110px -- confirme que
    l'hypothèse de corrélation (même espacement pour tous les slots) est
    juste, mais que la mesure de départ ne l'était pas. Régions recalées sur
    cette moyenne (cf. profiles/Default/config.json).

    CE QUI RESTE NON RÉSOLU, PAS CACHÉ : rien ne confirme que l'icône
    observée à cet emplacement soit spécifiquement LE debuff dispellable --
    plusieurs icônes différentes (proc personnel, buff, cooldown ?) ont été
    vues au même endroit selon les instants dans les 2 vidéos, sans moyen de
    les distinguer depuis les pixels seuls. cleanse_needed peut donc
    déclencher sur autre chose qu'un vrai debuff à nettoyer tant que ce
    point n'est pas tranché en jeu (capture_helper.py mark pendant que TOI
    tu confirmes visuellement "c'est bien ça, le debuff cleansable").

    EN: Reads the 4 party-frame slots and fills state.party (cf.
    EN: CombatState.party, engine.py) -- HP% via fill bar PER SLOT (cf.
    EN: VIGOUR_HP_BAR_FILL_BGR), cleanse_needed via a debuff-icon template.
    EN: hot_active is NOT read yet (Phase 1 of the integration plan -- cf.
    EN: architecture discussion): stays False until the HoT template is
    EN: captured and this function updated accordingly.

    EN: Personal resources (mana, orbs) written into state.resources -- the
    EN: same generic dict as stormfall/earthfall (Meiko) and fury (Tariq),
    EN: not a dedicated field: these are the PLAYER's resources, not the
    EN: group's, exactly the same kind of information this dict already holds
    EN: elsewhere in this file.

    EN: Missing region (regions.get(...) is None) -> read skipped (slot
    EN: marked absent, or resource left at its default), same convention as
    EN: rage_bar/hammer_storm_cast_bar (Tariq, above): an uncalibrated
    EN: profile shouldn't crash the read, only produce "nothing to see here".
    EN: That's the case today for mana_bar (position not confidently located,
    EN: cf. VIGOUR_MANA_BAR_FILL_BGR) -- party_*_debuff ARE present in the
    EN: profile (cf. docstring below for their real status: present and
    EN: recalibrated, but not yet guaranteed to target the right icon type).

    EN: party_N_debuff: RECALIBRATED on 2026-07-16 (2nd video analyzed). The
    EN: 1st calibration (offset 91px from the frame's left edge, measured
    EN: ONCE on party_1 in the 1st video, then extrapolated by correlation to
    EN: the other 3) was imprecise. This time, TWO independent measurements
    EN: (Elayra/party_1 AND Kaschy/party_2, different icons but same
    EN: location) give a consistent offset of 109-110px -- confirms the
    EN: correlation assumption (same spacing for every slot) is correct, but
    EN: the original measurement wasn't. Regions recalibrated on this average
    EN: (cf. profiles/Default/config.json).

    EN: WHAT REMAINS UNRESOLVED, NOT HIDDEN: nothing confirms the icon
    EN: observed at this location is specifically THE dispellable debuff --
    EN: several different icons (personal proc, buff, cooldown?) were seen at
    EN: the same spot at different moments across both videos, with no way
    EN: to tell them apart from pixels alone. cleanse_needed could therefore
    EN: trigger on something other than a real debuff to cleanse until this
    EN: is settled in-game (capture_helper.py mark while YOU visually confirm
    EN: "yes, that's the cleansable debuff").
    """

    def state_update_fn(state: CombatState) -> None:
        # party_cleanse_debuff.png n'a jamais été capturé (2026-07-16, 1er test
        # en jeu : FileNotFoundError levée par matcher.is_match, cf. error.log).
        # TemplateMatcher._load ne met rien en cache sur un échec de lecture,
        # donc l'exception se reproduirait à CHAQUE tick -- et comme ce bloc
        # tourne AVANT state.party = party / detection_meta plus bas, elle
        # avortait toute la fonction : party, mana et orbes ne se mettaient
        # plus jamais à jour, pas seulement cleanse_needed. Même convention que
        # les régions non calibrées (hp_region/debuff_region/mana_region plus
        # bas) : un template pas encore capturé -> "rien à voir ici", pas un
        # crash. Vérifié une seule fois par tick (pas par slot, le nom du
        # template est le même pour les 4).
        #
        # EN: party_cleanse_debuff.png was never captured (2026-07-16, first
        # EN: in-game test: FileNotFoundError raised by matcher.is_match, cf.
        # EN: error.log). TemplateMatcher._load caches nothing on a failed
        # EN: read, so the exception would repeat on EVERY tick -- and since
        # EN: this block ran BEFORE state.party = party / detection_meta below,
        # EN: it aborted the whole function: party, mana and orbs stopped
        # EN: updating entirely, not just cleanse_needed. Same convention as
        # EN: uncalibrated regions (hp_region/debuff_region/mana_region below):
        # EN: a not-yet-captured template -> "nothing to see here", not a
        # EN: crash. Checked once per tick (not per slot, the template name is
        # EN: the same for all 4).
        debuff_template_available = (matcher.template_dir / "party_cleanse_debuff.png").exists()

        party: dict = {}
        for i in PARTY_SLOTS:
            hp_region = regions.get(f"party_{i}_hp")
            if hp_region is None:
                party[i] = {"present": False, "hp_pct": None, "cleanse_needed": False, "hot_active": False}
                continue

            frame = capturer.grab(hp_region)
            hp_pct = _read_party_hp_pct(frame)

            cleanse_needed = False
            debuff_region = regions.get(f"party_{i}_debuff")
            if debuff_region is not None and debuff_template_available:
                debuff_frame = capturer.grab(debuff_region)
                cleanse_needed = matcher.is_match(debuff_frame, "party_cleanse_debuff")

            party[i] = {"present": True, "hp_pct": hp_pct, "cleanse_needed": cleanse_needed, "hot_active": False}

        state.party = party
        state.detection_meta["vigour_party_bars"] = DetectionMeta(created_at=time.monotonic())

        mana_region = regions.get("mana_bar")
        if mana_region is not None:
            mana_frame = capturer.grab(mana_region)
            state.resources["mana"] = read_fill_pct(
                mana_frame, VIGOUR_MANA_BAR_FILL_BGR, tolerance=50, direction="ltr", max_gap=4
            )

        orb_region = regions.get("orb_bar")
        if orb_region is not None:
            state.resources["orbs"] = count_lit_orb_pips(capturer, orb_region["left"], orb_region["top"])

    return state_update_fn
