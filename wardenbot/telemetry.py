"""
telemetry.py — journal de décisions (JSON Lines) pour l'apprentissage hors
ligne (Phase 4, 2026-07-14, cf. discussion architecture CombatState/Intent/
Decision Planner).

Écrit UNE ligne JSON par décision RÉELLEMENT pressée (pas à chaque tick --
30-60Hz rendrait le fichier illisible et inutilement volumineux pour ce que
"chaque décision majeure" veut dire, cf. discussion). Écriture disque
TOUJOURS sur un thread séparé (TelemetryWorker) : RotationEngine.tick() ne
fait qu'un queue.put() (opération mémoire, ~microsecondes) -- même principe
que tout le reste de ce projet (InputWorker pour les touches, les threads de
détection pour la capture/YOLO/OCR) : jamais d'I/O bloquant sur le thread de
décision.

OPT-IN, PAS AUTOMATIQUE : RotationEngine ne crée JAMAIS de TelemetryWorker
lui-même (cf. engine.py) -- si c'était le cas, chaque RotationEngine()
construit par les tests (des dizaines, cf. test_wardenbot.py/test_tariq.py)
ouvrirait un fichier et démarrerait un thread daemon, ce que CLAUDE.md
interdit explicitement ("test_wardenbot.py doit rester exécutable sans écran
ni dépendances lourdes"). C'est à l'appelant (main.py::build_engine) de
construire et démarrer un TelemetryWorker, puis de le passer explicitement à
RotationEngine(telemetry=...).

EN: telemetry.py — decision journal (JSON Lines) for offline learning
EN: (Phase 4, 2026-07-14, cf. CombatState/Intent/Decision Planner architecture
EN: discussion).
EN:
EN: Writes ONE JSON line per decision ACTUALLY pressed (not every tick --
EN: 30-60Hz would make the file unreadable and needlessly large for what
EN: "every major decision" means, cf. discussion). Disk writes ALWAYS happen
EN: on a separate thread (TelemetryWorker): RotationEngine.tick() only does a
EN: queue.put() (a memory operation, ~microseconds) -- same principle as
EN: everything else in this project (InputWorker for keypresses, the
EN: detection threads for capture/YOLO/OCR): never any blocking I/O on the
EN: decision thread.
EN:
EN: OPT-IN, NOT AUTOMATIC: RotationEngine NEVER creates a TelemetryWorker
EN: itself (cf. engine.py) -- if it did, every RotationEngine() built by the
EN: tests (dozens of them, cf. test_wardenbot.py/test_tariq.py) would open a
EN: file and start a daemon thread, which CLAUDE.md explicitly forbids
EN: ("test_wardenbot.py must stay runnable without a screen or heavy
EN: dependencies"). It's up to the caller (main.py::build_engine) to build and
EN: start a TelemetryWorker, then explicitly pass it to
EN: RotationEngine(telemetry=...).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

from wardenbot import __version__
from wardenbot.paths import data_path

# data_path(), PAS Path(...) nu (2026-07-26, cf. wardenbot/diagnostics.py
# pour la justification complète) : c'est le canal du coupe-circuit
# (wardenbot/circuit.py::log_incident) -- un incident qui échoue à
# s'écrire silencieusement (CWD différent du dossier de l'exe) serait
# l'inverse du contrat de ce mécanisme.
DEFAULT_TELEMETRY_LOG_PATH = data_path("telemetry.jsonl")

# Contrairement à debug.log (écrasé à chaque lancement, cf. main.py), ce
# fichier doit s'accumuler à travers PLUSIEURS sessions de jeu -- c'est la
# matière première de kb_engine.py, qui n'a de valeur qu'avec plusieurs
# combats enregistrés (cf. discussion "50 ou 100 combats"). Ouvert en mode
# append ("a"), jamais tronqué.
# EN: Unlike debug.log (overwritten on every launch, cf. main.py), this file
# EN: must accumulate ACROSS MULTIPLE game sessions -- it's the raw material
# EN: for kb_engine.py, which only has value once several combats are
# EN: recorded (cf. the "50 or 100 combats" discussion). Opened in append mode
# EN: ("a"), never truncated.

# Combien de temps le thread attend un item de la queue avant de revérifier
# `_stop_event` -- borne le délai d'arrêt de stop() sans faire de busy-loop.
# EN: How long the thread waits for a queue item before rechecking
# EN: `_stop_event` -- bounds stop()'s shutdown delay without a busy-loop.
_QUEUE_POLL_TIMEOUT_S = 1.0


class TelemetryWorker(threading.Thread):
    def __init__(self, log_file: "str | Path" = DEFAULT_TELEMETRY_LOG_PATH):
        super().__init__(daemon=True, name="telemetry-worker")
        self.log_file = Path(log_file)
        self._queue: "queue.Queue[dict]" = queue.Queue()
        # threading.Event, PAS un bool nu (cf. wardenbot/state_update.py,
        # même idiome pour les threads de détection) -- un bool nu lu/écrit
        # depuis 2 threads sans event n'a aucune garantie de visibilité
        # immédiate ; Event est fait pour exactement ce cas.
        # EN: threading.Event, NOT a bare bool (cf. wardenbot/state_update.py,
        # EN: same idiom for the detection threads) -- a bare bool read/written
        # EN: from 2 threads has no visibility guarantee; Event is built for
        # EN: exactly this case.
        self._stop_event = threading.Event()

    def log_decision(self, state, intent, extra: "Optional[dict[str, Any]]" = None) -> None:
        """Appelé par RotationEngine.tick() juste après une presse RÉELLE
        (jamais pour un tick qui n'a rien pressé -- cf. docstring module).
        Construit l'entrée ICI (sur le thread appelant -- lecture de state,
        pas d'I/O) puis se contente d'un queue.put() : le formatage JSON et
        l'écriture disque arrivent sur CE thread (run()), jamais sur celui de
        RotationEngine.tick().

        `extra` : dict optionnel de contexte supplémentaire propre à
        l'appelant (ex: gcd_slack_ms, choice_reason) -- fusionné tel quel
        dans l'entrée, PAS dans state_snapshot (qui ne contient QUE ce qui
        vient de state).

        EN: Called by RotationEngine.tick() right after a REAL press (never
        EN: for a tick that pressed nothing -- cf. module docstring). Builds
        EN: the entry HERE (on the calling thread -- reading state, no I/O)
        EN: then just does a queue.put(): JSON formatting and the disk write
        EN: happen on THIS thread (run()), never on RotationEngine.tick()'s.
        EN:
        EN: `extra`: optional dict of caller-specific extra context (e.g.
        EN: gcd_slack_ms, choice_reason) -- merged as-is into the entry, NOT
        EN: into state_snapshot (which holds ONLY what comes from state).
        """
        entry = {
            "timestamp": time.time(),
            "decision": asdict(intent),
            "state_snapshot": {
                "mode": state.mode,
                "resources": dict(state.resources),
                "buffs": dict(state.buffs),
                "planner": dict(state.planner),
                # Clés seulement, pas les valeurs -- state.hooks contient des
                # compteurs/horodatages de debounce internes (peu de valeur
                # analytique, cf. hooks.py) ; savoir QUELS mécanismes étaient
                # actifs suffit pour la corrélation, pas leur détail exact.
                # EN: Keys only, not values -- state.hooks holds internal
                # EN: debounce counters/timestamps (little analytical value,
                # EN: cf. hooks.py); knowing WHICH mechanisms were active is
                # EN: enough for correlation, not their exact detail.
                "hooks_keys": sorted(state.hooks.keys()),
            },
        }
        if extra:
            entry.update(extra)
        self._queue.put(entry)

    def log_incident(
        self,
        reason: str,
        character: str,
        last_exception: Optional[BaseException] = None,
    ) -> None:
        """Coupe-circuit (cf. wardenbot/circuit.py, audit "RPA professionnel"
        2026-07-26) : une ligne PAR déclenchement, type d'enregistrement
        distinct de log_decision (`"type": "incident"` -- log_decision n'a
        jamais eu ce champ, sa forme reste inchangée) pour que kb_engine.py
        (ou une lecture humaine de telemetry.jsonl) puisse un jour distinguer
        les deux sans ambiguïté.

        `reason` : la raison lisible fournie par circuit.py::make_circuit_breaker
        (contient déjà la durée/le ratio en toutes lettres, ex. "panne
        franche : 10s sans aucun tick réussi") -- pas de champ durée séparé,
        ça duplirait la même information sous une autre forme.

        `version` TOUJOURS inclus (wardenbot.__version__) : un telemetry.jsonl
        récupéré chez l'utilisateur doit identifier quelle build tournait au
        moment de l'incident.

        EN: Circuit breaker (cf. wardenbot/circuit.py, "professional RPA"
        EN: audit 2026-07-26): one line PER trip, a record type distinct from
        EN: log_decision (`"type": "incident"` -- log_decision never had this
        EN: field, its shape stays unchanged) so kb_engine.py (or a human
        EN: reading telemetry.jsonl) can one day tell the two apart
        EN: unambiguously.

        EN: `reason`: the human-readable reason from
        EN: circuit.py::make_circuit_breaker (already spells out the
        EN: duration/ratio, e.g. "hard failure: 10s with no successful
        EN: tick") -- no separate duration field, that would just duplicate
        EN: the same information in another form.

        EN: `version` ALWAYS included (wardenbot.__version__): a
        EN: telemetry.jsonl retrieved from a user must identify which build
        EN: was running at the time of the incident.
        """
        entry = {
            "type": "incident",
            "timestamp": time.time(),
            "reason": reason,
            "character": character,
            "version": __version__,
            "last_exception": repr(last_exception) if last_exception is not None else None,
        }
        self._queue.put(entry)

    def stop(self, timeout: Optional[float] = 5.0) -> None:
        """Signale l'arrêt et attend que la queue se vide (best-effort, cf.
        timeout) -- pour ne pas perdre les dernières décisions d'une session
        si l'appelant arrête proprement le bot (cf. main.py). Un thread
        daemon serait de toute façon tué au process exit, mais SANS garantie
        d'avoir vidé la queue avant -- stop() explicite évite de perdre les
        toutes dernières lignes sur un arrêt volontaire.

        EN: Signals a stop and waits for the queue to drain (best-effort, cf.
        EN: timeout) -- so as not to lose a session's last decisions if the
        EN: caller cleanly stops the bot (cf. main.py). A daemon thread would
        EN: be killed at process exit anyway, but WITHOUT any guarantee the
        EN: queue was drained first -- an explicit stop() avoids losing the
        EN: very last lines on a voluntary shutdown.
        """
        self._stop_event.set()
        self.join(timeout=timeout)

    def run(self) -> None:
        # BUG ÉVITÉ EN ÉCRIVANT LE TEST (2026-07-14) : `while not
        # _stop_event.is_set(): get(...)` vérifierait le flag AVANT de
        # tenter de dépiler -- si stop() est appelé juste après un
        # log_decision() (cas normal : on arrête le bot après la dernière
        # décision de la session), le item tout juste mis en queue pourrait
        # ne JAMAIS être écrit si ce thread n'a pas encore eu la main. Ordre
        # inversé ICI : on essaie TOUJOURS de dépiler en premier (jusqu'à
        # _QUEUE_POLL_TIMEOUT_S d'attente) ; on ne vérifie l'arrêt QUE quand
        # la queue est confirmée vide (queue.Empty) -- garantit que tout ce
        # qui a été mis en queue avant que stop() ne retourne est bien écrit.
        # EN: BUG AVOIDED WHILE WRITING THE TEST (2026-07-14): `while not
        # EN: _stop_event.is_set(): get(...)` would check the flag BEFORE
        # EN: trying to dequeue -- if stop() is called right after a
        # EN: log_decision() (the normal case: stopping the bot after the
        # EN: session's last decision), the item just queued could NEVER be
        # EN: written if this thread hadn't gotten the CPU yet. Order
        # EN: reversed HERE: we ALWAYS try to dequeue first (up to
        # EN: _QUEUE_POLL_TIMEOUT_S wait); we only check for stop once the
        # EN: queue is confirmed empty (queue.Empty) -- guarantees everything
        # EN: queued before stop() returns is actually written.
        with self.log_file.open("a", encoding="utf-8") as f:
            while True:
                try:
                    entry = self._queue.get(timeout=_QUEUE_POLL_TIMEOUT_S)
                except queue.Empty:
                    if self._stop_event.is_set():
                        break
                    continue
                try:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                except Exception:
                    # ROBUSTESSE (même principe que _run_detection_worker,
                    # state_update.py) : une entrée malformée (type non
                    # JSON-sérialisable ajouté par erreur dans `extra`, par
                    # ex.) ne doit jamais tuer ce thread daemon en silence --
                    # elle est juste perdue, le thread continue au prochain
                    # item. Pas de log_error ICI (diagnostics.py) pour éviter
                    # une dépendance circulaire triviale -- ce module reste
                    # volontairement autonome.
                    # EN: ROBUSTNESS (same principle as _run_detection_worker,
                    # EN: state_update.py): a malformed entry (a non-JSON-
                    # EN: serializable type mistakenly added to `extra`, e.g.)
                    # EN: must never silently kill this daemon thread -- it's
                    # EN: just lost, the thread continues on the next item. No
                    # EN: log_error HERE (diagnostics.py) to avoid a trivial
                    # EN: circular dependency -- this module stays deliberately
                    # EN: self-contained.
                    pass
                finally:
                    self._queue.task_done()
