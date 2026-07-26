"""
app.py — Application desktop wardenbot (PyWebView).

Fenêtre unique qui contient le dashboard ET pilote le bot dans le même
process, en réutilisant tel quel le wiring de main.py (build_engine,
make_debug_logger, make_error_handler) -- aucune logique de rotation/
détection/input n'est réimplémentée ici, seulement le pont API<->UI.

    python app.py

L'interface (ui.html) appelle les méthodes de `Api` via
window.pywebview.api.*
"""

from __future__ import annotations

import sys
import threading
import time
import traceback

from wardenbot.paths import data_path, resource_path

# UTF-8 stdout/stderr (2026-07-26, retour reçu) : DUPLIQUÉ ici plutôt que de
# compter sur l'effet de bord du `from main import ...` plus bas (main.py
# fait le même fix à SON niveau module, cf. main.py) -- si un jour app.py
# cesse d'importer main.py, ou si l'ordre d'imports change, cette protection
# ne doit pas disparaître silencieusement sans qu'aucun test ne le signale.
# Défensif sur `stream is not None` (contrairement à l'original de main.py,
# qui suppose une vraie console `console=True`) : en `console=False` (cf.
# wardenbot.spec), sys.stdout/sys.stderr peuvent être None ou un flux sans
# `.encoding`. Nécessaire pour survivre à la barre de progression Unicode
# qu'EasyOCR imprime au tout premier téléchargement de ses modèles (cf.
# wardenbot/ocr.py, même bug que documenté dans main.py).
# EN: UTF-8 stdout/stderr (2026-07-26, feedback received): DUPLICATED here
# EN: rather than relying on the side effect of the `from main import ...`
# EN: below (main.py does the same fix at ITS module level, cf. main.py) --
# EN: if app.py ever stops importing main.py, or the import order changes,
# EN: this protection must not silently disappear without any test flagging
# EN: it. Defensive on `stream is not None` (unlike main.py's original,
# EN: which assumes a real console, `console=True`): in `console=False`
# EN: (cf. wardenbot.spec), sys.stdout/sys.stderr can be None or a stream
# EN: without `.encoding`. Needed to survive the Unicode progress bar
# EN: EasyOCR prints on its very first model download (cf. wardenbot/ocr.py,
# EN: same bug documented in main.py).
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and getattr(_stream, "encoding", None) and _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8")

# CRASH LOGGER (2026-07-26, audit packaging) : installé AVANT tout autre
# import (webview, main, ...) pour capturer même un échec d'IMPORT, pas
# seulement une exception en cours de run. En `console=False` (build
# packagé, cf. wardenbot.spec), une exception non rattrapée n'a plus aucune
# console où s'afficher -- PyInstaller affiche une fenêtre "Unhandled
# exception in script" qui se ferme sans laisser de trace exploitable une
# fois fermée. crash.log, à côté de l'exécutable (data_path -- PAS
# resource_path/_internal, PAS le CWD non plus, souvent arbitraire pour un
# .exe packagé lancé par raccourci/double-clic), est le pendant packaging de
# telemetry.jsonl::log_incident : celui-ci couvre les erreurs DE TICK
# rattrapées par RotationEngine.run_forever ; crash.log couvre tout le
# reste (import, construction UI, thread de rotation) -- "le jour où un
# utilisateur dit que ça a planté, ce fichier est la seule télémétrie
# disponible."
# EN: CRASH LOGGER (2026-07-26, packaging audit): installed BEFORE any
# EN: other import (webview, main, ...) to capture even an IMPORT failure,
# EN: not just a runtime exception. In `console=False` (packaged build, cf.
# EN: wardenbot.spec), an uncaught exception no longer has any console to
# EN: print to -- PyInstaller shows an "Unhandled exception in script"
# EN: window that leaves no usable trace once closed. crash.log, next to
# EN: the executable (data_path -- NOT resource_path/_internal, NOT the CWD
# EN: either, often arbitrary for a packaged .exe launched via
# EN: shortcut/double-click), is the packaging counterpart to
# EN: telemetry.jsonl::log_incident: that one covers TICK errors caught by
# EN: RotationEngine.run_forever; crash.log covers everything else (import,
# EN: UI construction, rotation thread) -- "the day a user says it crashed,
# EN: this file is the only telemetry available."
CRASH_LOG_PATH = data_path("crash.log")


def _write_crash(exc_type, exc_value, exc_tb, source: str) -> None:
    try:
        with CRASH_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ({source}) ===\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception:
        pass  # crash.log ne doit jamais lui-même faire planter le process


def _on_main_thread_exception(exc_type, exc_value, exc_tb) -> None:
    _write_crash(exc_type, exc_value, exc_tb, "thread principal")
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _on_worker_thread_exception(args) -> None:
    # threading.excepthook (Python 3.8+) : couvre le thread de rotation
    # (self._thread, cf. BotController.start()) -- sys.excepthook seul ne
    # voit QUE le thread principal.
    # EN: threading.excepthook (Python 3.8+): covers the rotation thread
    # EN: (self._thread, cf. BotController.start()) -- sys.excepthook alone
    # EN: only sees the main thread.
    name = args.thread.name if args.thread is not None else "?"
    _write_crash(args.exc_type, args.exc_value, args.exc_traceback, f"thread '{name}'")


sys.excepthook = _on_main_thread_exception
threading.excepthook = _on_worker_thread_exception

import ctypes
from collections import deque
from datetime import datetime
from typing import Optional

import webview  # pip install pywebview

from main import DEBUG_LOG_PATH, build_engine, make_circuit_wiring, make_debug_logger, make_error_handler
from wardenbot import __version__
from wardenbot.config_manager import DEFAULT_PROFILE
from wardenbot.engine import RotationEngine
from wardenbot.game_config import CHARACTERS

# resource_path() (2026-07-26, packaging PyInstaller onedir) plutôt que
# l'ancien os.path.dirname(os.path.abspath(__file__)) : DANS un bundle
# PyInstaller, __file__ d'un module gelé pointe dans le dossier interne de
# PyInstaller, pas forcément là où `datas=` place ui.html à côté de
# l'exécutable -- cf. wardenbot/paths.py docstring. str() : pywebview
# attend une URL/chemin string, pas un Path.
UI = str(resource_path("ui.html"))

# ui.html affiche un bandeau statique "TARIQ_ROTATION SCHISM_V2" (texte HTML
# brut, pas templaté) -- Tariq reste donc le personnage par défaut le plus
# cohérent avec l'UI telle qu'elle est aujourd'hui.
DEFAULT_CHARACTER = "tariq"

# Touche C = même hotkey pause/reprise que main.py (cf. main.py::make_pause_toggle)
# -- rebranchée ICI directement sur self.status plutôt qu'en réutilisant l'état
# interne de make_pause_toggle, pour ne jamais avoir deux sources de vérité sur
# "en pause ou pas" (le bouton GUI et la touche C doivent piloter EXACTEMENT le
# même flag, jamais deux booléens qui pourraient diverger).
VK_C = 0x43


class BotController:
    """
    Pilote un vrai RotationEngine (cf. wardenbot/engine.py) dans un thread
    séparé. "profile" ici désigne le PERSONNAGE/rotation (meiko|tariq, cf.
    CHARACTERS) -- pas le profil d'affichage ConfigManager (résolution/DPI,
    resté fixé à "Default", cf. calibrate() plus bas), pour matcher ce que
    Api.set_profile est censé faire ("charge config/<name>.yaml").
    """

    def __init__(self):
        self.character = DEFAULT_CHARACTER
        self.profiles = list(CHARACTERS.keys())  # ["meiko", "tariq"] -- pas de noms inventés
        self.profile = self.character
        self.status = "stopped"  # stopped | running | paused

        self._engine: Optional[RotationEngine] = None
        self._engine_character: Optional[str] = None
        self._log_tick = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._recent = deque(maxlen=14)
        self._last_pressed = None
        self._pause_hotkey_down_last = False
        self._tick_had_error = False

        # Coupe-circuit (cf. wardenbot/circuit.py, main.py::make_circuit_wiring)
        # -- reconstruit dans _ensure_engine() en même temps que le moteur
        # (même cycle de vie que self._log_tick juste au-dessus), pour que le
        # nom de personnage utilisé par le bip/log d'incident reste exact
        # après un set_profile(), et pour qu'un changement de personnage
        # reparte d'un coupe-circuit vierge plutôt que d'hériter du streak/
        # historique du personnage précédent.
        # EN: Circuit breaker (cf. wardenbot/circuit.py,
        # EN: main.py::make_circuit_wiring) -- rebuilt in _ensure_engine()
        # EN: alongside the engine (same lifecycle as self._log_tick right
        # EN: above), so the character name used by the beep/incident log
        # EN: stays accurate after a set_profile(), and so a character switch
        # EN: starts from a clean breaker rather than inheriting the previous
        # EN: character's streak/history.
        self._circuit_record_error = None
        self._circuit_record_success = None
        self._circuit_is_tripped = None
        self._circuit_reset = None

        # État live exposé au dashboard (mêmes clés que debug.log)
        self.state = {
            "resources": {"fury": 0.0, "hammer_storm_cast_pct": 0.0},
            "locks": {"gcd_remaining_s": 0.0, "hard_lock_remaining_s": 0.0},
            "buffs": {"hammer_storm_empowered": False,
                      "skull_crusher_empowered": False,
                      "focused_wrath_empowered": False},
            "last_cast": None, "pressed": None,
            "fps": 0, "confidence": 0.0, "last_error": None,
        }

    # -- construction paresseuse du moteur -----------------------------
    def _ensure_engine(self) -> RotationEngine:
        if self._engine is None or self._engine_character != self.character:
            # dry_run=False -- exactement comme main.py::__main__ : capture
            # d'écran réelle et vraies touches pressées dès que start()
            # lance la boucle. profile=None -> ConfigManager profil
            # "Default" (cf. build_engine, main.py).
            # enable_telemetry=True (2026-07-26, retour reçu) -- avant ça,
            # app.py ne l'activait jamais (contrairement à main.py::__main__),
            # rendant make_circuit_wiring(..., self._engine.telemetry) ci-
            # dessous silencieusement inopérant pour log_incident : le
            # coupe-circuit bipait/pausait très bien depuis le dashboard GUI,
            # mais n'écrivait jamais dans telemetry.jsonl -- l'inverse du
            # contrat de ce mécanisme. Même défaut que main.py::__main__
            # désormais : actif par défaut, écrit sur data_path()
            # ("telemetry.jsonl", cf. wardenbot/telemetry.py), jamais bloquant
            # (TelemetryWorker, thread séparé).
            # EN: enable_telemetry=True (2026-07-26, feedback received) --
            # EN: before this, app.py never enabled it (unlike
            # EN: main.py::__main__), silently making
            # EN: make_circuit_wiring(..., self._engine.telemetry) below inert
            # EN: for log_incident: the circuit breaker beeped/paused just
            # EN: fine from the GUI dashboard, but never wrote to
            # EN: telemetry.jsonl -- the opposite of that mechanism's
            # EN: contract. Same default as main.py::__main__ now: on by
            # EN: default, writes to data_path() ("telemetry.jsonl", cf.
            # EN: wardenbot/telemetry.py), never blocking (TelemetryWorker,
            # EN: separate thread).
            self._engine = build_engine(self.character, dry_run=False, profile=None, enable_telemetry=True)
            self._engine_character = self.character
            self._log_tick = make_debug_logger(self._engine.state)
            (self._circuit_record_error, self._circuit_record_success,
             self._circuit_is_tripped, self._circuit_reset) = make_circuit_wiring(
                self.character, self._engine.telemetry
            )
        return self._engine

    def _is_paused(self) -> bool:
        return self.status == "paused"

    def _check_pause_hotkey(self) -> None:
        # cf. VK_C plus haut -- même touche que main.py, un seul état piloté
        # (self.status), que ce soit le bouton GUI ou le clavier qui bascule.
        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_C) & 0x8000)
        if down and not self._pause_hotkey_down_last:
            if self.status == "running":
                self.status = "paused"
                self._log_pause_transition(True)
            elif self.status == "paused":
                self.status = "running"
                self._log_pause_transition(False)
                self._reset_circuit_if_any()
        self._pause_hotkey_down_last = down

    def _reset_circuit_if_any(self) -> None:
        # Reprise (touche C OU bouton Start, cf. start()) -> reset() du
        # coupe-circuit (cf. wardenbot/circuit.py) : si la panne persiste,
        # un nouveau streak/ratio re-déclenchera naturellement -- COMPORTEMENT
        # ATTENDU, pas un bug (cf. main.py::make_circuit_wiring docstring).
        # None tant que _ensure_engine() n'a encore jamais tourné (avant le
        # tout 1er start()).
        # EN: Resume (C key OR Start button, cf. start()) -> reset() the
        # EN: circuit breaker (cf. wardenbot/circuit.py): if the fault
        # EN: persists, a new streak/ratio will naturally re-trip --
        # EN: INTENDED behavior, not a bug (cf.
        # EN: main.py::make_circuit_wiring docstring). None until
        # EN: _ensure_engine() has run at least once (before the very first
        # EN: start()).
        if self._circuit_reset is not None:
            self._circuit_reset()

    def _make_on_error(self):
        base_on_error = make_error_handler()

        def on_error(exc: BaseException) -> None:
            base_on_error(exc)  # error.log, cf. diagnostics.py::log_error
            self.state["last_error"] = repr(exc)
            self._tick_had_error = True
            self._circuit_record_error(time.monotonic(), exc)
            self._sync_circuit_trip()

        return on_error

    def _sync_circuit_trip(self) -> None:
        # self.status reste l'UNIQUE source de vérité pour le dashboard (cf.
        # commentaire VK_C plus haut, "jamais deux booléens qui pourraient
        # diverger") -- donc un déclenchement du coupe-circuit doit
        # directement piloter self.status = "paused", pas un 2e flag composé
        # séparément (contrairement à main.py, qui n'a pas d'enum self.status
        # unifié et compose deux prédicats booléens par OR).
        # EN: self.status stays the ONLY source of truth for the dashboard
        # EN: (cf. the VK_C comment above, "never two booleans that could
        # EN: diverge") -- so a circuit-breaker trip must directly drive
        # EN: self.status = "paused", not a 2nd separately-composed flag
        # EN: (unlike main.py, which has no unified self.status enum and ORs
        # EN: two boolean predicates instead).
        if self._circuit_is_tripped() and self.status == "running":
            self.status = "paused"
            self._log_pause_transition(True)

    def _on_tick(self, pressed: Optional[str]) -> None:
        # Appelé à chaque itération de run_forever(), MÊME en pause (cf.
        # engine.py) -- c'est justement ce qui permet à la touche C de
        # détecter une reprise pendant qu'on est en pause.
        had_error = self._tick_had_error
        self._tick_had_error = False
        if self.status == "running" and not had_error and self._circuit_record_success is not None:
            self._circuit_record_success(time.monotonic())
        self._check_pause_hotkey()
        self._log_tick(pressed)  # debug.log, même format/dédup que main.py
        self._push(pressed)
        self._sync_state(pressed)

    # -- cycle de vie ----------------------------------------------------
    def _log_pause_transition(self, paused: bool) -> None:
        # Marqueur visible DANS debug.log au moment exact d'un toggle
        # pause/reprise -- même principe que main.py::_log_pause_event.
        # Sans ça, un long silence dans le log (cf. dedup de
        # make_debug_logger) est indissociable d'un vrai blocage : ce
        # marqueur permet de trancher direct à la lecture.
        marker = "=== PAUSE ACTIVÉE ===" if paused else "=== PAUSE DÉSACTIVÉE -- reprise ==="
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {marker}\n")

    def start(self) -> None:
        if self.status == "running":
            return
        was_paused = self.status == "paused"
        self.status = "running"
        if was_paused:
            self._log_pause_transition(False)
        # Reset INCONDITIONNEL (pas seulement si was_paused) : un Stop manuel
        # après un déclenchement du coupe-circuit laisserait sinon
        # self._circuit_reset jamais rappelé (was_paused=False depuis
        # "stopped") -- le coupe-circuit resterait tripped[0]=True pour
        # toujours (cf. wardenbot/circuit.py : record_error/record_success
        # sont des no-op tant que tripped), silencieusement inopérant pour le
        # reste de la session après un Stop/Start. reset() est idempotent
        # (no-op) sur un coupe-circuit déjà vierge -- gratuit à chaque start().
        # EN: UNCONDITIONAL reset (not only if was_paused): a manual Stop
        # EN: after a circuit-breaker trip would otherwise never call
        # EN: self._circuit_reset again (was_paused=False from "stopped") --
        # EN: the breaker would stay tripped[0]=True forever (cf.
        # EN: wardenbot/circuit.py: record_error/record_success are no-ops
        # EN: while tripped), silently inert for the rest of the session
        # EN: after a Stop/Start. reset() is idempotent (no-op) on an
        # EN: already-clean breaker -- free on every start().
        self._reset_circuit_if_any()
        if not self._thread or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="rotation-engine")
            self._thread.start()

    def _run(self) -> None:
        # _ensure_engine() (donc build_engine(), donc ScreenCapture()) DOIT
        # tourner ICI, sur CE thread -- pas sur celui qui a appelé start()
        # (le pont JS de pywebview). Bug trouvé en jeu (2026-07-15) :
        # mss.mss() n'est valide que sur le thread qui l'a créé, sinon
        # ScreenShotError('... BitBlt') dès le premier is_ready_fn/grab()
        # appelé depuis tick() sur ce thread-ci. cf. wardenbot/capture.py.
        try:
            engine = self._ensure_engine()
        except Exception as exc:
            self.state["last_error"] = repr(exc)
            self.status = "stopped"
            return
        tick_hz = CHARACTERS[self.character].get("tick_hz", 30.0)
        engine.run_forever(
            tick_hz=tick_hz,
            on_tick=self._on_tick,
            is_paused=self._is_paused,
            on_error=self._make_on_error(),
            stop_event=self._stop,
        )

    def pause(self) -> None:
        if self.status == "running":
            self.status = "paused"
            self._log_pause_transition(True)

    def stop(self) -> None:
        self.status = "stopped"
        self._stop.set()  # run_forever() sort proprement (cf. engine.py)

    def set_profile(self, name: str) -> None:
        if name not in CHARACTERS or name == self.character:
            return
        self.stop()
        # Changer de personnage = un tout autre YAML/hooks/regions (cf.
        # game_config.py::CHARACTERS) -- FIX (audit architecture, 2026-07-18,
        # fuite confirmée) : self._engine.shutdown() arrête maintenant
        # réellement l'InputWorker ET le thread de détection de l'ANCIEN
        # engine (cf. main.py::build_engine, shutdown_fn) avant de l'abandonner
        # -- avant ce fix, les deux restaient actifs indéfiniment en arrière-
        # plan après chaque changement de personnage (capture d'écran/
        # inférence YOLO tournant pour rien, jamais arrêtés avant la fermeture
        # du process). self.stop() ci-dessus ne fait QUE sortir de
        # run_forever() (cf. engine.py) -- volontairement séparé de shutdown()
        # : un simple Stop/Start SANS changement de personnage doit pouvoir
        # réutiliser le même engine (et ses workers déjà démarrés), pas les
        # tuer à chaque pause.
        # EN: Switching character = a whole different YAML/hooks/regions (cf.
        # EN: game_config.py::CHARACTERS) -- FIX (architecture audit,
        # EN: 2026-07-18, confirmed leak): self._engine.shutdown() now
        # EN: actually stops the OLD engine's InputWorker AND detection thread
        # EN: (cf. main.py::build_engine, shutdown_fn) before dropping it --
        # EN: before this fix, both stayed active indefinitely in the
        # EN: background after every character switch (screen capture/YOLO
        # EN: inference running for nothing, never stopped before the process
        # EN: itself exited). self.stop() above ONLY exits run_forever() (cf.
        # EN: engine.py) -- deliberately kept separate from shutdown(): a
        # EN: plain Stop/Start WITHOUT a character switch must be able to
        # EN: reuse the same engine (and its already-started workers), not
        # EN: kill them on every pause.
        if self._engine is not None:
            self._engine.shutdown()
        self.character = name
        self.profile = name
        self._engine = None

    # -- état / logs -------------------------------------------------------
    def _push(self, pressed: Optional[str]) -> None:
        if pressed and pressed != self._last_pressed:
            self._recent.appendleft({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "action": pressed,
                "kind": "proc" if "proc" in pressed else "cast",
            })
        self._last_pressed = pressed

    def _sync_state(self, pressed: Optional[str]) -> None:
        engine_state = self._engine.state
        now = time.monotonic()
        s = self.state
        s["resources"]["fury"] = engine_state.resources.get("fury", 0.0)
        s["resources"]["hammer_storm_cast_pct"] = engine_state.resources.get("hammer_storm_cast_pct", 0.0)
        s["locks"]["gcd_remaining_s"] = max(0.0, engine_state.gcd_until - now)
        s["locks"]["hard_lock_remaining_s"] = max(0.0, engine_state.hard_lock_until - now)
        s["buffs"]["hammer_storm_empowered"] = engine_state.buffs.get("hammer_storm_empowered", False)
        s["buffs"]["skull_crusher_empowered"] = engine_state.buffs.get("skull_crusher_empowered", False)
        s["buffs"]["focused_wrath_empowered"] = engine_state.buffs.get("focused_wrath_empowered", False)
        s["last_cast"] = engine_state.last_cast
        s["pressed"] = pressed
        s["fps"] = CHARACTERS[self.character].get("tick_hz", 30.0)
        # Pas de signal de confiance agrégé dans engine.py aujourd'hui (matcher
        # confidence est transitoire, par ability, jamais stocké sur CombatState) --
        # laissé à 0.0 plutôt qu'une valeur inventée.
        s["confidence"] = 0.0

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "profile": self.profile,
            "profiles": self.profiles,
            "version": __version__,
            **self.state,
            "recent_casts": list(self._recent),
        }


class Api:
    """Méthodes appelées depuis le JS via window.pywebview.api.<method>()"""

    def __init__(self, bot: BotController):
        self.bot = bot

    # contrôles
    def start(self): self.bot.start(); return {"ok": True, "status": self.bot.status}
    def pause(self): self.bot.pause(); return {"ok": True, "status": self.bot.status}
    def stop(self): self.bot.stop(); return {"ok": True, "status": self.bot.status}

    def set_profile(self, name):
        self.bot.set_profile(name)
        return {"ok": True, "profile": self.bot.profile}

    # polling état (le dashboard appelle ça ~4x/s)
    def get_state(self):
        return self.bot.snapshot()

    # logs (onglet Logs) -- vrai debug.log (main.py::make_debug_logger),
    # pas la liste recent_casts en mémoire.
    def get_logs(self, n=200):
        if not DEBUG_LOG_PATH.exists():
            return {"lines": []}
        lines = DEBUG_LOG_PATH.read_text(encoding="utf-8").splitlines()
        return {"lines": lines[-n:]}

    # calibration (onglet Calibration) -- assistant existant (interactif,
    # bloquant, souris) plutôt qu'une logique de calibration réimplémentée ici.
    def calibrate(self):
        from wardenbot.calibration_wizard import run_calibration_wizard
        try:
            saved_path = run_calibration_wizard(self.bot.character, DEFAULT_PROFILE)
        except Exception as exc:
            return {"ok": False, "msg": f"Calibration échouée : {exc!r}"}
        if saved_path is None:
            return {"ok": False, "msg": "Calibration annulée."}
        return {"ok": True, "msg": f"Profil sauvegardé : {saved_path}"}


def main():
    bot = BotController()
    api = Api(bot)
    webview.create_window(
        "WARDENBOT // MISSION CONTROL",
        UI,
        js_api=api,
        width=1280, height=820,
        min_size=(1024, 700),
        background_color="#060A12",
        text_select=False,
    )
    webview.start()


if __name__ == "__main__":
    main()
