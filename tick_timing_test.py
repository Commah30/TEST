"""
tick_timing_test.py — mesure la durée réelle d'un tick complet (capture
écran + template matching + YOLO + EasyOCR throttlés + hooks + choix
d'ability), SANS presser de touche (press_fn est un no-op qui enregistre
juste le nom, jamais pydirectinput/InputSimulator).

Contrairement à build_engine(dry_run=True) (main.py), qui désactive
ENTIÈREMENT la capture/détection (capturer=None, state_update_fn=None) et ne
mesurerait donc rien d'utile ici -- ce script câble les VRAIES pièces
(ScreenCapture, TemplateMatcher, make_tariq_state_update_fn, hooks réels)
comme en prod, juste avec un press_fn inoffensif.

Peu importe si le jeu tourne ou non : les régions captureront ce qu'il y a
à l'écran à ces coordonnées (bureau, IDE...) -- le contenu n'a aucune
importance pour cette mesure, seul le COÛT de la capture/matching/YOLO/OCR
compte.

USAGE :
    python tick_timing_test.py [character=tariq] [n_ticks=200]

GÉNÉRALISÉ (2026-07-19, cf. discussion audit performance) -- codé en dur sur
Tariq à l'origine (seul personnage avec un vrai souci de budget confirmé à
l'époque). pre_tick_hook/post_tick_hook/off_gcd_hook_abilities sont
maintenant lus directement depuis CHARACTERS[character] (déjà la source de
vérité, cf. game_config.py) plutôt qu'importés par leur nom -- élimine le
couplage dur à Tariq sans changer le comportement pour Tariq lui-même.
off_gcd_hook_abilities utilise .get(...) avec un défaut (frozenset()) : seul
Tariq renseigne cette clé dans CHARACTERS, les autres personnages n'en ont
simplement pas besoin (cf. RotationEngine, défaut identique).

Le throttle YOLO (YOLO_DETECT_INTERVAL_S) et OCR (STACK_OCR_INTERVAL_S) sont
basés sur le temps réel écoulé (time.monotonic()) -- ce script tourne les
ticks dos-à-dos sans pause artificielle, donc les ticks "chers" (YOLO/OCR
qui se relance) et "gratuits" (throttle pas encore écoulé) apparaissent
naturellement, comme en prod à 30Hz.
"""
from __future__ import annotations

import sys
import time

from wardenbot.capture import ScreenCapture
from wardenbot.matcher import TemplateMatcher
from wardenbot.engine import CombatState, RotationEngine, load_abilities

from main import CHARACTERS, REGIONS


def main():
    character = sys.argv[1] if len(sys.argv) > 1 else "tariq"
    n_ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    cfg = CHARACTERS[character]
    regions = REGIONS[character]

    abilities, meta = load_abilities(cfg["config"])
    state = CombatState(mode=cfg["default_mode"])

    matcher = TemplateMatcher(cfg["templates"])
    capturer = ScreenCapture()
    make_state_update_fn = cfg["make_state_update_fn"]
    state_update_fn = make_state_update_fn(capturer, matcher, regions) if make_state_update_fn is not None else None

    pressed_log = []

    def press_fn(name: str) -> None:
        pressed_log.append(name)  # no-op : jamais de vraie touche envoyée

    def is_ready_fn(name: str, confidence_threshold=None) -> bool:
        region = regions.get(name)
        if region is None:
            return True
        frame = capturer.grab(region)
        threshold = confidence_threshold if confidence_threshold is not None else 0.70
        return matcher.is_match(frame, name, threshold)

    engine = RotationEngine(
        abilities=abilities,
        state=state,
        press_fn=press_fn,
        is_ready_fn=is_ready_fn,
        gcd_ms=meta.get("gcd_ms", 1100),
        pre_tick_hook=cfg["pre_tick_hook"],
        post_tick_hook=cfg["post_tick_hook"],
        state_update_fn=state_update_fn,
        off_gcd_hook_abilities=cfg.get("off_gcd_hook_abilities", frozenset()),
    )

    print(f"Personnage : {character}\n")

    print(f"Mesure de {n_ticks} ticks (capture réelle, YOLO+EasyOCR throttlés comme en prod, aucune touche pressée)...\n")

    durations = []
    for i in range(n_ticks):
        start = time.perf_counter()
        pressed = engine.tick()
        duration = (time.perf_counter() - start) * 1000
        durations.append(duration)
        marker = f" <- pressed={pressed}" if pressed else ""
        print(f"tick {i:3d}: {duration:6.1f} ms{marker}")

    durations.sort()
    n = len(durations)
    print("\n--- Résumé ---")
    print(f"min:    {durations[0]:.1f} ms")
    print(f"médian: {durations[n // 2]:.1f} ms")
    print(f"moy:    {sum(durations) / n:.1f} ms")
    print(f"p95:    {durations[int(n * 0.95)]:.1f} ms")
    print(f"max:    {durations[-1]:.1f} ms")
    print(f"\nBudget cible à 30Hz : 33.3 ms/tick.")
    over_budget = sum(1 for d in durations if d > 33.3)
    print(f"{over_budget}/{n} ticks ({over_budget / n * 100:.0f}%) dépassent ce budget.")


if __name__ == "__main__":
    main()
