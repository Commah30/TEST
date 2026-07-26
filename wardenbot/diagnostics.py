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

import ctypes
import subprocess
import time
import traceback
from typing import Optional

from wardenbot.paths import data_path

# data_path(), PAS Path(...) nu (correction 2026-07-26, retour reçu après
# l'audit resource_path()/data_path()) : ces logs de SESSION sont écrits en
# continu pendant que app.py/main.py tournent -- le même raisonnement que
# profiles/ s'applique. Un lancement depuis un CWD qui n'est PAS le dossier
# de l'exe (raccourci avec "Démarrer dans" vide/différent, ou le test de
# packaging lancé depuis C:\Windows\System32 qui a servi à vérifier
# resource_path()) ferait échouer l'écriture ou, pire, la ferait réussir
# silencieusement ailleurs via la virtualisation UAC (VirtualStore) -- des
# logs write qui existent mais que personne ne retrouve. Particulièrement
# critique pour ERROR_LOG_PATH et telemetry.jsonl (cf. wardenbot/telemetry.py) :
# c'est exactement le canal du coupe-circuit (wardenbot/circuit.py::
# log_incident), construit précisément pour qu'un incident laisse une
# trace -- un incident qui échoue à s'écrire silencieusement serait
# l'inverse du contrat de ce mécanisme. En dev (pas de sys.frozen),
# data_path() est un no-op, comportement inchangé.
# EN: data_path(), NOT a bare Path(...) (correction 2026-07-26, feedback
# EN: received after the resource_path()/data_path() audit): these SESSION
# EN: logs are written continuously while app.py/main.py run -- same
# EN: reasoning as profiles/. A launch from a CWD that ISN'T the exe's
# EN: folder (a shortcut with an empty/different "Start in", or the
# EN: packaging test launched from C:\Windows\System32 that was used to
# EN: verify resource_path()) would make the write fail, or worse, silently
# EN: succeed elsewhere via UAC virtualization (VirtualStore) -- logs that
# EN: exist but nobody finds. Especially critical for ERROR_LOG_PATH and
# EN: telemetry.jsonl (cf. wardenbot/telemetry.py): that's exactly the
# EN: circuit breaker's channel (wardenbot/circuit.py::log_incident), built
# EN: precisely so an incident leaves a trace -- an incident that silently
# EN: fails to write would be the opposite of that mechanism's contract. In
# EN: dev (no sys.frozen), data_path() is a no-op, unchanged behavior.

# Cf. CLAUDE.md / commentaires historiques (main.py, 2026-07-12) : au moins un
# blocage total de ~45s observé une fois, jamais reproduit depuis -- ce seuil
# détecte tout appel (capture, matching, YOLO, OCR) largement au-dessus du
# ~15-30ms mesuré en conditions normales.
CAPTURE_WATCHDOG_THRESHOLD_S = 0.2
CAPTURE_WATCHDOG_LOG_PATH = data_path("capture_watchdog.log")
ERROR_LOG_PATH = data_path("error.log")

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


def _torch_vram_snapshot() -> str:
    """VRAM allouée/réservée côté PyTorch spécifiquement (distinct de
    nvidia-smi memory.used, qui inclut TOUT sur le GPU -- jeu compris).
    AJOUTÉ (2026-07-13) suite à l'hypothèse "fuite/fragmentation CUDA
    progressive" -- un stress test isolé de 10 000 appels YOLO n'a montré
    AUCUNE dérive (6.0MB alloué / 33.6MB réservé, stable du 1er au 10000e
    appel), mais ce test tournait sans le jeu ni contention réelle -- cette
    valeur de référence sert à comparer avec un vrai pic en jeu la prochaine
    fois que ça se reproduit."""
    try:
        import torch
        if not torch.cuda.is_available():
            return "n/a"
        alloc = torch.cuda.memory_allocated() / 1e6
        reserved = torch.cuda.memory_reserved() / 1e6
        return f"torch_vram_alloc={alloc:.1f}MB torch_vram_reserved={reserved:.1f}MB"
    except Exception:
        return "n/a"


def _gpu_snapshot() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={_NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
        )
        if out.returncode != 0 or not out.stdout.strip():
            gpu_line = "n/a"
        else:
            util, mem_used, mem_total, clock_sm, power = (v.strip() for v in out.stdout.strip().split(","))
            gpu_line = f"gpu={util}% vram={mem_used}/{mem_total}MiB clock_sm={clock_sm}MHz power={power}W"
    except Exception:
        gpu_line = "n/a"
    return f"{gpu_line} {_torch_vram_snapshot()}"


def log_slow_call(label: str, duration_s: float) -> None:
    gpu = _gpu_snapshot()
    line = (
        f"{time.strftime('%H:%M:%S')} {label} a pris {duration_s:.3f}s "
        f"(seuil {CAPTURE_WATCHDOG_THRESHOLD_S}s) -- gpu: {gpu}\n"
    )
    with CAPTURE_WATCHDOG_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


def log_pipeline_diagnostic(
    label: str,
    total_duration_s: float,
    stages: "dict[str, float]",
    cpu_time_s: Optional[float] = None,
) -> None:
    """Watchdog transformé en outil de diagnostic (2026-07-13) : au lieu
    d'une seule durée opaque ("detect() a pris 6.5s"), décompose le cycle en
    étapes (grab, matches template, lecture de barres, YOLO
    preprocess/inference/postprocess...) -- chaque étape passée en `stages`
    (nom -> durée en ms).

    La somme des étapes est comparée à total_duration_s : si le "temps
    manquant" (total - somme des étapes connues) est grand, le ralentissement
    n'est PAS dans une étape qu'on mesure nous-mêmes -- il est ailleurs
    (scheduling GIL/OS, attente GPU/driver non comptabilisée). C'est la seule
    façon de distinguer "on sait où ça bloque" de "on ne sait PAS encore où
    ça bloque", sans deviner.

    cpu_time_s : delta de time.process_time() mesuré par l'APPELANT autour de
    l'opération lente (avant/après) -- PAS le temps cumulé du process entier.
    Si wall-clock (total_duration_s) >> cpu_time_s, le thread était en
    ATTENTE (bloqué sur un appel système/pilote GPU) pendant l'essentiel du
    temps, pas en train de calculer -- signal direct pour distinguer "calcul
    lourd" de "attente externe" (ex: contention GPU avec le jeu)."""
    known_ms = sum(stages.values())
    total_ms = total_duration_s * 1000
    unaccounted_ms = max(0.0, total_ms - known_ms)

    gpu = _gpu_snapshot()
    stages_str = " ".join(f"{name}={dur:.2f}ms" for name, dur in stages.items())
    now = time.time()
    ts = f"{time.strftime('%H:%M:%S', time.localtime(now))}.{int((now % 1) * 1000):03d}"
    cpu_str = f"{cpu_time_s*1000:.2f}ms" if cpu_time_s is not None else "n/a"

    line = (
        f"{ts} [DIAGNOSTIC] {label} a pris {total_duration_s:.3f}s "
        f"(seuil {CAPTURE_WATCHDOG_THRESHOLD_S}s)\n"
        f"    étapes: {stages_str}\n"
        f"    temps non-comptabilisé (hors étapes connues): {unaccounted_ms:.2f}ms\n"
        f"    cpu_time_consommé_pendant_l_appel={cpu_str} (vs {total_ms:.0f}ms de wall-clock -- "
        f"un grand écart = attente externe, pas du calcul)\n"
        f"    gpu: {gpu}\n"
    )
    with CAPTURE_WATCHDOG_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


# FOCUS_LOG (2026-07-13) : l'utilisateur rapporte des presses "silencieuses" --
# le bot log pressed='X', la touche part bien côté OS (aucune trace
# d'erreur), mais le sort ne se lance pas en jeu, et ce pour N'IMPORTE
# QUELLE ability, pas seulement Hammer Storm (qui a déjà son propre fix de
# confirmation via la cast bar). Hypothèse la plus vérifiable sans matériel
# ni driver (cf. décision de rester sur pydirectinput/SendInput) :
# SendInput livre l'événement clavier à la fenêtre qui a le focus au moment
# T -- si un overlay (Xbox Game Bar utilisé pour enregistrer, Discord...)
# vole le focus ne serait-ce qu'une frame, la presse "réussit" côté Windows
# mais le jeu ne la voit jamais, sans que rien ne puisse le détecter côté
# software.
FOCUS_LOG_PATH = data_path("focus_check.log")


def check_foreground_window(label: str, game_window_title_hint: str) -> None:
    """Journalise (append) si la fenêtre au premier plan à CET instant ne
    contient pas `game_window_title_hint` -- appelé juste avant chaque presse
    (cf. main.py::press_fn). N'écrit RIEN quand le jeu a le focus (cas
    normal) pour ne pas noyer le fichier -- seul un vrai vol de focus laisse
    une trace.

    `game_window_title_hint` : PAS de défaut (2026-07-26, généricité du
    moteur) -- avant ça, ce module (censé être générique) codait en dur
    `GAME_WINDOW_TITLE_HINT = "fellowship"`, un vrai bug latent pour Saryn
    (Warframe) : le titre de fenêtre réel de Warframe ne contient jamais
    "fellowship", donc CHAQUE presse aurait dû logger un faux positif "focus
    volé" -- jamais remarqué car ce log est purement informatif (n'affecte
    aucune décision). L'appelant (main.py, qui connaît le personnage actif
    via game_config.py::CHARACTERS[character]["game_window_title_hint"])
    fournit désormais la bonne valeur pour CHAQUE jeu.

    EN: `game_window_title_hint`: NO default (2026-07-26, engine genericity)
    EN: -- before this, this (supposedly generic) module hardcoded
    EN: `GAME_WINDOW_TITLE_HINT = "fellowship"`, a real latent bug for Saryn
    EN: (Warframe): Warframe's real window title never contains
    EN: "fellowship", so EVERY press should have logged a false-positive
    EN: "focus stolen" -- never noticed because this log is purely
    EN: informational (never affects any decision). The caller (main.py,
    EN: which knows the active character via
    EN: game_config.py::CHARACTERS[character]["game_window_title_hint"])
    EN: now supplies the right value for EACH game.
    """
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
    except Exception as exc:
        title = f"<erreur lecture fenêtre: {exc!r}>"

    if game_window_title_hint.lower() not in title.lower():
        line = f"{time.strftime('%H:%M:%S')} presse {label!r} envoyée alors que la fenêtre au premier plan est {title!r} (pas le jeu)\n"
        with FOCUS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)


# INPUT_TIMING_LOG (2026-07-13) -- instrumentation ciblée sur hammer_storm_proc
# uniquement (cf. wardenbot/input_sim.py::InputWorker.TRACKED_LABELS) : 6/7
# échecs de confirmation observés dans debug.log montrent hammer_storm_cast_pct
# resté à ZÉRO absolu pendant toute la seconde qui suit la presse -- pas une
# tentative partielle interrompue, un canal qui ne démarre jamais. YOLO (le
# buff est détecté en continu, front montant confirmé) et la cast bar
# (échantillonnée sans retard notable) ont déjà été écartés séparément via
# debug.log/capture_watchdog.log -- la frontière suspecte restante est
# decision -> InputWorker.enqueue() -> OS/pydirectinput -> jeu. Ce log
# permet de trancher entre : (A) la queue retarde la frappe (queue_delay_ms
# grand, queue_depth>0) ; (B) la frappe part mais trop brève/chevauchée --
# même famille que le bug Shift+3 déjà rencontré -- (hold_ms anormalement
# court) ; (C) InputWorker est innocent (tout est quasi instantané), auquel
# cas le problème est ailleurs (jeu/serveur), pas dans ce projet.
INPUT_TIMING_LOG_PATH = data_path("input_timing.log")


def log_input_timing(
    label: str,
    enqueue_ts: float,
    dequeue_ts: float,
    keydown_ts: Optional[float],
    keyup_ts: Optional[float],
    queue_depth_at_enqueue: int,
    queue_depth_at_execution: int,
    worker_busy_duration_before_press_s: float,
) -> None:
    def _fmt_ts(t: Optional[float]) -> str:
        if t is None:
            return "n/a"
        return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"

    queue_delay_ms = (dequeue_ts - enqueue_ts) * 1000
    hold_ms = ((keyup_ts - keydown_ts) * 1000) if (keydown_ts is not None and keyup_ts is not None) else None
    line = (
        f"{_fmt_ts(time.time())} [INPUT_TIMING] {label} "
        f"enqueue={_fmt_ts(enqueue_ts)} dequeue={_fmt_ts(dequeue_ts)} queue_delay_ms={queue_delay_ms:.1f} "
        f"queue_depth_enqueue={queue_depth_at_enqueue} queue_depth_execution={queue_depth_at_execution} "
        f"worker_busy_before_ms={worker_busy_duration_before_press_s * 1000:.1f} "
        f"keydown={_fmt_ts(keydown_ts)} keyup={_fmt_ts(keyup_ts)} "
        f"hold_ms={f'{hold_ms:.1f}' if hold_ms is not None else 'n/a'}\n"
    )
    with INPUT_TIMING_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line)


# PRESS_CONFIRM_LOG (2026-07-14) -- confirmation de presse basée sur le
# "voile GCD" (cf. wardenbot/state_update.py, GCD_ICON_REGIONS/OFF_GCD_ICON_REGIONS)
# plutôt que sur YOLO/InputWorker : un accusé de réception visuel du JEU
# lui-même. Complète input_timing.log (qui prouve que la touche est bien
# partie proprement côté InputWorker) sans essayer de fusionner les deux
# dans une seule ligne inter-thread -- corrélation par timestamp+ability
# après coup, comme le reste des logs de ce projet (debug.log/
# focus_check.log/capture_watchdog.log). requested_at/confirmed_at sont des
# timestamps wall-clock (time.time()) même si hooks.py raisonne en interne
# avec time.monotonic() pour la fenêtre de confirmation (cf.
# PRESS_CONFIRM_WINDOW_S, hooks.py) -- uniquement pour que ce log reste
# lisible et recoupable avec les autres fichiers.
PRESS_CONFIRM_LOG_PATH = data_path("press_confirm.log")


def log_press_confirmation(
    ability: str,
    requested_at: float,
    confirmed_at: Optional[float],
    result: str,
) -> None:
    def _fmt_ts(t: Optional[float]) -> str:
        if t is None:
            return "n/a"
        return time.strftime("%H:%M:%S", time.localtime(t)) + f".{int((t % 1) * 1000):03d}"

    line = (
        f"{_fmt_ts(time.time())} [PRESS_CONFIRM] {ability} "
        f"requested_at={_fmt_ts(requested_at)} gcd_detected_at={_fmt_ts(confirmed_at)} result={result}\n"
    )
    with PRESS_CONFIRM_LOG_PATH.open("a", encoding="utf-8") as f:
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
