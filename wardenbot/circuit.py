"""
circuit.py — coupe-circuit générique (auto-pause après pannes soutenues).

Mécanisme PUR, stdlib uniquement (`time`/`collections.deque` en type hints
seulement -- aucun appel interne à `time.monotonic()`, l'horloge est TOUJOURS
injectée par l'appelant via le paramètre `now`) -- même séparation
mécanisme/politique que wardenbot/freshness.py (classification pure, jamais
la décision d'agir) : ce module décide UNIQUEMENT si le circuit doit être
ouvert, jamais comment réagir (bip, pause, log) -- ça reste au wiring
applicatif (main.py/app.py) via le callback `on_trip`.

Deux critères de déclenchement INDÉPENDANTS (OU logique), suite à l'audit
"RPA professionnel" (2026-07-26) : `main.py::make_error_handler` journalisait
une exception de tick indéfiniment sans jamais s'arrêter.

1. Panne franche : erreurs consécutives sans AUCUN tick réussi pendant
   `max_streak_s`. Fenêtre TEMPORELLE, pas un compteur brut d'erreurs, pour
   rester invariant au tick_hz du personnage (Tariq ~60Hz vs Saryn ~15Hz
   rendraient un seuil en COMPTE incohérent entre persos).
2. Flapping : cas dégénéré où un tick réussit de temps en temps par hasard
   (capture qui fonctionne une frame sur cent, faux positif de template) --
   ne déclencherait JAMAIS le critère 1 alors que le bot est fonctionnellement
   mort, et c'est le scénario qui spamme les logs pendant des heures. Fenêtre
   glissante `flap_window_s` : si le ratio d'échecs dépasse
   `flap_error_ratio` sur au moins `min_flap_samples` échantillons, trip
   aussi.

`record_error(now)` ET `record_success(now)` doivent TOUS LES DEUX être
appelés par l'appelant (un par tick, jamais les deux pour la même itération)
-- le critère de flapping calcule un RATIO, impossible à obtenir en ne
comptant que les échecs.

Une fois déclenché (`is_tripped()` -> True), reste déclenché jusqu'à
`reset()` explicite. Si la panne sous-jacente persiste après un `reset()`
(ex. reprise manuelle utilisateur pendant que le problème n'est pas résolu),
un nouveau streak/ratio re-déclenchera naturellement -- comportement VOULU,
pas un bug : une reprise manuelle est un acte informé, re-tripper confirme
juste que la panne n'est pas résolue.

EN: circuit.py — generic circuit breaker (auto-pause after sustained
EN: failures).

EN: PURE mechanism, stdlib only -- no internal call to `time.monotonic()`,
EN: the clock is ALWAYS injected by the caller via the `now` parameter --
EN: same mechanism/policy separation as wardenbot/freshness.py (pure
EN: classification, never the decision to act): this module only decides
EN: whether the circuit should open, never how to react (beep, pause, log)
EN: -- that stays application wiring (main.py/app.py) via the `on_trip`
EN: callback.

EN: Two INDEPENDENT trip criteria (logical OR), following the "professional
EN: RPA" audit (2026-07-26): `main.py::make_error_handler` used to log a
EN: tick exception forever without ever stopping.

EN: 1. Hard failure: consecutive errors with NO successful tick at all for
EN:    `max_streak_s`. TIME window, not a raw error count, to stay invariant
EN:    across character tick_hz (Tariq ~60Hz vs Saryn ~15Hz would make a
EN:    COUNT-based threshold behave inconsistently between characters).
EN: 2. Flapping: the degenerate case where a tick occasionally succeeds by
EN:    chance (capture that works one frame in a hundred, template false
EN:    positive) -- would NEVER trip criterion 1 even though the bot is
EN:    functionally dead, and is the scenario that spams logs for hours.
EN:    Sliding window `flap_window_s`: if the error ratio exceeds
EN:    `flap_error_ratio` over at least `min_flap_samples` samples, trip too.

EN: `record_error(now)` AND `record_success(now)` must BOTH be called by the
EN: caller (one per tick, never both for the same iteration) -- the flapping
EN: criterion computes a RATIO, impossible to get by only counting failures.

EN: Once tripped (`is_tripped()` -> True), stays tripped until an explicit
EN: `reset()`. If the underlying fault persists after a `reset()` (e.g. a
EN: manual user resume while the problem isn't fixed), a new streak/ratio
EN: will naturally re-trip -- INTENDED behavior, not a bug: a manual resume
EN: is an informed act, re-tripping just confirms the fault isn't resolved.
"""
from __future__ import annotations

from collections import deque
from typing import Callable, Deque, Tuple


def make_circuit_breaker(
    max_streak_s: float,
    flap_window_s: float,
    flap_error_ratio: float,
    min_flap_samples: int,
    on_trip: Callable[[str], None],
) -> Tuple[Callable[[float], None], Callable[[float], None], Callable[[], bool], Callable[[], None]]:
    """Retourne (record_error, record_success, is_tripped, reset).

    EN: Returns (record_error, record_success, is_tripped, reset)."""
    streak_start: list = [None]  # None ou float (now du 1er échec du streak courant)
    history: Deque[Tuple[float, bool]] = deque()  # (timestamp, success)
    tripped = [False]

    def _trip(reason: str) -> None:
        tripped[0] = True
        on_trip(reason)

    def _record(now: float, success: bool) -> None:
        history.append((now, success))
        while history and now - history[0][0] > flap_window_s:
            history.popleft()
        if tripped[0] or len(history) < min_flap_samples:
            return
        errors = sum(1 for _, ok in history if not ok)
        error_ratio = errors / len(history)
        if error_ratio >= flap_error_ratio:
            _trip(f"flapping : {error_ratio * 100:.0f}% d'échecs sur {flap_window_s:.0f}s")

    def record_error(now: float) -> None:
        if tripped[0]:
            return
        if streak_start[0] is None:
            streak_start[0] = now
        elif now - streak_start[0] >= max_streak_s:
            _trip(f"panne franche : {max_streak_s:.0f}s sans aucun tick réussi")
            return
        _record(now, success=False)

    def record_success(now: float) -> None:
        if tripped[0]:
            return
        streak_start[0] = None
        _record(now, success=True)

    def is_tripped() -> bool:
        return tripped[0]

    def reset() -> None:
        tripped[0] = False
        streak_start[0] = None
        history.clear()

    return record_error, record_success, is_tripped, reset
