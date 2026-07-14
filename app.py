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

import ctypes
import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

import webview  # pip install pywebview

from main import DEBUG_LOG_PATH, build_engine, make_debug_logger, make_error_handler
from wardenbot.config_manager import DEFAULT_PROFILE
from wardenbot.engine import RotationEngine
from wardenbot.game_config import CHARACTERS

HERE = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(HERE, "ui.html")

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
            self._engine = build_engine(self.character, dry_run=False, profile=None)
            self._engine_character = self.character
            self._log_tick = make_debug_logger(self._engine.state)
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
        self._pause_hotkey_down_last = down

    def _make_on_error(self):
        base_on_error = make_error_handler()

        def on_error(exc: BaseException) -> None:
            base_on_error(exc)  # error.log, cf. diagnostics.py::log_error
            self.state["last_error"] = repr(exc)

        return on_error

    def _on_tick(self, pressed: Optional[str]) -> None:
        # Appelé à chaque itération de run_forever(), MÊME en pause (cf.
        # engine.py) -- c'est justement ce qui permet à la touche C de
        # détecter une reprise pendant qu'on est en pause.
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
        # game_config.py::CHARACTERS) -- l'ancien engine (et ses threads de
        # détection/input, cf. limitation connue) reste en veille, un nouveau
        # est reconstruit paresseusement au prochain start().
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
