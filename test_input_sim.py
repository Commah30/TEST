"""
test_input_sim.py — prouve que InputWorker.request_press() ne bloque jamais
l'appelant, même quand la queue interne est pleine (audit architecture,
2026-07-18 : queue.Queue() était non bornée jusqu'ici, cf. hooks.py/engine.py
pour les autres correctifs du même audit).

Lance avec : python test_input_sim.py
Aucune dépendance externe (pydirectinput n'est pas requis -- InputSimulator
force dry_run=True dès qu'il est absent, cf. input_sim.py).
"""
from __future__ import annotations

import queue

from wardenbot.input_sim import InputSimulator, InputWorker


def _stopped_worker() -> InputWorker:
    """Construit un InputWorker puis attend que son thread ait RÉELLEMENT
    terminé (join(), pas juste stop()) avant de rendre la main -- élimine
    toute course avec le thread de dépilement : une fois join() revenu, plus
    rien ne peut jamais vider la queue, ce qui rend le test de bornage
    déterministe plutôt que dépendant du timing réel du thread."""
    worker = InputWorker(InputSimulator(dry_run=True))
    worker.stop()
    worker._thread.join(timeout=2.0)
    assert not worker._thread.is_alive(), "le thread d'input aurait dû s'arrêter avant la fin du test"
    return worker


def test_queue_has_a_bounded_maxsize():
    worker = _stopped_worker()
    assert worker._queue.maxsize == InputWorker.MAX_QUEUE_SIZE > 0, (
        "la queue doit être bornée (audit architecture, 2026-07-18) -- plus jamais queue.Queue() sans maxsize"
    )
    print(f"InputWorker._queue est bornée à {InputWorker.MAX_QUEUE_SIZE} (plus jamais illimitée).")


def test_request_press_never_blocks_when_queue_is_full():
    """Remplit la queue jusqu'à MAX_QUEUE_SIZE (thread déjà arrêté -- cf.
    _stopped_worker, personne ne la vide en parallèle) puis presse encore
    une fois : ne doit NI bloquer NI lever d'exception -- juste dropper la
    demande (cf. hooks.py::request_press, log_error journalise l'anomalie
    sans jamais remonter à l'appelant)."""
    worker = _stopped_worker()
    for i in range(InputWorker.MAX_QUEUE_SIZE):
        worker.request_press("1", label=f"filler_{i}")
    assert worker._queue.qsize() == InputWorker.MAX_QUEUE_SIZE

    worker.request_press("1", label="one_too_many")   # ne doit pas bloquer/lever -- sinon le test resterait pendu
    assert worker._queue.qsize() == InputWorker.MAX_QUEUE_SIZE, (
        "la demande en trop doit être abandonnée (drop), pas empilée au-delà de maxsize"
    )
    print("Queue pleine (32/32) -- une presse de plus est abandonnée proprement, jamais de blocage.")


def test_request_press_still_enqueues_normally_below_max_size():
    """Non-régression : en dessous du plafond, le comportement normal
    (empilement réel, quasi instantané) est inchangé."""
    worker = _stopped_worker()
    worker.request_press("3", label="greater_heal")
    assert worker._queue.qsize() == 1
    key, label, light, enqueue_ts, queue_depth_at_enqueue = worker._queue.get_nowait()
    assert (key, label) == ("3", "greater_heal")
    assert queue_depth_at_enqueue == 0, "la queue était vide au moment de cet enqueue précis"
    print("Sous le plafond -- comportement d'empilement inchangé (non-régression).")


if __name__ == "__main__":
    test_queue_has_a_bounded_maxsize()
    test_request_press_never_blocks_when_queue_is_full()
    test_request_press_still_enqueues_normally_below_max_size()
    print("\nTous les tests passent.")
