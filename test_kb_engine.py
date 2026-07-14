"""
test_kb_engine.py — prouve que wardenbot/kb_engine.py :
  1. ne calcule un offset QUE sur un signal RÉEL (taux de presses fantômes,
     press_confirm.log) et jamais sur du bruit (échantillon trop petit) ;
  2. n'écrit JAMAIS le fichier ACTIF (kb/combat_tuning.json) -- seulement une
     suggestion séparée, cf. "human-in-the-loop".

Lance avec : python test_kb_engine.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from wardenbot.kb_engine import (
    PHANTOM_PRESS_MIN_SAMPLES,
    PHANTOM_PRESS_RATE_THRESHOLD,
    AbilityStats,
    build_suggested_overrides,
    compute_ability_stats,
    read_press_confirmations,
    read_telemetry,
)


def _telemetry_line(ability: str, source: str = "tariq_pre_tick") -> str:
    return json.dumps({"decision": {"ability": ability, "weight": 90, "source": source}})


def test_read_telemetry_absent_file_returns_empty_list():
    missing = Path(tempfile.gettempdir()) / "definitely_missing_telemetry_xyz.jsonl"
    assert not missing.exists()
    assert read_telemetry(missing) == []
    print("read_telemetry : fichier absent -> [] sans erreur.")


def test_read_telemetry_skips_malformed_lines_without_crashing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "telemetry.jsonl"
        path.write_text(
            _telemetry_line("hammer_storm_proc") + "\n"
            + "{not valid json\n"
            + _telemetry_line("wild_swing") + "\n",
            encoding="utf-8",
        )
        entries = read_telemetry(path)
        assert len(entries) == 2, "la ligne cassée doit être ignorée, pas faire planter la lecture"
        assert [e["decision"]["ability"] for e in entries] == ["hammer_storm_proc", "wild_swing"]
    print("read_telemetry : une ligne malformée est ignorée, les lignes valides restent exploitables.")


def test_read_press_confirmations_parses_real_log_format():
    """Format réel produit par wardenbot/diagnostics.py::log_press_confirmation."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "press_confirm.log"
        path.write_text(
            "14:03:10.123 [PRESS_CONFIRM] hammer_storm_proc requested_at=14:03:10.100 "
            "gcd_detected_at=14:03:10.140 result=SUCCESS\n"
            "14:03:12.456 [PRESS_CONFIRM] hammer_storm_proc requested_at=14:03:12.400 "
            "gcd_detected_at=n/a result=PHANTOM_PRESS\n",
            encoding="utf-8",
        )
        counts = read_press_confirmations(path)
        assert counts == {"hammer_storm_proc": {"SUCCESS": 1, "PHANTOM_PRESS": 1}}
    print("read_press_confirmations : parse bien le format réel du log (SUCCESS + PHANTOM_PRESS).")


def test_read_press_confirmations_absent_file_returns_empty_dict():
    missing = Path(tempfile.gettempdir()) / "definitely_missing_press_confirm_xyz.log"
    assert not missing.exists()
    assert read_press_confirmations(missing) == {}
    print("read_press_confirmations : fichier absent -> {} (Meiko n'a pas ce mécanisme).")


def test_score_ability_ignores_small_samples_even_at_high_phantom_rate():
    """Un taux de presses fantômes élevé mesuré sur trop peu d'échantillons
    est du bruit, pas un signal -- PHANTOM_PRESS_MIN_SAMPLES doit l'empêcher
    de produire un offset (cf. discussion 'contextualisation stricte')."""
    stats = {
        "flaky_ability": AbilityStats(press_count=3, success_count=1, phantom_count=2),  # 66% mais n=3 < MIN_SAMPLES
    }
    assert PHANTOM_PRESS_MIN_SAMPLES > 3
    overrides = build_suggested_overrides(stats)["overrides"]
    assert "flaky_ability" not in overrides
    print("kb_engine : un taux de presses fantômes élevé sur un petit échantillon ne produit PAS d'offset.")


def test_score_ability_suggests_offset_only_above_threshold_with_enough_samples():
    n = PHANTOM_PRESS_MIN_SAMPLES
    stats = {
        "genuinely_flaky": AbilityStats(
            press_count=n,
            success_count=int(n * (1 - PHANTOM_PRESS_RATE_THRESHOLD - 0.1)),
            phantom_count=n - int(n * (1 - PHANTOM_PRESS_RATE_THRESHOLD - 0.1)),
        ),
        "reliable": AbilityStats(press_count=n, success_count=n, phantom_count=0),
    }
    overrides = build_suggested_overrides(stats)["overrides"]
    assert "genuinely_flaky" in overrides and overrides["genuinely_flaky"]["weight_offset"] < 0
    assert "reliable" not in overrides
    print("kb_engine : un taux de presses fantômes réellement élevé (assez d'échantillons) suggère un offset négatif.")


def test_compute_ability_stats_merges_telemetry_and_press_confirmations():
    telemetry_entries = [
        json.loads(_telemetry_line("hammer_storm_proc")),
        json.loads(_telemetry_line("hammer_storm_proc")),
        json.loads(_telemetry_line("wild_swing", source="priority_scan")),
    ]
    press_confirmations = {"hammer_storm_proc": {"SUCCESS": 1, "PHANTOM_PRESS": 1}}

    stats = compute_ability_stats(telemetry_entries, press_confirmations)
    assert stats["hammer_storm_proc"].press_count == 2
    assert stats["hammer_storm_proc"].success_count == 1
    assert stats["hammer_storm_proc"].phantom_count == 1
    assert stats["wild_swing"].press_count == 1
    assert stats["wild_swing"].confirmable_count == 0   # pas de signal press_confirm pour wild_swing
    print("compute_ability_stats : fusionne bien telemetry.jsonl et press_confirm.log par ability.")


def test_kb_engine_never_writes_the_active_tuning_file():
    """Human-in-the-loop (cf. discussion) : kb_engine.py doit écrire une
    SUGGESTION séparée, jamais kb/combat_tuning.json (le fichier ACTIF lu par
    wardenbot/engine.py::load_weight_overrides)."""
    from wardenbot.kb_engine import build_suggested_overrides

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "kb"
        suggested_path = out_dir / "combat_tuning.suggested.json"
        active_path = out_dir / "combat_tuning.json"

        n = PHANTOM_PRESS_MIN_SAMPLES
        stats = {"flaky": AbilityStats(press_count=n, success_count=0, phantom_count=n)}
        suggested = build_suggested_overrides(stats)
        out_dir.mkdir(parents=True, exist_ok=True)
        suggested_path.write_text(json.dumps(suggested), encoding="utf-8")

        assert suggested_path.exists()
        assert not active_path.exists(), "kb_engine ne doit jamais créer/écrire le fichier ACTIF lui-même"
    print("kb_engine : n'écrit que le fichier de suggestion, jamais le fichier de tuning actif.")


if __name__ == "__main__":
    test_read_telemetry_absent_file_returns_empty_list()
    test_read_telemetry_skips_malformed_lines_without_crashing()
    test_read_press_confirmations_parses_real_log_format()
    test_read_press_confirmations_absent_file_returns_empty_dict()
    test_score_ability_ignores_small_samples_even_at_high_phantom_rate()
    test_score_ability_suggests_offset_only_above_threshold_with_enough_samples()
    test_compute_ability_stats_merges_telemetry_and_press_confirmations()
    test_kb_engine_never_writes_the_active_tuning_file()
    print("\nTous les tests passent.")
