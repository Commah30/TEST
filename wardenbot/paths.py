"""
paths.py — résolution des chemins liés au packaging PyInstaller (audit "RPA
professionnel", 2026-07-26), en DEUX familles distinctes -- lecture seule
vs écriture -- suite à un vrai bug trouvé en confondant les deux :

`resource_path()` : ressources BUNDLÉES EN LECTURE SEULE (templates/,
config/*.yaml, ui.html, poids YOLO) -- figées au moment du build, ancrées
sur `sys._MEIPASS` (le dossier qui contient réellement `datas=` dans un
bundle PyInstaller, onedir OU onefile).

`data_path()` : état MUTABLE (profiles/, `crash.log`) -- écrit par
l'application elle-même à l'exécution, ancré sur le dossier de
l'EXÉCUTABLE (`Path(sys.executable).parent`), PAS sur `sys._MEIPASS`.

POURQUOI DEUX FONCTIONS, PAS UNE (2026-07-26, retour reçu après le fix
_MEIPASS) : `resource_path()` a d'abord servi, à tort, pour `profiles/` --
or `wardenbot/calibration_wizard.py` ÉCRIT dans `PROFILES_DIR` (nouveau
profil sauvegardé après calibration). Un profil vit dans `_internal/`
(ancré sur `sys._MEIPASS`) aurait deux défauts concrets : (1) tout
redéploiement du bundle (nouveau zip décompressé par-dessus) écraserait
silencieusement les profils calibrés par l'utilisateur ; (2) l'écriture
échouerait purement et simplement si le dossier d'installation n'est pas
inscriptible (ex. Program Files). `data_path()` (dossier de l'exe, PAS
`_internal/`) correspond au réflexe "app portable" -- cohérent avec le
public de ce projet (soi-même + utilisateurs avancés, cf. discussion), par
opposition au réflexe "app installée" qui écrirait plutôt dans
`%LOCALAPPDATA%`.

Ni l'un ni l'autre ne couvre les logs de session (debug.log, error.log,
telemetry.jsonl, kb/) -- ceux-là restent `Path("...")` relatif au CWD comme
avant ce module (hors scope du plan de packaging original : rien ne prouve
qu'ils posent un problème réel pour l'instant, cf. discussion 2026-07-26 --
au lancement normal par double-clic, le CWD Windows est déjà le dossier de
l'exe).

CORRECTION `resource_path()` (2026-07-26, après un vrai crash reproduit et
lu via `app.py::CRASH_LOG_PATH`) : la version initiale de ce module ancrait
`resource_path()` sur `Path(sys.executable).resolve().parent`, en supposant
qu'un onedir place ses `datas=` directement à côté de l'exécutable. FAUX en
pratique : PyInstaller 6.x place par défaut TOUT (interpréteur, .pyd/.dll,
ET `datas=`) dans un sous-dossier `_internal/` (le "contents directory",
configurable via `--contents-directory` mais `_internal` par défaut) --
SEUL le `.exe` reste au niveau racine du dossier onedir. Fixé en ancrant
`resource_path()` sur `sys._MEIPASS`, que PyInstaller positionne TOUJOURS
sur le dossier qui contient réellement les `datas=`, onedir (`_internal/`
stable, jamais extrait) ou onefile (dossier temporaire auto-extrait à
chaque lancement) -- la différence onedir/onefile porte sur COMMENT ce
dossier existe, jamais sur QUEL attribut lire pour le trouver.

EN: paths.py — resolves PyInstaller-packaging-related paths ("professional
EN: RPA" audit, 2026-07-26), in TWO distinct families -- read-only vs
EN: writable -- following a real bug found by conflating the two:

EN: `resource_path()`: READ-ONLY BUNDLED resources (templates/,
EN: config/*.yaml, ui.html, YOLO weights) -- fixed at build time, anchored
EN: on `sys._MEIPASS` (the folder that actually holds `datas=` in a
EN: PyInstaller bundle, onedir OR onefile).

EN: `data_path()`: MUTABLE state (profiles/, `crash.log`) -- written by the
EN: application itself at runtime, anchored on the EXECUTABLE's folder
EN: (`Path(sys.executable).parent`), NOT on `sys._MEIPASS`.

EN: WHY TWO FUNCTIONS, NOT ONE (2026-07-26, feedback received after the
EN: _MEIPASS fix): `resource_path()` was first used, wrongly, for
EN: `profiles/` -- but `wardenbot/calibration_wizard.py` WRITES into
EN: `PROFILES_DIR` (a new profile saved after calibration). A profile
EN: living in `_internal/` (anchored on `sys._MEIPASS`) would have two
EN: concrete flaws: (1) any bundle redeployment (a new zip unpacked on top)
EN: would silently wipe the user's calibrated profiles; (2) writing would
EN: simply fail if the install folder isn't writable (e.g. Program Files).
EN: `data_path()` (the exe's folder, NOT `_internal/`) matches the
EN: "portable app" reflex -- consistent with this project's audience
EN: (yourself + advanced users, cf. discussion), as opposed to the
EN: "installed app" reflex which would write to `%LOCALAPPDATA%` instead.

EN: Neither covers session logs (debug.log, error.log, telemetry.jsonl,
EN: kb/) -- those stay `Path("...")` relative to the CWD like before this
EN: module (out of the original packaging plan's scope: nothing proves
EN: they're an actual problem yet, cf. 2026-07-26 discussion -- on a normal
EN: double-click launch, Windows' CWD is already the exe's folder).

EN: `resource_path()` CORRECTION (2026-07-26, after a real crash reproduced
EN: and read via `app.py::CRASH_LOG_PATH`): this module's initial version
EN: anchored `resource_path()` on `Path(sys.executable).resolve().parent`,
EN: assuming a onedir build places its `datas=` directly next to the
EN: executable. WRONG in practice: PyInstaller 6.x by default puts
EN: EVERYTHING (interpreter, .pyd/.dll, AND `datas=`) into an `_internal/`
EN: subfolder (the "contents directory", configurable via
EN: `--contents-directory` but `_internal` by default) -- ONLY the `.exe`
EN: stays at the onedir folder's root. Fixed by anchoring `resource_path()`
EN: on `sys._MEIPASS`, which PyInstaller ALWAYS points at the folder that
EN: actually holds `datas=`, onedir (`_internal/`, stable, never extracted)
EN: or onefile (self-extracted temp folder on every launch) -- the
EN: onedir/onefile difference is about HOW that folder comes to exist,
EN: never about WHICH attribute to read to find it.
"""
from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative_path: "str | Path") -> Path:
    """Ressource BUNDLÉE EN LECTURE SEULE (templates/, config/*.yaml,
    ui.html, poids YOLO). En dev (`sys.frozen` absent) : comportement
    INCHANGÉ, `Path(relative_path)` tel quel, relatif au CWD. Dans un bundle
    PyInstaller (onedir ou onefile) : ancré à `sys._MEIPASS`.

    EN: READ-ONLY BUNDLED resource (templates/, config/*.yaml, ui.html,
    EN: YOLO weights). In dev (no `sys.frozen`): UNCHANGED behavior,
    EN: `Path(relative_path)` as-is, relative to the CWD. In a PyInstaller
    EN: bundle (onedir or onefile): anchored to `sys._MEIPASS`.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) if meipass else Path(sys.executable).resolve().parent
        return base / relative_path
    return Path(relative_path)


def data_path(relative_path: "str | Path") -> Path:
    """État MUTABLE écrit par l'application (profiles/, crash.log). En dev
    (`sys.frozen` absent) : comportement INCHANGÉ, `Path(relative_path)` tel
    quel, relatif au CWD. Dans un bundle PyInstaller : ancré au dossier de
    l'EXÉCUTABLE (`Path(sys.executable).parent`) -- PAS `sys._MEIPASS`
    (`_internal/`), qui serait écrasé au prochain redéploiement du bundle
    et potentiellement non-inscriptible (ex. Program Files).

    EN: MUTABLE state written by the application (profiles/, crash.log). In
    EN: dev (no `sys.frozen`): UNCHANGED behavior, `Path(relative_path)` as-
    EN: is, relative to the CWD. In a PyInstaller bundle: anchored to the
    EN: EXECUTABLE's folder (`Path(sys.executable).parent`) -- NOT
    EN: `sys._MEIPASS` (`_internal/`), which would get overwritten on the
    EN: next bundle redeployment and might not be writable (e.g. Program
    EN: Files).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / relative_path
    return Path(relative_path)
