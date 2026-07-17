"""
test_telemetry.py — prouve que TelemetryWorker journalise en JSONL sans
bloquer l'appelant (log_decision ne fait qu'un queue.put(), l'écriture disque
arrive sur le thread séparé) et que le fichier produit est exploitable (une
ligne JSON valide par décision, dans l'ordre, y compris au moment de
l'arrêt).

Lance avec : python test_telemetry.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from wardenbot.engine import CombatState, Intent
from wardenbot.telemetry import TelemetryWorker


def test_log_decision_writes_valid_jsonl_entry():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "telemetry.jsonl"
        worker = TelemetryWorker(log_file=log_path)
        worker.start()

        state = CombatState(mode="st")
        state.resources["fury"] = 0.62
        state.buffs["focused_wrath_empowered"] = True
        state.planner["tariq_objective"] = "normal"
        state.hooks["hammer_storm_proc_ready"] = True
        state.hooks["_hammer_storm_empowered_prev"] = True   # clé interne -- ne doit apparaître QUE dans hooks_keys, jamais sa valeur

        intent = Intent(ability="hammer_storm_proc", weight=90, source="tariq_pre_tick")
        worker.log_decision(state, intent, extra={"gcd_slack_ms": 12.4})

        worker.stop(timeout=5.0)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        # target (2026-07-15, Vigour) : 4e champ d'Intent, None ici -- Tariq ne
        # cible jamais un allié précis, cf. wardenbot/engine.py::Intent.
        assert entry["decision"] == {
            "ability": "hammer_storm_proc", "weight": 90, "source": "tariq_pre_tick", "target": None,
        }
        assert entry["state_snapshot"]["mode"] == "st"
        assert entry["state_snapshot"]["resources"]["fury"] == 0.62
        assert entry["state_snapshot"]["buffs"]["focused_wrath_empowered"] is True
        assert entry["state_snapshot"]["planner"]["tariq_objective"] == "normal"
        assert sorted(entry["state_snapshot"]["hooks_keys"]) == sorted(
            ["hammer_storm_proc_ready", "_hammer_storm_empowered_prev"]
        )
        assert entry["gcd_slack_ms"] == 12.4
        assert "timestamp" in entry
    print("TelemetryWorker : une décision produit bien une ligne JSONL exploitable, sans bloquer.")


def test_multiple_decisions_are_appended_in_order():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "telemetry.jsonl"
        worker = TelemetryWorker(log_file=log_path)
        worker.start()

        state = CombatState()
        for name in ("a", "b", "c"):
            worker.log_decision(state, Intent(ability=name, weight=90, source="test"))

        worker.stop(timeout=5.0)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(line)["decision"]["ability"] for line in lines] == ["a", "b", "c"]
    print("TelemetryWorker : plusieurs décisions s'accumulent dans l'ordre, en append.")


def test_stop_drains_queue_instead_of_dropping_pending_entries():
    """BUG ÉVITÉ EN ÉCRIVANT CE TEST (cf. telemetry.py::run()) : arrêter le
    worker juste après avoir mis une décision en queue ne doit JAMAIS perdre
    cette décision, même si le thread n'a pas encore eu la main -- c'est
    exactement le cas réel "on arrête le bot juste après sa dernière presse
    de la session"."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "telemetry.jsonl"
        worker = TelemetryWorker(log_file=log_path)
        worker.start()

        state = CombatState()
        for name in ("first", "second", "last"):
            worker.log_decision(state, Intent(ability=name, weight=90, source="test"))
        worker.stop(timeout=5.0)   # appelé immédiatement après -- pas de sleep avant

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert [json.loads(line)["decision"]["ability"] for line in lines] == ["first", "second", "last"]
    print("TelemetryWorker : stop() vide bien la queue avant de s'arrêter, aucune décision perdue.")


def test_malformed_entry_does_not_kill_the_worker_thread():
    """Une entrée qui ne peut pas être sérialisée en JSON ne doit jamais tuer
    silencieusement le thread daemon -- cf. docstring TelemetryWorker.run()."""
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "telemetry.jsonl"
        worker = TelemetryWorker(log_file=log_path)
        worker.start()

        state = CombatState()
        # object() n'est pas JSON-sérialisable -- glissé directement dans la
        # queue pour simuler une entrée corrompue (log_decision ne construit
        # normalement que des types sérialisables, cf. Intent/CombatState).
        worker._queue.put({"broken": object()})
        worker.log_decision(state, Intent(ability="ok_after_broken", weight=50, source="test"))

        worker.stop(timeout=5.0)

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, "seule l'entrée valide doit être écrite -- la corrompue est perdue, pas fatale"
        assert json.loads(lines[0])["decision"]["ability"] == "ok_after_broken"
    print("TelemetryWorker : une entrée corrompue est perdue proprement, le thread continue.")


if __name__ == "__main__":
    test_log_decision_writes_valid_jsonl_entry()
    test_multiple_decisions_are_appended_in_order()
    test_stop_drains_queue_instead_of_dropping_pending_entries()
    test_malformed_entry_does_not_kill_the_worker_thread()
    print("\nTous les tests passent.")
