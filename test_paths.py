"""
test_paths.py — prouve, sans écran ni jeu (stdlib seulement), que
wardenbot/paths.py distingue bien ressources LECTURE SEULE (resource_path)
et état MUTABLE (data_path) :

resource_path() :
  1. en dev (pas de sys.frozen) laisse le comportement INCHANGÉ (relatif au
     CWD, comme avant ce module) ;
  2. dans un bundle PyInstaller (sys.frozen=True, sys._MEIPASS positionné --
     onedir OU onefile) ancre le chemin à sys._MEIPASS, PAS au dossier de
     l'exécutable ni au CWD ;
  3. si sys._MEIPASS est absent malgré sys.frozen=True (cas défensif,
     jamais vu avec PyInstaller mais couvert par sécurité), retombe sur le
     dossier de l'exécutable plutôt que de planter.

data_path() :
  4. en dev, comportement inchangé (relatif au CWD) ;
  5. en mode gelé, ancre TOUJOURS au dossier de l'exécutable -- JAMAIS à
     sys._MEIPASS (_internal/), contrairement à resource_path().

Le test 2 couvre un vrai bug corrigé le 2026-07-26 (cf. wardenbot/paths.py,
section CORRECTION) : la 1re version de resource_path() ancrait sur
Path(sys.executable).parent, en supposant qu'un onedir place ses datas=
directement à côté de l'exe -- FAUX, PyInstaller 6.x les place dans
_internal/ par défaut. Un vrai build+lancement (crash.log capturé via
app.py) a confirmé le bug avant ce fix. Le test 5 couvre le suivi direct de
ce fix : profiles/ (écrit par calibration_wizard.py) ne doit PAS finir dans
_internal/, sous peine d'être écrasé au prochain redéploiement du bundle ou
de ne plus pouvoir s'écrire du tout (dossier d'installation non
inscriptible) -- d'où data_path(), une fonction séparée de resource_path().

Lance avec : python test_paths.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from wardenbot.paths import data_path, resource_path


def test_dev_mode_returns_relative_path_unchanged():
    """Pas de sys.frozen (cas normal en dev, `python main.py`) -> comportement
    IDENTIQUE à avant ce module : Path(relative_path) tel quel, PAS résolu
    en absolu, PAS ancré à autre chose que le CWD implicite de Path()."""
    assert not hasattr(sys, "frozen"), "ce test suppose un interpréteur non gelé"
    result = resource_path("templates/meiko")
    assert result == Path("templates/meiko")
    assert not result.is_absolute(), "en dev, resource_path ne doit PAS forcer un chemin absolu"
    print("Dev (pas de sys.frozen) : resource_path() inchangé, relatif au CWD.")


def test_frozen_mode_anchors_to_meipass_not_executable_directory():
    """sys.frozen=True + sys._MEIPASS positionné (PyInstaller pose TOUJOURS
    les deux avant tout code utilisateur, onedir ET onefile) -> ancré à
    _MEIPASS, PAS à Path(sys.executable).parent -- en onedir, _MEIPASS
    pointe vers _internal/ (le "contents directory" par défaut de
    PyInstaller 6.x), un sous-dossier DIFFÉRENT de celui de l'exe."""
    exe_dir = Path("C:/Program Files/WardenBot") if sys.platform == "win32" else Path("/opt/wardenbot")
    meipass_dir = exe_dir / "_internal"   # cf. PyInstaller 6.x, contents-directory par défaut
    fake_exe = exe_dir / "wardenbot.exe"
    sys.frozen = True
    sys._MEIPASS = str(meipass_dir)
    sys.executable = str(fake_exe)
    try:
        result = resource_path("config/tariq.yaml")
        assert result == (meipass_dir.resolve() / "config/tariq.yaml"), (
            "doit s'ancrer sur _MEIPASS (_internal/), PAS sur le dossier de l'exe -- "
            "c'est exactement le bug reproduit en vrai le 2026-07-26"
        )
        assert result != (exe_dir.resolve() / "config/tariq.yaml"), (
            "confirme que _internal/ et le dossier de l'exe sont bien DEUX chemins différents ici"
        )
        assert result.is_absolute()
    finally:
        del sys.frozen
        del sys._MEIPASS
    print("Gelé + _MEIPASS positionné : resource_path() ancré sur _MEIPASS (_internal/), pas sur le dossier de l'exe.")


def test_frozen_mode_falls_back_to_executable_directory_if_meipass_missing():
    """sys.frozen=True mais _MEIPASS absent (cas défensif -- jamais observé
    avec PyInstaller, qui pose toujours les deux ensemble) -> retombe sur
    Path(sys.executable).parent plutôt que de lever AttributeError."""
    exe_dir = Path("C:/Program Files/WardenBot") if sys.platform == "win32" else Path("/opt/wardenbot")
    fake_exe = exe_dir / "wardenbot.exe"
    sys.frozen = True
    sys.executable = str(fake_exe)
    assert not hasattr(sys, "_MEIPASS")
    try:
        result = resource_path("config/tariq.yaml")
        assert result == (exe_dir.resolve() / "config/tariq.yaml")
    finally:
        del sys.frozen
    print("Gelé sans _MEIPASS (cas défensif) : repli sur le dossier de l'exécutable, pas de crash.")


def test_data_path_dev_mode_returns_relative_path_unchanged():
    """Pas de sys.frozen -> comportement IDENTIQUE à resource_path() en dev :
    Path(relative_path) tel quel, relatif au CWD."""
    assert not hasattr(sys, "frozen")
    result = data_path("profiles")
    assert result == Path("profiles")
    assert not result.is_absolute()
    print("Dev (pas de sys.frozen) : data_path() inchangé, relatif au CWD.")


def test_data_path_frozen_mode_anchors_to_executable_directory_never_meipass():
    """sys.frozen=True + sys._MEIPASS positionné (_internal/, comme un vrai
    onedir) -> data_path() doit s'ancrer au dossier de l'EXÉCUTABLE, PAS à
    _MEIPASS -- LE POINT MÊME de data_path() : profiles/ (écrit par
    calibration_wizard.py) ne doit jamais atterrir dans _internal/, sous
    peine d'être écrasé au prochain redéploiement du bundle ou de ne plus
    pouvoir s'écrire si le dossier d'installation n'est pas inscriptible."""
    exe_dir = Path("C:/Program Files/WardenBot") if sys.platform == "win32" else Path("/opt/wardenbot")
    meipass_dir = exe_dir / "_internal"
    fake_exe = exe_dir / "wardenbot.exe"
    sys.frozen = True
    sys._MEIPASS = str(meipass_dir)
    sys.executable = str(fake_exe)
    try:
        result = data_path("profiles")
        assert result == (exe_dir.resolve() / "profiles"), (
            "data_path() doit s'ancrer sur le dossier de l'exe, jamais sur _MEIPASS/_internal"
        )
        assert result != (meipass_dir.resolve() / "profiles"), (
            "confirme que data_path() et resource_path() divergent bien quand _MEIPASS != dossier de l'exe"
        )
    finally:
        del sys.frozen
        del sys._MEIPASS
    print("Gelé + _MEIPASS positionné : data_path() reste ancré au dossier de l'exe, jamais à _internal/.")


if __name__ == "__main__":
    test_dev_mode_returns_relative_path_unchanged()
    test_frozen_mode_anchors_to_meipass_not_executable_directory()
    test_frozen_mode_falls_back_to_executable_directory_if_meipass_missing()
    test_data_path_dev_mode_returns_relative_path_unchanged()
    test_data_path_frozen_mode_anchors_to_executable_directory_never_meipass()
    print("\nTous les tests paths sont passés.")
