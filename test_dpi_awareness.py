"""
test_dpi_awareness.py — prouve que la cascade Per-Monitor V2 -> V1 -> System
Aware -> indisponible se comporte correctement, SANS dépendre de l'état réel
Windows (qui ne peut être fixé qu'une fois par process -- impossible de
rappeler la vraie API plusieurs fois dans une suite de tests). user32/shcore
sont injectés comme de faux objets pour chaque scénario.

Un seul test (test_real_windows_call_achieves_at_least_system_aware) touche
la vraie API Windows, en dernier -- vérifie juste qu'on obtient au moins le
niveau le plus bas sur CETTE machine, sans präsupposer V2 disponible partout.

Lance avec : python test_dpi_awareness.py
"""
from __future__ import annotations

from wardenbot.dpi_awareness import (
    PER_MONITOR_V1,
    PER_MONITOR_V2,
    SYSTEM_AWARE,
    UNAVAILABLE,
    enable_dpi_awareness,
    get_current_dpi_scale_percent,
)


class _User32AllSucceed:
    def SetProcessDpiAwarenessContext(self, ctx):
        return 1

    def SetProcessDPIAware(self):
        return 1


class _User32V2FailsRestSucceed:
    def SetProcessDpiAwarenessContext(self, ctx):
        raise AttributeError("SetProcessDpiAwarenessContext absente (Windows < 10 1703)")

    def SetProcessDPIAware(self):
        return 1


class _User32AllFail:
    def SetProcessDpiAwarenessContext(self, ctx):
        raise AttributeError("absente")

    def SetProcessDPIAware(self):
        raise OSError("refusée")


class _ShcoreSucceeds:
    def SetProcessDpiAwareness(self, level):
        return 0  # S_OK


class _ShcoreFails:
    def SetProcessDpiAwareness(self, level):
        raise OSError("refusée")


def test_per_monitor_v2_used_when_available():
    level = enable_dpi_awareness(user32=_User32AllSucceed(), shcore=_ShcoreSucceeds())
    assert level == PER_MONITOR_V2
    print("Per-Monitor V2 utilisé en priorité quand disponible.")


def test_falls_back_to_per_monitor_v1_when_v2_unavailable():
    level = enable_dpi_awareness(user32=_User32V2FailsRestSucceed(), shcore=_ShcoreSucceeds())
    assert level == PER_MONITOR_V1
    print("Repli sur Per-Monitor V1 quand V2 indisponible (Windows plus ancien).")


def test_falls_back_to_system_aware_when_v2_and_v1_unavailable():
    level = enable_dpi_awareness(user32=_User32V2FailsRestSucceed(), shcore=_ShcoreFails())
    assert level == SYSTEM_AWARE
    print("Repli sur System Aware quand V2 ET V1 sont indisponibles.")


def test_returns_unavailable_when_everything_fails():
    level = enable_dpi_awareness(user32=_User32AllFail(), shcore=_ShcoreFails())
    assert level == UNAVAILABLE
    print("Retourne 'unavailable' sans lever quand tout échoue -- ne doit jamais planter le bot.")


def test_shcore_none_does_not_crash_the_fallback_chain():
    """shcore peut être None (ex: import échoué) -- la cascade doit sauter
    directement à System Aware sans planter."""
    level = enable_dpi_awareness(user32=_User32V2FailsRestSucceed(), shcore=None)
    assert level == SYSTEM_AWARE
    print("shcore=None : saute proprement à System Aware, pas de crash.")


def test_real_windows_call_achieves_at_least_system_aware():
    """Seul test qui touche la VRAIE API Windows -- une seule fois (l'OS ne
    permet de fixer le niveau qu'une fois par process). Sur toute machine
    Windows Vista+, au moins SYSTEM_AWARE doit être atteignable."""
    level = enable_dpi_awareness()
    assert level != UNAVAILABLE, f"attendu au moins system_aware sur une vraie machine Windows, reçu {level!r}"
    print(f"Appel réel sur cette machine : niveau obtenu = {level!r}")


class _Gdi32Returns:
    def __init__(self, dpi_x):
        self._dpi_x = dpi_x

    def GetDeviceCaps(self, hdc, index):
        return self._dpi_x


class _User32ForDpiRead:
    def GetDC(self, hwnd):
        return 12345  # faux handle non-nul

    def ReleaseDC(self, hwnd, hdc):
        return 1


class _User32DcFails:
    def GetDC(self, hwnd):
        return 0  # échec (handle nul)

    def ReleaseDC(self, hwnd, hdc):
        return 1


def test_get_current_dpi_scale_percent_reports_100_at_baseline_dpi():
    percent = get_current_dpi_scale_percent(user32=_User32ForDpiRead(), gdi32=_Gdi32Returns(96))
    assert percent == 100
    print(f"96 DPI (baseline Windows) -> {percent}%")


def test_get_current_dpi_scale_percent_reports_125_and_150():
    assert get_current_dpi_scale_percent(user32=_User32ForDpiRead(), gdi32=_Gdi32Returns(120)) == 125
    assert get_current_dpi_scale_percent(user32=_User32ForDpiRead(), gdi32=_Gdi32Returns(144)) == 150
    print("120 DPI -> 125%, 144 DPI -> 150% -- cohérent avec l'avertissement README (100% seul supporté).")


def test_get_current_dpi_scale_percent_returns_none_when_gdi32_unavailable():
    percent = get_current_dpi_scale_percent(user32=_User32ForDpiRead(), gdi32=None)
    assert percent is None
    print("gdi32 indisponible -> None, pas de crash.")


def test_get_current_dpi_scale_percent_returns_none_when_getdc_fails():
    percent = get_current_dpi_scale_percent(user32=_User32DcFails(), gdi32=_Gdi32Returns(96))
    assert percent is None
    print("GetDC échoue (handle nul) -> None, pas de crash.")


class _User32TracksAwarenessAttemptAndReadsDpi:
    """Simule un process JAMAIS rendu DPI-aware -- suit si le code a bien
    TENTÉ de déclarer l'awareness avant de lire le DPI (peu importe le
    résultat de la tentative), plutôt que de compter sur l'appelant."""

    def __init__(self):
        self.awareness_attempted = False

    def SetProcessDpiAwarenessContext(self, ctx):
        self.awareness_attempted = True
        return 1

    def SetProcessDPIAware(self):
        return 1

    def GetDC(self, hwnd):
        return 12345

    def ReleaseDC(self, hwnd, hdc):
        return 1


def test_get_current_dpi_scale_percent_declares_awareness_itself_before_reading():
    """BUG TROUVÉ EN JEU (2026-07-14, rapporté par l'utilisateur) : appelée depuis
    un script isolé (sans jamais passer par main.py), cette fonction
    retournait TOUJOURS 100% même à 125%/150% Windows réels -- Windows ment
    sur GetDeviceCaps tant que le process appelant n'est pas lui-même
    DPI-aware. Vérifie que get_current_dpi_scale_percent() déclare
    maintenant l'awareness LUI-MÊME avant de lire, plutôt que de supposer
    que l'appelant l'a déjà fait."""
    fake_user32 = _User32TracksAwarenessAttemptAndReadsDpi()
    get_current_dpi_scale_percent(user32=fake_user32, gdi32=_Gdi32Returns(120))
    assert fake_user32.awareness_attempted, "doit déclarer l'awareness avant de lire, pas compter sur l'appelant"
    print("get_current_dpi_scale_percent() déclare bien l'awareness lui-même avant de lire.")


def test_get_current_dpi_scale_percent_real_call_returns_a_plausible_value():
    """Seul test qui touche la vraie API pour CETTE fonction -- contrairement
    à enable_dpi_awareness(), GetDC/GetDeviceCaps sont des lectures pures,
    rappelables autant de fois qu'on veut sans effet de bord sur le process."""
    percent = get_current_dpi_scale_percent()
    assert percent is not None, "attendu une valeur lisible sur une vraie machine Windows"
    assert 50 <= percent <= 400, f"valeur suspecte : {percent}%"
    print(f"Scaling Windows réel sur cette machine : {percent}%")


if __name__ == "__main__":
    test_per_monitor_v2_used_when_available()
    test_falls_back_to_per_monitor_v1_when_v2_unavailable()
    test_falls_back_to_system_aware_when_v2_and_v1_unavailable()
    test_returns_unavailable_when_everything_fails()
    test_shcore_none_does_not_crash_the_fallback_chain()
    test_real_windows_call_achieves_at_least_system_aware()
    test_get_current_dpi_scale_percent_reports_100_at_baseline_dpi()
    test_get_current_dpi_scale_percent_reports_125_and_150()
    test_get_current_dpi_scale_percent_returns_none_when_gdi32_unavailable()
    test_get_current_dpi_scale_percent_returns_none_when_getdc_fails()
    test_get_current_dpi_scale_percent_declares_awareness_itself_before_reading()
    test_get_current_dpi_scale_percent_real_call_returns_a_plausible_value()
    print("\nTous les tests passent.")
