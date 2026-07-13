"""
dpi_awareness.py — déclare le process DPI-aware AVANT toute capture d'écran/
lecture souris, pour que les coordonnées ne soient jamais faussées par le
scaling Windows (100%/125%/150%...).

PHASE 3 industrialisation (2026-07-14). Sans ceci, Windows MENT aux
applications "DPI-unaware" sur les coordonnées écran (il les recalcule selon
l'échelle active) -- mss (wardenbot/capture.py), ctypes.windll.user32.GetCursorPos
(capture_helper.py) et GetForegroundWindow/GetAsyncKeyState (diagnostics.py,
main.py) verraient alors des coordonnées différentes de la réalité physique
dès que Windows n'est pas à 100%. DOIT être appelé UNE SEULE FOIS, le plus
tôt possible dans le process -- avant tout autre appel user32/mss -- cf.
main.py, en toute première ligne, avant même les autres imports.

Niveaux Windows (du plus complet au plus basique) :
  Per-Monitor V2 (Windows 10 1703+, via user32.SetProcessDpiAwarenessContext)
    -- niveau demandé, gère aussi un changement de moniteur en cours
    d'exécution (multi-écrans à DPI différents).
  Per-Monitor V1 (Windows 8.1+, via shcore.SetProcessDpiAwareness) -- fallback
    si V2 indisponible (Windows plus ancien).
  System DPI Aware (Vista+, via user32.SetProcessDPIAware) -- fallback
    ultime, le plus compatible mais ne réagit pas à un changement de
    moniteur en cours d'exécution.
Cascade volontaire : ROBUSTESSE > COMPATIBILITÉ -- ne jamais planter le bot
entier pour un défaut d'awareness, juste dégrader au niveau obtenu et le
journaliser (cf. Phase 5, diagnostic).

user32/shcore optionnels (injection) : permet de tester toute la cascade de
repli sans dépendre de l'état réel Windows, qui ne peut être fixé qu'UNE
fois par process (un 2e appel réel échouerait silencieusement ou lèverait,
donc pas question de rappeler la vraie API plusieurs fois dans les tests).
"""
from __future__ import annotations

import sys
from typing import Optional

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4  # valeur documentée par Microsoft, passée en HANDLE

PER_MONITOR_V2 = "per_monitor_v2"
PER_MONITOR_V1 = "per_monitor_v1"
SYSTEM_AWARE = "system_aware"
UNAVAILABLE = "unavailable"


_UNSET = object()   # sentinelle distincte de None : None est une valeur EXPLOITABLE
                     # ("shcore indisponible", cf. tests) -- pas juste "non fourni".


def enable_dpi_awareness(user32=_UNSET, shcore=_UNSET) -> str:
    """Tente Per-Monitor V2 -> Per-Monitor V1 -> System Aware, dans cet
    ordre -- retourne le niveau réellement obtenu. Ne lève JAMAIS : un échec
    ici dégrade la précision des coordonnées, ne doit pas empêcher le bot de
    démarrer.

    user32/shcore : laissés à _UNSET par un appelant réel (résolus vers
    ctypes.windll.*) -- un TEST peut passer explicitement None pour simuler
    "cette API n'existe pas sur cette machine", distinct de "l'appelant n'a
    rien précisé"."""
    if sys.platform != "win32":
        return UNAVAILABLE

    if user32 is _UNSET or shcore is _UNSET:
        import ctypes
        if user32 is _UNSET:
            user32 = ctypes.windll.user32
        if shcore is _UNSET:
            try:
                shcore = ctypes.windll.shcore
            except (AttributeError, OSError):
                shcore = None

    try:
        if user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            return PER_MONITOR_V2
    except (AttributeError, OSError):
        pass  # API absente (Windows < 10 1703) ou refusée -- on retente en dessous

    if shcore is not None:
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2 -- retourne un HRESULT, 0 = S_OK
            if shcore.SetProcessDpiAwareness(2) == 0:
                return PER_MONITOR_V1
        except (AttributeError, OSError):
            pass

    try:
        if user32.SetProcessDPIAware():
            return SYSTEM_AWARE
    except (AttributeError, OSError):
        pass

    return UNAVAILABLE


def get_current_dpi_scale_percent(user32=_UNSET, gdi32=_UNSET) -> Optional[int]:
    """Pourcentage de scaling Windows RÉELLEMENT actif (100/125/150...) --
    None si indéterminable. Ajouté pour la Phase 5 (diagnostic) : Phase 3 se
    contentait de DÉCLARER l'awareness, jamais de LIRE le scaling courant --
    nécessaire pour croiser avec l'avertissement du README ("seul 100% est
    officiellement supporté pour l'instant").

    BUG TROUVÉ EN JEU (2026-07-14, rapporté par l'utilisateur) : appelée depuis un
    script isolé (`python -c "from wardenbot.diagnostic_report import
    check_dpi; ..."`, sans jamais passer par main.py qui déclare l'awareness
    en tout premier), cette fonction retournait TOUJOURS 100% -- vérifié en
    vrai à 125% ET 150% Windows. Cause : Windows MENT sur
    GetDeviceCaps(LOGPIXELSX) tant que le PROCESS APPELANT lui-même n'est
    pas déclaré DPI-aware (comportement de compatibilité historique : un
    process non-aware est toujours vu à 96 DPI/100%, peu importe le
    scaling réel -- Windows fait l'upscaling en coulisses). Le docstring
    d'origine SUPPOSAIT que l'appelant avait déjà déclaré l'awareness --
    fragile, dépendait de la discipline de l'appelant. Fix : cette fonction
    se charge maintenant ELLE-MÊME de déclarer l'awareness avant de lire --
    idempotent (sans effet néfaste si main.py l'a déjà fait), donc fiable
    peu importe qui l'appelle et quand."""
    if sys.platform != "win32":
        return None
    if user32 is _UNSET or gdi32 is _UNSET:
        import ctypes
        if user32 is _UNSET:
            user32 = ctypes.windll.user32
        if gdi32 is _UNSET:
            try:
                gdi32 = ctypes.windll.gdi32
            except (AttributeError, OSError):
                gdi32 = None
    if gdi32 is None:
        return None

    enable_dpi_awareness(user32=user32)

    LOGPIXELSX = 88
    try:
        hdc = user32.GetDC(0)
        if not hdc:
            return None
        try:
            dpi_x = gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
        finally:
            user32.ReleaseDC(0, hdc)
        if not dpi_x:
            return None
        return round(dpi_x / 96 * 100)
    except (AttributeError, OSError):
        return None
