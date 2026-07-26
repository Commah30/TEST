"""
environment_detect.py — détection d'environnement (résolution, écrans,
fenêtre du jeu) pour l'assistant de calibration (Phase 4a de
l'industrialisation, 2026-07-14).

Volontairement séparé de toute UI : pure logique, testable sans overlay
graphique -- l'overlay (Phase 4c) et le wizard (Phase 4d) s'appuieront
dessus, pas l'inverse.

Suppose que enable_dpi_awareness() (wardenbot/dpi_awareness.py) a déjà été
appelée avant (cf. main.py, tout en haut) -- sinon Windows mentirait sur les
valeurs de résolution/moniteurs retournées ici, comme pour tout le reste du
projet qui lit l'écran.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Optional


@dataclass
class MonitorInfo:
    left: int
    top: int
    width: int
    height: int
    is_primary: bool


def get_primary_resolution() -> tuple[int, int]:
    """(largeur, hauteur) du moniteur PRINCIPAL, en pixels physiques."""
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)   # SM_CXSCREEN
    height = user32.GetSystemMetrics(1)  # SM_CYSCREEN
    return width, height


def list_monitors() -> list[MonitorInfo]:
    """Un MonitorInfo par écran connecté (EnumDisplayMonitors) -- une machine
    multi-écrans (cf. Phase 10 de la checklist) doit tous les voir, pas
    seulement le principal."""

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    monitors: list[MonitorInfo] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_RECT), ctypes.c_double
    )

    def _callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        r = lprcMonitor.contents
        monitors.append(
            MonitorInfo(
                left=r.left,
                top=r.top,
                width=r.right - r.left,
                height=r.bottom - r.top,
                is_primary=(r.left == 0 and r.top == 0),
            )
        )
        return 1  # continue l'énumération

    callback = MonitorEnumProc(_callback)
    ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback, 0)
    return monitors


def find_window_by_title_hint(hint: str) -> Optional[str]:
    """Cherche une fenêtre visible dont le titre contient `hint` (insensible
    à la casse) -- retourne le titre exact trouvé, ou None si le jeu ne
    semble pas ouvert.

    `hint` : PAS de défaut (2026-07-26, généricité du moteur) -- défautait
    avant sur GAME_WINDOW_TITLE_HINT (diagnostics.py, "fellowship" codé en
    dur), un bug latent pour Saryn/Warframe (cf. diagnostics.py::
    check_foreground_window pour le détail). L'appelant fournit désormais
    la valeur pour le jeu actif (cf. game_config.py::CHARACTERS[character]
    ["game_window_title_hint"]).

    EN: `hint`: NO default (2026-07-26, engine genericity) -- used to
    EN: default to GAME_WINDOW_TITLE_HINT (diagnostics.py, hardcoded
    EN: "fellowship"), a latent bug for Saryn/Warframe (cf.
    EN: diagnostics.py::check_foreground_window for details). The caller now
    EN: supplies the value for the active game (cf.
    EN: game_config.py::CHARACTERS[character]["game_window_title_hint"]).
    """
    found: list[str] = []
    hint_lower = hint.lower()

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def _callback(hwnd, lParam):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return 1
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return 1
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        if hint_lower in buf.value.lower():
            found.append(buf.value)
            return 0  # trouvé -- arrête l'énumération
        return 1

    callback = EnumWindowsProc(_callback)
    ctypes.windll.user32.EnumWindows(callback, 0)
    return found[0] if found else None
