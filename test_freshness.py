"""
test_freshness.py — prouve que FreshnessPolicy classe un âge en FRESH/STALE/
EXPIRED sans jamais décider quoi en faire, et que DetectionMeta.age_ms() est
testable sans dépendre du temps réel qui s'écoule pendant le test (cf.
discussion 2026-07-14, point 7 : "avant : pending_since, impossible à tester
correctement ; après : snapshot.created_at = now - 4s -> assert ... ==
EXPIRED").

Lance avec : python test_freshness.py
Aucune dépendance externe (module freshness.py n'importe que enum/dataclasses/time).
"""
from __future__ import annotations

from wardenbot.freshness import DetectionMeta, Freshness, FreshnessPolicy


def test_classify_boundaries_are_inclusive_on_fresh_and_stale_edges():
    """age_ms == fresh_ms doit encore être FRESH (pas STALE) ; age_ms ==
    stale_ms doit encore être STALE (pas EXPIRED) -- bornes inclusives des
    deux côtés, pas de zone floue entre les 3 états."""
    policy = FreshnessPolicy(fresh_ms=150, stale_ms=500)

    assert policy.classify(0) == Freshness.FRESH
    assert policy.classify(150) == Freshness.FRESH
    assert policy.classify(150.001) == Freshness.STALE
    assert policy.classify(500) == Freshness.STALE
    assert policy.classify(500.001) == Freshness.EXPIRED
    assert policy.classify(10_000) == Freshness.EXPIRED
    print("FreshnessPolicy.classify() : bornes FRESH/STALE/EXPIRED correctement inclusives.")


def test_detection_meta_age_ms_uses_explicit_now_for_deterministic_tests():
    """Le point central de la testabilité (cf. discussion) : pas besoin
    d'attendre du vrai temps qui passe, ni de mocker time.monotonic() --
    juste construire un DetectionMeta avec un created_at choisi et passer un
    `now` explicite."""
    meta = DetectionMeta(created_at=100.0)
    assert meta.age_ms(now=100.0) == 0.0
    assert abs(meta.age_ms(now=100.15) - 150.0) < 1e-6   # arithmétique flottante -- 0.15 n'est pas exact en binaire
    assert abs(meta.age_ms(now=104.0) - 4000.0) < 1e-6
    print("DetectionMeta.age_ms(now=...) : déterministe, sans dépendre du temps réel écoulé pendant le test.")


def test_freshness_policy_and_detection_meta_compose_end_to_end():
    """L'exemple exact de la discussion : une lecture YOLO vieille de 4s doit
    être classée EXPIRED par une politique calée sur le débit réel de YOLO
    (throttle ~150ms, cf. wardenbot/state_update.py)."""
    yolo_policy = FreshnessPolicy(fresh_ms=200, stale_ms=600)
    meta = DetectionMeta(created_at=1000.0)

    assert yolo_policy.classify(meta.age_ms(now=1000.05)) == Freshness.FRESH    # 50ms
    assert yolo_policy.classify(meta.age_ms(now=1000.30)) == Freshness.STALE    # 300ms
    assert yolo_policy.classify(meta.age_ms(now=1004.0)) == Freshness.EXPIRED   # 4s -- cf. discussion
    print("FreshnessPolicy + DetectionMeta : composent bien pour classer une lecture réellement périmée.")


def test_freshness_module_never_makes_an_acting_decision():
    """Vérifie noir sur blanc qu'aucune méthode de ce module ne prend de
    décision d'action -- seulement classify()/age_ms(), jamais can_act()/
    accept()/should_press() (cf. discussion point 3 : séparation stricte
    entre 'cette donnée est-elle exploitable' et 'dois-je agir')."""
    # dir() sur la CLASSE (pas une instance) ne liste que les méthodes -- les
    # champs de dataclass sans valeur par défaut n'existent qu'au niveau
    # instance (cf. __init__), donc absents ici. C'est justement ce qu'on
    # veut vérifier : aucune MÉTHODE de décision (can_act/accept/should_press).
    # EN: dir() on the CLASS (not an instance) only lists methods -- dataclass
    # EN: fields without a default only exist at the instance level (cf.
    # EN: __init__), so absent here. That's exactly what we want to check: no
    # EN: decision METHOD (can_act/accept/should_press).
    public_methods = {name for name in dir(FreshnessPolicy) if not name.startswith("_")}
    assert public_methods == {"classify"}
    meta_public_methods = {name for name in dir(DetectionMeta) if not name.startswith("_")}
    assert meta_public_methods == {"age_ms"}
    print("freshness.py : aucune API de décision d'action -- uniquement classification/mesure d'âge.")


if __name__ == "__main__":
    test_classify_boundaries_are_inclusive_on_fresh_and_stale_edges()
    test_detection_meta_age_ms_uses_explicit_now_for_deterministic_tests()
    test_freshness_policy_and_detection_meta_compose_end_to_end()
    test_freshness_module_never_makes_an_acting_decision()
    print("\nTous les tests passent.")
