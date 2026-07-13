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
from wardenbot.diagnostics import check_foreground_window, log_error
from wardenbot.engine import RotationEngine, State, load_abilities
from wardenbot.game_config import CHARACTERS, KEY_MAP, REGIONS
from wardenbot.input_sim import InputSimulator, InputWorker
from wardenbot.matcher import TemplateMatcher
from wardenbot.state_update import TARIQ_BUFF_BAR_THRESHOLDS  # noqa: F401 -- re-exporté, cf. build_yolo_dataset.py

DEBUG_LOG_PATH = Path("debug.log")


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


def build_engine(character: str, dry_run: bool = True) -> RotationEngine:
    cfg = CHARACTERS[character]
    key_map = KEY_MAP[character]
    regions = REGIONS[character]

    abilities, meta = load_abilities(cfg["config"])
    state = State(mode=cfg["default_mode"])

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
        input_worker.request_press(key, label=name, light=light)

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
    character = choose_character()
    engine = build_engine(character, dry_run=False)  # mettre à True pour tester sans presser de touches
    print(f"[main] Personnage : {character}")
    print(f"[main] debug -> {DEBUG_LOG_PATH.resolve()}")
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
    engine.run_forever(tick_hz=tick_hz, on_tick=on_tick, is_paused=is_paused, on_error=on_error)
