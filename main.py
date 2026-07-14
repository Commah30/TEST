"""
main.py — point d'entrée. Assemble capture d'écran, template matching,
moteur de rotation, hooks par personnage et simulation de touches.

Deux personnages supportés (cf. wardenbot/game_config.py::CHARACTERS) :
"meiko" et "tariq". Au lancement, l'exe/le script demande lequel piloter --
chacun a son propre dossier de templates (templates/<perso>/), son propre
YAML (config/<perso>.yaml) et son propre KEY_MAP/REGIONS.

Le reste du projet est réparti par responsabilité (cf. README.md) :
  wardenbot/game_config.py   KEY_MAP, REGIONS, CHARACTERS (donnée + wiring)
  wardenbot/state_update.py  lecture d'écran -> state (détection, par perso)
  wardenbot/diagnostics.py   watchdog (appels lents) + log d'erreurs
  wardenbot/hooks.py         logique de combo/rotation par personnage
Ce fichier ne fait plus que l'assemblage (build_engine), la boucle de debug
(logger/hotkeys) et le point d'entrée console (__main__) -- avant refactor
(2026-07-12) il faisait 750+ lignes en mélangeant tout ça avec KEY_MAP/
REGIONS et toute la logique de détection Tariq.

SETUP AVANT DE LANCER EN VRAI (dry_run=False) :
  1. pip install -r requirements.txt
  2. Remplis KEY_MAP[<perso>] (wardenbot/game_config.py) avec tes vrais binds.
  3. Capture les templates PNG (une icône par ability + les debuffs/badges
     nécessaires) dans templates/<perso>/, nommés exactement comme dans
     KEY_MAP[<perso>].
  4. Remplis REGIONS[<perso>] (wardenbot/game_config.py) avec les
     coordonnées (left, top, width, height) de chaque icône à l'écran (clic
     droit sur ta capture d'écran / un outil comme ShareX pour lire les
     coordonnées, ou `python capture_helper.py mark`).
  5. Lance d'abord avec dry_run=True pour vérifier dans la console que les
     bonnes abilities sortent au bon moment, AVANT de laisser le bot presser
     de vraies touches en jeu.
"""
from __future__ import annotations

# PHASE 3 industrialisation (2026-07-14) : déclare le process DPI-aware AVANT
# TOUT AUTRE import/appel qui pourrait toucher à l'écran ou la souris (mss,
# ctypes.windll.user32.GetCursorPos/GetForegroundWindow/GetAsyncKeyState...)
# -- cf. wardenbot/dpi_awareness.py pour la cascade Per-Monitor V2 -> V1 ->
# System Aware. Doit rester la toute première chose que fait ce module,
# avant même le fix d'encodage UTF-8 juste en dessous : sans ça, Windows
# recalcule (ment sur) les coordonnées écran dès que le scaling n'est pas à
# 100%, et tout ce qui suit (capture, souris, presses) serait décalé.
from wardenbot.dpi_awareness import enable_dpi_awareness

DPI_AWARENESS_LEVEL = enable_dpi_awareness()

import ctypes
import sys
import time
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

from wardenbot.capture import ScreenCapture
from wardenbot.config_manager import ConfigManager, list_profiles, validate_profile
from wardenbot.diagnostics import check_foreground_window, log_error
from wardenbot.engine import CombatState, RotationEngine, load_abilities, load_weight_overrides, parse_sequences
from wardenbot.game_config import CHARACTERS
from wardenbot.input_sim import InputSimulator, InputWorker
from wardenbot.matcher import TemplateMatcher
from wardenbot.state_update import TARIQ_BUFF_BAR_THRESHOLDS  # noqa: F401 -- re-exporté, cf. build_yolo_dataset.py
from wardenbot.telemetry import TelemetryWorker

DEBUG_LOG_PATH = Path("debug.log")

# PHASE 1 industrialisation (2026-07-14) : REGIONS/KEY_MAP ne sont plus des
# dicts codés en dur dans wardenbot/game_config.py -- ils viennent maintenant
# de config/default.json via ConfigManager. Reconstruits ici, au chargement
# du module, UNIQUEMENT pour la rétrocompatibilité de 4 scripts de dev qui
# font `from main import REGIONS` (build_yolo_dataset.py, detect_check.py,
# tick_timing_test.py, yolo_check.py) -- aucun changement requis dessus.
# build_engine() ci-dessous n'utilise PAS ces globals : il crée son propre
# ConfigManager et appelle get_region()/get_key() directement.
_CONFIG_MANAGERS = {name: ConfigManager(name) for name in CHARACTERS}
REGIONS = {name: cm.regions for name, cm in _CONFIG_MANAGERS.items()}
KEY_MAP = {name: cm.keybinds for name, cm in _CONFIG_MANAGERS.items()}


def make_debug_logger(state: CombatState) -> Callable[[Optional[str]], None]:
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
            # Tout state.detection/hooks/planner plutôt qu'un champ Meiko codé
            # en dur (warden_stacks) -- generique pour marcher pareil avec les
            # clés de proc de Tariq (hammer_storm_proc_ready, etc.). 3 clés
            # distinctes (2026-07-14, migration CombatState) au lieu d'un seul
            # "flags" fourre-tout -- même contenu, juste rangé par propriétaire
            # (cf. wardenbot/engine.py::CombatState).
            "detection": dict(state.detection),
            "hooks": dict(state.hooks),
            "planner": dict(state.planner),
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
        # None -- cf. CombatState.debug_skip_trace (engine.py), rempli par
        # _pick_ability : pourquoi CHAQUE ability a été écartée ce tick.
        skip_trace = state.debug_skip_trace if pressed is None else []
        # choice_reason (2026-07-14, audit rotation/stabilité) : symétrique de
        # skip_trace -- répond à "pourquoi X A gagné" quand pressed n'est PAS
        # None, là où skip_trace répondait déjà à "pourquoi pas X" quand rien
        # n'est pressé. cf. CombatState.debug_choice_reason (engine.py), rempli par
        # _pick_ability (ou le pre_tick_hook quand il court-circuite le scan).
        choice_reason = state.debug_choice_reason if pressed is not None else ""
        line = (
            f"{time.strftime('%H:%M:%S')} "
            f"pressed={pressed!r} last_cast={state.last_cast!r} "
            f"buffs={snapshot['buffs']} debuffs={snapshot['debuffs']} "
            f"resources={snapshot['resources']} detection={snapshot['detection']} "
            f"hooks={snapshot['hooks']} planner={snapshot['planner']} "
            f"locks={snapshot['locks']} cooldowns_remaining_s={snapshot['cooldowns_remaining_s']} "
            f"skip_trace={skip_trace} choice_reason={choice_reason!r}\n"
        )
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)

    return log_tick


VK_F9 = 0x78


def make_debug_reset_hotkey(state: CombatState) -> Callable[[], None]:
    """
    F9 en jeu : remet state.hooks["warden_stacks"] à 0 à la main. Sert de
    secours si le compteur de stacks Warden of the Temple (cf. hooks.py) se
    désynchronise de l'état réel -- typiquement si tu relances le bot en
    plein combat alors que des stacks étaient déjà posées avant.
    """
    pressed_last = False

    def check_reset_hotkey() -> None:
        nonlocal pressed_last
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_F9) & 0x8000)
        if down and not pressed_last:
            state.hooks["warden_stacks"] = 0
            state.hooks.pop("warden_stacks_since", None)
            print("[main] F9 -- warden_stacks remis à 0 manuellement")
        pressed_last = down

    return check_reset_hotkey


VK_C = 0x43


def make_pause_toggle(on_toggle: Optional[Callable[[bool], None]] = None) -> tuple[Callable[[], bool], Callable[[], None]]:
    """
    C en jeu : active/désactive la rotation sans relancer le script. En
    pause, RotationEngine.run_forever() saute complètement tick() (cf.
    engine.py) -- aucune touche pressée, aucun état modifié, donc rien ne
    dérive pendant que c'est arrêté.
    Retourne (is_paused, check_toggle_hotkey) : is_paused se passe à
    run_forever(is_paused=...), check_toggle_hotkey se branche sur on_tick
    (il doit tourner MÊME en pause pour pouvoir détecter la reprise).

    on_toggle(is_now_paused) : AJOUTÉ (2026-07-13) -- avant ça, la pause
    n'était imprimée qu'en console, jamais journalisée. Un long silence dans
    debug.log (fury/skip_trace figés, GCD qui part largement négatif -- cf.
    session du 2026-07-13, ~32s et ~4m52 de "gel" qui ressemblaient à un bug
    de détection) était en fait indissociable d'une vraie pause volontaire
    sans preuve écrite après coup -- fallait redemander à l'utilisateur. on_toggle
    permet à main.py d'écrire un marqueur DANS debug.log au moment exact du
    toggle, visible directement à côté des ticks concernés (pas un fichier
    séparé à recorréler à la main).
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
            if on_toggle is not None:
                on_toggle(toggle_state["paused"])
        pressed_last = down

    return is_paused, check_toggle_hotkey


def build_engine(
    character: str, dry_run: bool = True, profile: Optional[str] = None, enable_telemetry: bool = False
) -> RotationEngine:
    cfg = CHARACTERS[character]
    # PHASE 1 industrialisation (2026-07-14) : le moteur ne connaît plus les
    # coordonnées -- il passe par ConfigManager.
    # `.regions` reste un dict brut passé tel quel à state_update.py (qui
    # fait plusieurs .get()/itérations dessus, cf. wardenbot/state_update.py)
    # -- get_region()/get_key() servent aux lookups individuels ci-dessous.
    # PHASE 2 (2026-07-14) : profile=None -> profil "Default" (cf.
    # ConfigManager), comportement identique à avant l'introduction des
    # profils -- rien ne change pour un appelant qui ne précise pas de profil.
    config = ConfigManager(character, profile=profile)
    # Validation stricte UNIQUEMENT en dehors du dry_run (2026-07-14, suite à
    # la discussion) : en dry_run, aucune capture/presse réelle n'a lieu de
    # toute façon (is_ready_fn retourne True inconditionnellement plus bas) --
    # rester permissif garde le flux de dev existant (itérer sur la logique
    # sans écran ni profil calibré) intact. En conditions réelles
    # (dry_run=False), on préfère un échec explicite et immédiat à un bot qui
    # tourne "à l'aveugle" sur un profil non calibré.
    if not dry_run:
        validate_profile(config, required_regions=cfg.get("required_regions"))

    abilities, meta = load_abilities(cfg["config"])
    # Phase 3 (2026-07-14) : bloc `sequences:` optionnel, déjà exposé tel
    # quel dans `meta` par load_abilities (clé "sequences" du YAML, comme
    # n'importe quelle autre clé de haut niveau hors `abilities:`) -- absent
    # pour Tariq aujourd'hui, donc parse_sequences(None) -> [] et
    # RotationEngine se comporte à l'identique d'avant cette migration.
    # EN: Phase 3 (2026-07-14): optional `sequences:` block, already exposed
    # EN: as-is in `meta` by load_abilities (the YAML's "sequences" key, like
    # EN: any other top-level key outside `abilities:`) -- absent for Tariq
    # EN: today, so parse_sequences(None) -> [] and RotationEngine behaves
    # EN: identically to before this migration.
    sequences = parse_sequences(meta.get("sequences"))
    state = CombatState(mode=cfg["default_mode"])

    # Phase 4 (2026-07-14, télémétrie + tuning appris) : les DEUX sont
    # opt-in, jamais automatiques.
    #   - weight_overrides : lu à chaque démarrage depuis kb/combat_tuning.json
    #     (cf. wardenbot/kb_engine.py) -- absent tant que l'utilisateur n'a rien
    #     approuvé (cf. "human-in-the-loop") -> {} -> comportement identique
    #     à avant cette fonctionnalité, MÊME hors dry_run.
    #   - telemetry : enable_telemetry=False par défaut -- ne construit/
    #     démarre un TelemetryWorker (thread + fichier) QUE si explicitement
    #     demandé, jamais par RotationEngine lui-même (cf.
    #     wardenbot/telemetry.py, docstring module -- sinon chaque
    #     RotationEngine() de test ouvrirait un fichier).
    # EN: Phase 4 (2026-07-14, telemetry + learned tuning): BOTH are opt-in,
    # EN: never automatic.
    # EN:   - weight_overrides: read at every startup from
    # EN:     kb/combat_tuning.json (cf. wardenbot/kb_engine.py) -- absent as
    # EN:     long as the user hasn't approved anything (cf. "human-in-the-loop")
    # EN:     -> {} -> behavior identical to before this feature, EVEN outside
    # EN:     dry_run.
    # EN:   - telemetry: enable_telemetry=False by default -- only builds/
    # EN:     starts a TelemetryWorker (thread + file) IF explicitly requested,
    # EN:     never by RotationEngine itself (cf. wardenbot/telemetry.py,
    # EN:     module docstring -- otherwise every test's RotationEngine()
    # EN:     would open a file).
    weight_overrides = load_weight_overrides()
    telemetry = None
    if enable_telemetry:
        telemetry = TelemetryWorker()
        telemetry.start()

    matcher = TemplateMatcher(cfg["templates"])
    capturer = ScreenCapture() if not dry_run else None
    input_sim = InputSimulator(dry_run=dry_run)

    # Décision/input séparés (2026-07-13) : avant, press_fn() appelait
    # input_sim.press() directement et de façon BLOQUANTE -- keyDown/
    # sleep(30-90ms, plus pour un combo modificateur)/keyUp tournaient sur
    # le MÊME thread que RotationEngine.tick(), gelant la boucle de décision
    # (60Hz = budget 16.67ms/tick pour Tariq) pendant tout le hold. Même
    # goulot que celui déjà résolu côté détection (cf. state_update.py),
    # jamais traité côté input jusqu'ici. InputWorker fait tourner
    # l'exécution physique des touches sur son propre thread, avec une
    # queue -- press_fn() devient un simple enqueue quasi instantané.
    #
    # on_before_press=check_foreground_window déplacé ICI (dans le thread
    # d'input, juste avant la vraie presse) plutôt qu'au moment de la
    # décision -- plus précis : vérifie l'état du focus au moment où la
    # touche part VRAIMENT, pas au moment où le tick l'a décidée (la queue
    # peut, rarement, introduire un léger décalage entre les deux).
    def _check_focus_before_press(name: str) -> None:
        if not dry_run:
            check_foreground_window(name)

    input_worker = InputWorker(input_sim, on_before_press=_check_focus_before_press)

    def press_fn(name: str) -> None:
        key = config.get_key(name)
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
        input_worker.request_press(key, label=name, light=light)

    def is_ready_fn(name: str, confidence_threshold: Optional[float] = None) -> bool:
        if dry_run:
            return True  # en dry-run on considère tout "ready" pour tester la logique
        region = config.get_region(name)
        if region is None:
            return True  # pas de template pour cette entrée -> on ne bloque pas dessus
        frame = capturer.grab(region)
        threshold = confidence_threshold if confidence_threshold is not None else 0.70
        return matcher.is_match(frame, name, threshold)

    make_state_update_fn = cfg["make_state_update_fn"]
    state_update_fn = (
        make_state_update_fn(capturer, matcher, config.regions)
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
        sequences=sequences,
        telemetry=telemetry,
        weight_overrides=weight_overrides,
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


def choose_profile() -> Optional[str]:
    """PHASE 2 (2026-07-14) : demande en console quel profil d'affichage
    utiliser (résolution/DPI/échelle UI, cf. wardenbot/config_manager.py) --
    miroir de choose_character() ci-dessus. Retourne None si un seul profil
    existe (ou aucun) -- ConfigManager retombe alors sur "Default" tout
    seul, pas la peine de demander pour rien."""
    names = list_profiles()
    if len(names) <= 1:
        return None
    print("[main] Profils disponibles :")
    for i, name in enumerate(names, start=1):
        print(f"  {i}. {name}")
    while True:
        choice = input("Choisis un profil (numéro ou nom, Entrée = Default) : ").strip()
        if choice == "":
            return None
        if choice in names:
            return choice
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return names[int(choice) - 1]
        print(f"Choix invalide ({choice!r}) -- réessaie.")


def make_error_handler() -> Callable[[BaseException], None]:
    """
    ROBUSTESSE (2026-07-12) : avant ça, une exception dans self.tick() (ex.
    frame de capture invalide, glitch matcher/YOLO) n'était rattrapée nulle
    part et tuait tout le bot en pleine partie, sans aucune trace utile
    accessible pendant qu'on joue. Journalise dans error.log
    (wardenbot/diagnostics.py) et laisse la boucle continuer au tick
    suivant -- un incident probablement transitoire ne doit pas forcer à
    relancer le bot en plein combat.
    """
    def on_error(exc: BaseException) -> None:
        log_error("RotationEngine.tick()", exc)

    return on_error


def _log_pause_event(paused: bool) -> None:
    """Écrit un marqueur bien visible DANS debug.log au moment exact d'un
    toggle pause/reprise (cf. make_pause_toggle::on_toggle) -- pas dedup,
    toujours écrit, pour rester repérable même si rien d'autre ne change
    juste avant/après."""
    marker = "=== PAUSE ACTIVÉE (touche C) ===" if paused else "=== PAUSE DÉSACTIVÉE (touche C) -- reprise ==="
    line = f"{time.strftime('%H:%M:%S')} {marker}\n"
    with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


if __name__ == "__main__":
    print(f"[main] DPI awareness : {DPI_AWARENESS_LEVEL}")
    character = choose_character()
    profile = choose_profile()
    # enable_telemetry=True (2026-07-14, Phase 4) : journal de décisions
    # (telemetry.jsonl, cf. wardenbot/telemetry.py) actif par défaut en
    # conditions réelles -- c'est la matière première de
    # wardenbot/kb_engine.py, qui n'a de valeur qu'avec des sessions
    # réellement enregistrées. Mets à False ici si tu ne veux pas l'écriture
    # disque associée pour une session donnée.
    # EN: enable_telemetry=True (2026-07-14, Phase 4): decision journal
    # EN: (telemetry.jsonl, cf. wardenbot/telemetry.py) active by default under
    # EN: real conditions -- it's the raw material for wardenbot/kb_engine.py,
    # EN: which only has value with genuinely recorded sessions. Set to False
    # EN: here if you don't want the associated disk writes for a given
    # EN: session.
    engine = build_engine(character, dry_run=False, profile=profile, enable_telemetry=True)  # mettre dry_run à True pour tester sans presser de touches
    print(f"[main] Personnage : {character}")
    print(f"[main] Profil : {profile or 'Default'}")
    print(f"[main] debug -> {DEBUG_LOG_PATH.resolve()}")
    if engine.telemetry is not None:
        print(f"[main] télémétrie -> {engine.telemetry.log_file.resolve()}")
    print("[main] C = pause/reprise rotation -- F9 = reset manuel warden_stacks")

    log_tick = make_debug_logger(engine.state)
    check_reset_hotkey = make_debug_reset_hotkey(engine.state)
    is_paused, check_pause_hotkey = make_pause_toggle(on_toggle=_log_pause_event)
    on_error = make_error_handler()

    def on_tick(pressed: Optional[str]) -> None:
        log_tick(pressed)
        check_reset_hotkey()
        check_pause_hotkey()

    tick_hz = CHARACTERS[character].get("tick_hz", 30.0)
    try:
        engine.run_forever(tick_hz=tick_hz, on_tick=on_tick, is_paused=is_paused, on_error=on_error)
    finally:
        # Vide la queue de télémétrie avant de quitter (cf.
        # wardenbot/telemetry.py::TelemetryWorker.stop) -- sans ça, les
        # toutes dernières décisions de la session pourraient ne jamais être
        # écrites (thread daemon tué au process exit).
        # EN: Drains the telemetry queue before exiting (cf.
        # EN: wardenbot/telemetry.py::TelemetryWorker.stop) -- without this,
        # EN: the session's very last decisions could never be written (daemon
        # EN: thread killed at process exit).
        if engine.telemetry is not None:
            engine.telemetry.stop()
2