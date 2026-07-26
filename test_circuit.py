"""
test_circuit.py — prouve, sans écran ni jeu (aucune dépendance externe, seul
`wardenbot.circuit` -- stdlib pur -- est importé), que le coupe-circuit
(cf. wardenbot/circuit.py, audit "RPA professionnel" 2026-07-26) respecte
ses deux critères de déclenchement INDÉPENDANTS :
  1. Panne franche : erreurs consécutives sans AUCUN succès pendant
     max_streak_s.
  2. Flapping : ratio d'échecs au-dessus du seuil sur une fenêtre glissante,
     même quand un succès occasionnel empêche le critère 1 de se déclencher
     -- le scénario le plus vicieux en pratique (spam de logs pendant des
     heures sans jamais que le critère 1 morde).

Chaque test utilise une horloge SIMULÉE (des float croissants passés
explicitement à record_error/record_success, jamais time.monotonic() réel)
et désactive le critère qu'il ne teste pas (seuil énorme ou
min_flap_samples énorme) pour isoler proprement les deux mécaniques.

Lance avec : python test_circuit.py
"""
from __future__ import annotations

from wardenbot.circuit import make_circuit_breaker


def test_hard_streak_trips_after_max_streak_s_without_any_success():
    """Critère 1 seul (flapping désactivé via min_flap_samples énorme) :
    des erreurs à 1 par seconde ne doivent PAS déclencher avant max_streak_s,
    puis doivent déclencher pile au moment où l'écart au 1er échec du streak
    atteint max_streak_s."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=10.0, flap_window_s=30.0, flap_error_ratio=0.9,
        min_flap_samples=10_000, on_trip=reasons.append,
    )
    for t in range(10):  # t=0..9 : 9s d'écart max avec le 1er échec (t=0)
        record_error(float(t))
    assert is_tripped() is False, "ne doit pas encore avoir déclenché avant max_streak_s"
    record_error(10.0)  # écart de 10s avec le 1er échec (t=0) -> atteint max_streak_s
    assert is_tripped() is True, "doit déclencher pile à max_streak_s sans aucun succès"
    assert len(reasons) == 1 and "panne franche" in reasons[0]
    print("Panne franche : ne déclenche ni trop tôt ni trop tard, raison correcte.")


def test_success_resets_the_hard_streak():
    """Un seul succès au milieu d'un streak d'erreurs doit repousser le
    déclenchement du critère 1 -- sinon un succès isolé ne "compterait pour
    rien" et le critère 1 perdrait son sens (panne vraiment CONTINUE)."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=10.0, flap_window_s=30.0, flap_error_ratio=0.9,
        min_flap_samples=10_000, on_trip=reasons.append,
    )
    for t in range(9):  # t=0..8, presque au seuil (9s < 10s)
        record_error(float(t))
    record_success(9.0)  # casse le streak avant qu'il n'atteigne 10s
    assert is_tripped() is False
    record_error(10.0)  # nouveau streak qui repart de zéro (streak_start=10.0)
    assert is_tripped() is False, "le nouveau streak ne fait que commencer"
    record_error(20.0)  # écart de 10s avec CE nouveau streak_start (10.0)
    assert is_tripped() is True, "le nouveau streak, lui, doit bien déclencher à son propre +10s"
    print("Un succès isolé repousse bien le déclenchement du critère 1 (panne franche).")


def test_flapping_trips_despite_rare_successes_preventing_hard_streak():
    """LE SCÉNARIO LE PLUS VICIEUX (cf. plan) : un succès toutes les 10 ticks
    empêche à répétition le critère 1 de mordre (le streak est cassé avant
    max_streak_s à chaque fois), alors que le bot est fonctionnellement mort
    (90% d'échecs). Le critère 2 (ratio sur fenêtre glissante) doit quand
    même déclencher."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=1000.0,  # critère 1 désactivé pour isoler le critère 2
        flap_window_s=30.0, flap_error_ratio=0.9, min_flap_samples=10,
        on_trip=reasons.append,
    )
    for t in range(9):  # t=0..8 : 9 échecs
        record_error(float(t))
    assert is_tripped() is False, "seulement 9 échantillons, sous min_flap_samples"
    record_success(9.0)  # 10e échantillon : le seul succès -> ratio pile 90%
    assert is_tripped() is True, "9 échecs / 10 échantillons = 90% -> doit déclencher"
    assert len(reasons) == 1 and "flapping" in reasons[0]
    print("Flapping : déclenche via le ratio même quand des succès rares cassent le streak.")


def test_flapping_does_not_trip_below_min_flap_samples():
    """Même à 100% d'échecs, en dessous de min_flap_samples le critère 2 ne
    doit pas déclencher (pas assez d'échantillons pour conclure à un
    flapping réel plutôt qu'un début de panne franche normal)."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=1000.0, flap_window_s=30.0, flap_error_ratio=0.9,
        min_flap_samples=10, on_trip=reasons.append,
    )
    for t in range(9):  # 9 < min_flap_samples=10
        record_error(float(t))
    assert is_tripped() is False
    assert reasons == []
    print("Sous min_flap_samples, aucun déclenchement même à 100% d'échecs.")


def test_reset_clears_state_and_fault_persisting_retrips_as_expected():
    """reset() doit repartir de zéro ; ET si la panne persiste après un
    reset (ex. reprise manuelle via C alors que le problème n'est pas
    résolu), le breaker doit re-déclencher naturellement -- COMPORTEMENT
    ATTENDU (cf. plan), pas une régression."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=5.0, flap_window_s=1000.0, flap_error_ratio=0.9,
        min_flap_samples=10_000, on_trip=reasons.append,
    )
    record_error(0.0)
    record_error(5.0)  # écart de 5s -> déclenche
    assert is_tripped() is True
    assert len(reasons) == 1

    reset()
    assert is_tripped() is False, "reset() doit repartir de zéro"

    # La panne sous-jacente persiste (reprise manuelle prématurée) :
    record_error(6.0)   # nouveau streak_start=6.0 après reset
    assert is_tripped() is False
    record_error(11.0)  # écart de 5s avec CE nouveau streak -> re-déclenche
    assert is_tripped() is True, "re-déclenchement attendu si la panne persiste après reset"
    assert len(reasons) == 2, "on_trip doit être rappelé pour CE nouveau déclenchement"
    print("reset() repart de zéro ; re-déclenchement si la panne persiste : comportement attendu, pas un bug.")


def test_on_trip_not_spammed_while_already_tripped():
    """Une fois déclenché, des record_error/record_success supplémentaires
    ne doivent PAS rappeler on_trip en boucle (sinon un bip/pause spammé
    indéfiniment tant que l'utilisateur n'a pas repris la main)."""
    reasons = []
    record_error, record_success, is_tripped, reset = make_circuit_breaker(
        max_streak_s=5.0, flap_window_s=1000.0, flap_error_ratio=0.9,
        min_flap_samples=10_000, on_trip=reasons.append,
    )
    record_error(0.0)
    record_error(5.0)
    assert len(reasons) == 1
    for t in range(6, 20):
        record_error(float(t))
        record_success(float(t) + 0.5)
    assert len(reasons) == 1, "on_trip ne doit pas être rappelé tant que reset() n'a pas été appelé"
    print("on_trip n'est pas spammé une fois déclenché, malgré des appels supplémentaires.")


if __name__ == "__main__":
    test_hard_streak_trips_after_max_streak_s_without_any_success()
    test_success_resets_the_hard_streak()
    test_flapping_trips_despite_rare_successes_preventing_hard_streak()
    test_flapping_does_not_trip_below_min_flap_samples()
    test_reset_clears_state_and_fault_persisting_retrips_as_expected()
    test_on_trip_not_spammed_while_already_tripped()
    print("\nTous les tests circuit sont passés.")
