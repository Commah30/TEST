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


def log_slow_call(label: str, duration_s: float) -> None:
    line = f"{time.strftime('%H:%M:%S')} {label} a pris {duration_s:.3f}s (seuil {CAPTURE_WATCHDOG_THRESHOLD_S}s)\n"
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
