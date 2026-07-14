"""
test_state_update.py — prouve que les nouveaux champs de fraîcheur (Phase 0,
2026-07-14) sont cohérents entre eux, PAS que le thread de détection les
timbre correctement en conditions réelles (cf. limite ci-dessous).

CE QUE CE FICHIER NE TESTE PAS, ET POURQUOI : _run_meiko_detection_worker/
_detection_cycle font de la vraie capture d'écran (mss) -- les appeler
démarrerait un thread réel, pas un test unitaire. make_meiko_state_update_fn/
make_tariq_state_update_fn démarrent TOUJOURS ce thread comme effet de bord
(cf. leur docstring) : il n'existe aujourd'hui aucun moyen d'obtenir la
closure state_update_fn sans ça. C'est une limite PRÉEXISTANTE de ce fichier
(state_update.py n'a jamais eu de test unitaire direct, cf. CLAUDE.md :
test_wardenbot.py reste volontairement sans écran ni mss/cv2) -- ce test ne
prétend pas la combler, seulement vérifier ce qui EST vérifiable sans écran :
la forme des dataclasses de snapshot et la cohérence des FreshnessPolicy avec
les intervalles de throttle réels qu'elles sont censées représenter.

Lance avec : python test_state_update.py
Dépend de mss/opencv (comme tout wardenbot.state_update) -- contrairement à
test_wardenbot.py, volontairement screen-free.
"""
from __future__ import annotations

from wardenbot.freshness import Freshness
from wardenbot.state_update import (
    MEIKO_OCR_FRESHNESS_POLICY,
    MEIKO_OCR_INTERVAL_S,
    MEIKO_YOLO_DETECT_INTERVAL_S,
    MEIKO_YOLO_FRESHNESS_POLICY,
    TARIQ_BARS_FRESHNESS_POLICY,
    TARIQ_DETECTION_WORKER_HZ,
    TARIQ_YOLO_FRESHNESS_POLICY,
    YOLO_DETECT_INTERVAL_S,
    MeikoDetectionSnapshot,
    TariqDetectionSnapshot,
)


def test_meiko_snapshot_freshness_fields_default_to_never_measured():
    snap = MeikoDetectionSnapshot()
    assert snap.created_at == 0.0
    assert snap.yolo_created_at == 0.0
    assert snap.ocr_created_at is None   # cf. discussion point 4 -- None, pas un faux horodatage, tant qu'aucune lecture OCR n'a eu lieu
    print("MeikoDetectionSnapshot : created_at/yolo_created_at/ocr_created_at par défaut cohérents avec 'jamais mesuré'.")


def test_tariq_snapshot_freshness_fields_default_to_never_measured():
    snap = TariqDetectionSnapshot()
    assert snap.created_at == 0.0
    assert snap.yolo_created_at == 0.0
    print("TariqDetectionSnapshot : created_at/yolo_created_at par défaut à 0.0 (jamais mesuré).")


def test_meiko_yolo_freshness_policy_matches_the_real_throttle_interval():
    """Garde-fou contre le dérapage silencieux (cf. audit architecture,
    'dette #3') : si MEIKO_YOLO_DETECT_INTERVAL_S change un jour sans que la
    politique de fraîcheur soit mise à jour, CE test doit le détecter."""
    throttle_ms = MEIKO_YOLO_DETECT_INTERVAL_S * 1000
    assert MEIKO_YOLO_FRESHNESS_POLICY.classify(throttle_ms) == Freshness.FRESH, (
        "une lecture aussi vieille que l'intervalle de throttle lui-même doit rester FRESH -- "
        "sinon la politique est plus stricte que le débit réel de détection, et deviendrait STALE en permanence"
    )
    print("MEIKO_YOLO_FRESHNESS_POLICY : reste cohérente avec MEIKO_YOLO_DETECT_INTERVAL_S.")


def test_meiko_ocr_freshness_policy_matches_the_real_throttle_interval():
    throttle_ms = MEIKO_OCR_INTERVAL_S * 1000
    assert MEIKO_OCR_FRESHNESS_POLICY.classify(throttle_ms) == Freshness.FRESH
    print("MEIKO_OCR_FRESHNESS_POLICY : reste cohérente avec MEIKO_OCR_INTERVAL_S.")


def test_tariq_yolo_freshness_policy_matches_the_real_throttle_interval():
    throttle_ms = YOLO_DETECT_INTERVAL_S * 1000
    assert TARIQ_YOLO_FRESHNESS_POLICY.classify(throttle_ms) == Freshness.FRESH
    print("TARIQ_YOLO_FRESHNESS_POLICY : reste cohérente avec YOLO_DETECT_INTERVAL_S.")


def test_tariq_bars_freshness_policy_matches_the_real_worker_period():
    cycle_ms = (1.0 / TARIQ_DETECTION_WORKER_HZ) * 1000
    assert TARIQ_BARS_FRESHNESS_POLICY.classify(cycle_ms) == Freshness.FRESH, (
        "une lecture aussi vieille qu'un cycle du thread détection doit rester FRESH -- "
        "sinon la politique signalerait STALE en régime NORMAL, pas seulement en cas de blocage réel du thread"
    )
    print("TARIQ_BARS_FRESHNESS_POLICY : reste cohérente avec TARIQ_DETECTION_WORKER_HZ.")


if __name__ == "__main__":
    test_meiko_snapshot_freshness_fields_default_to_never_measured()
    test_tariq_snapshot_freshness_fields_default_to_never_measured()
    test_meiko_yolo_freshness_policy_matches_the_real_throttle_interval()
    test_meiko_ocr_freshness_policy_matches_the_real_throttle_interval()
    test_tariq_yolo_freshness_policy_matches_the_real_throttle_interval()
    test_tariq_bars_freshness_policy_matches_the_real_worker_period()
    print("\nTous les tests passent.")
