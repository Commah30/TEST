"""
diagnostics.py — infra partagée pour journaliser ce qui sort de l'ordinaire
pendant que le bot tourne : appels anormalement lents (watchdog) et
exceptions (erreurs) qui seraient sinon silencieuses.

Extrait de main.py pour être réutilisable à la fois par le thread de
détection (wardenbot/state_update.py) et par la boucle principale
(wardenbot/engine.py, via RotationEngine.run_forever(on_error=...)).

Fichiers séparés de debug.log : debug.log dépend du thread principal et du
dedup (pas de ligne si rien n'a changé) -- ces deux-là doivent rester
indépendants de ça pour ne jamais rater un événement anormal.
"""
from __future__ import annotations

import subprocess
import time
import traceback
from pathlib import Path

# Cf. CLAUDE.md / commentaires historiques (main.py, 2026-07-12) : au moins un
# blocage total de ~45s observé une fois, jamais reproduit depuis -- ce seuil
# détecte tout appel (capture, matching, YOLO, OCR) largement au-dessus du
# ~15-30ms mesuré en conditions normales.
CAPTURE_WATCHDOG_THRESHOLD_S = 0.2
CAPTURE_WATCHDOG_LOG_PATH = Path("capture_watchdog.log")
ERROR_LOG_PATH = Path("error.log")

# Snapshot GPU au moment du pic (2026-07-12, suite à la question "OCR/YOLO
# peut-il faire chuter le FPS ?") : les pics ponctuels de plusieurs secondes
# observés dans capture_watchdog.log (ex: yolo_detect.detect() à 6.5s) sont
# compatibles avec une contention GPU (le jeu et torch/CUDA se disputent le
# même GPU), mais "compatible avec" n'est pas une preuve -- nvidia-smi au
# moment précis du pic donne une preuve directe (usage %, VRAM, clocks,
# puissance) au lieu d'une simple corrélation temporelle. Best-effort : si
# nvidia-smi est absent/échoue/timeout, on journalise juste "n/a" plutôt que
# de faire planter le call qui a déjà déclenché le watchdog.
_NVIDIA_SMI_QUERY = "utilization.gpu,memory.used,memory.total,clocks.sm,power.draw"


def _gpu_snapshot() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return "n/a"
        util, mem_used, mem_total, clock_sm, power = (v.strip() for v in out.stdout.strip().split(","))
        return f"gpu={util}% vram={mem_used}/{mem_total}MiB clock_sm={clock_sm}MHz power={power}W"
    except Exception:
        return "n/a"


def log_slow_call(label: str, duration_s: float) -> None:
    gpu = _gpu_snapshot()
    line = (
        f"{time.strftime('%H:%M:%S')} {label} a pris {duration_s:.3f}s "
        f"(seuil {CAPTURE_WATCHDOG_THRESHOLD_S}s) -- gpu: {gpu}\n"
    )
    with CAPTURE_WATCHDOG_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def log_error(label: str, exc: BaseException) -> None:
    """Journalise une exception SANS jamais lever -- appelé depuis un thread
    daemon ou la boucle principale, là où laisser l'exception remonter
    tuerait silencieusement le thread (détection) ou tout le bot (moteur)
    au lieu de juste rater ce cycle/tick. cf. RotationEngine.run_forever et
    wardenbot/state_update.py::_run_detection_worker."""
    line = (
        f"{time.strftime('%H:%M:%S')} {label} : {exc!r}\n"
        f"{traceback.format_exc()}\n"
    )
    with ERROR_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)
    print(f"[diagnostics] {label} a levé {exc!r} -- cf. {ERROR_LOG_PATH} (on continue)")
