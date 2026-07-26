"""
config_manager.py — charge le config.json d'un profil (coordonnées écran et
touches, externalisées de game_config.py) et les expose au reste du projet
via une API stable (get_region/get_key), sans que quoi que ce soit d'autre
ait besoin de connaître le format JSON sous-jacent.

PHASE 1 de l'industrialisation (2026-07-14) : objectif ZÉRO changement de
comportement. game_config.py::REGIONS/KEY_MAP ont été recopiés tels quels
(vérifiés par diff programmatique avant suppression des dicts Python, cf.
commit) -- ConfigManager ne fait que les relire. wardenbot/state_update.py
et wardenbot/hooks.py ne changent PAS : ils continuent de recevoir un dict
`regions` classique (via ConfigManager.regions), exactement la même forme
qu'avant. Seul main.py (le point d'assemblage) parle au ConfigManager
directement.

PHASE 2 (2026-07-14) : support multi-profils -- un profil représente une
CONFIGURATION D'AFFICHAGE (résolution/DPI/échelle UI du jeu), orthogonale au
personnage (qui définit regions/keybinds À L'INTÉRIEUR d'un profil, cf.
schéma JSON). config/default.json (Phase 1) est devenu le profil "Default"
(profiles/Default/config.json) -- déplacé, pas dupliqué, vérifié identique
par diff programmatique avant suppression de l'ancien fichier.

Les champs resolution/dpi_scale/game_ui_scale sont exposés en lecture seule
(self.resolution, etc.) mais ne sont lus par AUCUN code moteur aujourd'hui
(engine.py/hooks.py/state_update.py) -- réservés à un usage futur (UI de
sélection de profil, diagnostic, Phase 3 DPI awareness). Le moteur ne
connaît toujours que .regions/.keybinds, exactement comme en Phase 1.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

from wardenbot.paths import data_path, resource_path

SUPPORTED_CONFIG_VERSION = 1
# data_path(), PAS resource_path() (correction 2026-07-26, retour reçu) :
# profiles/ est ÉCRIT par wardenbot/calibration_wizard.py (nouveau profil
# sauvegardé après calibration) -- ce n'est PAS une ressource en lecture
# seule. resource_path() (ancré sur sys._MEIPASS, donc _internal/ en
# onedir) serait écrasé au prochain redéploiement du bundle et
# potentiellement non-inscriptible (Program Files). data_path() ancre sur
# le dossier de l'exécutable -- réflexe "app portable", cohérent avec le
# public de ce projet. En dev, comportement inchangé (Path("profiles"),
# relatif au CWD). Cf. wardenbot/paths.py pour la distinction complète.
PROFILES_DIR = data_path("profiles")
DEFAULT_PROFILE = "Default"


def _seed_default_profiles_if_missing() -> None:
    """Premier lancement d'un .exe packagé : PROFILES_DIR (à côté de l'exe,
    data_path) n'existe pas encore -- copie les profils par défaut depuis le
    bundle en lecture seule (resource_path, _internal/profiles) vers cet
    emplacement inscriptible, en UNE fois. No-op en dev (data_path ==
    resource_path == même Path("profiles") relatif au CWD -- le dossier
    existe déjà, rien à copier) et no-op si PROFILES_DIR existe déjà (ne
    réécrase JAMAIS des profils déjà calibrés par l'utilisateur -- même sur
    une mise à jour du bundle qui ajouterait un nouveau profil par défaut ;
    limitation connue, pas un cas mesuré à ce jour).

    EN: First launch of a packaged .exe: PROFILES_DIR (beside the exe,
    EN: data_path) doesn't exist yet -- copies the default profiles from the
    EN: read-only bundle (resource_path, _internal/profiles) to this
    EN: writable location, ONCE. No-op in dev (data_path == resource_path ==
    EN: the same Path("profiles") relative to the CWD -- the folder already
    EN: exists, nothing to copy) and no-op if PROFILES_DIR already exists
    EN: (NEVER overwrites profiles the user has already calibrated -- even
    EN: on a bundle update that would add a new default profile; a known
    EN: limitation, not a measured real case yet).
    """
    if not getattr(sys, "frozen", False):
        return
    if PROFILES_DIR.exists():
        return
    bundled = resource_path("profiles")
    if bundled.is_dir():
        shutil.copytree(bundled, PROFILES_DIR)


_seed_default_profiles_if_missing()


class ConfigVersionError(Exception):
    """La config chargée a un numéro de version que cette version de
    wardenbot ne sait pas lire -- distinct d'une erreur JSON/fichier
    manquant, pour que l'appelant (ou une future UI) puisse proposer
    spécifiquement une migration plutôt qu'un message générique."""


class ProfileValidationError(Exception):
    """Le profil se charge (JSON valide, version correcte) mais n'est pas
    utilisable pour FAIRE TOURNER le bot -- cf. validate_profile(). Distinct
    de ConfigVersionError/FileNotFoundError (échecs de CHARGEMENT), celui-ci
    est un échec de CONTENU."""


class ProfileNotCalibratedError(ProfileValidationError):
    """Cas le plus fréquent de ProfileValidationError -- séparé pour qu'un
    appelant (une future UI) puisse proposer une action précise ("Lancer la
    calibration") plutôt qu'un message générique. cf. discussion (2026-07-14) :
    'je préfère un échec explicite à un fonctionnement silencieux dégradé' --
    un profil non calibré (regions vides) ne doit JAMAIS pouvoir démarrer le
    bot pour de vrai, même si techniquement rien ne plante."""


def list_profiles() -> list[str]:
    """Noms des profils disponibles (sous-dossiers de profiles/ contenant un
    config.json) -- triés pour un affichage stable (ex: futur menu de
    sélection). Ne lève jamais si profiles/ n'existe pas -- retourne juste
    une liste vide, à l'appelant de décider quoi en faire."""
    if not PROFILES_DIR.is_dir():
        return []
    return sorted(
        p.name for p in PROFILES_DIR.iterdir()
        if p.is_dir() and (p / "config.json").exists()
    )


class ConfigManager:
    def __init__(
        self,
        character: str,
        profile: Optional[str] = None,
        path: "Optional[str | Path]" = None,
    ):
        """
        profile : nom d'un dossier sous profiles/ (ex: "Default", "Ultrawide").
        path : chemin explicite vers un config.json -- prioritaire sur
        `profile` s'il est fourni (utilisé par les tests pour pointer vers un
        fichier temporaire). Si ni l'un ni l'autre n'est donné, charge le
        profil "Default" -- comportement STRICTEMENT identique à la Phase 1
        (qui chargeait toujours config/default.json, devenu
        profiles/Default/config.json).
        """
        if path is not None:
            self.path = Path(path)
        else:
            self.path = PROFILES_DIR / (profile or DEFAULT_PROFILE) / "config.json"
        self.character = character
        self.profile = profile or DEFAULT_PROFILE

        if not self.path.exists():
            raise FileNotFoundError(f"Fichier de config introuvable : {self.path}")
        raw = json.loads(self.path.read_text(encoding="utf-8"))

        version = raw.get("version")
        if version != SUPPORTED_CONFIG_VERSION:
            raise ConfigVersionError(
                f"{self.path} : version {version!r} non supportée par cette version "
                f"de wardenbot (attendu {SUPPORTED_CONFIG_VERSION})."
            )

        # Métadonnées de profil -- PAS lues par le moteur, cf. docstring.
        # Valeurs par défaut permissives (None/1.0) : un profil qui ne les
        # renseigne pas encore (aucun cas aujourd'hui, mais reste possible
        # pour un config.json écrit à la main) ne doit pas lever d'erreur.
        self.resolution: Optional[str] = raw.get("resolution")
        self.dpi_scale: Optional[float] = raw.get("dpi_scale")
        self.game_ui_scale: float = raw.get("game_ui_scale", 1.0)
        self.calibrated: bool = raw.get("calibrated", True)

        characters = raw.get("characters", {})
        if character not in characters:
            raise KeyError(
                f"{self.path} : aucune config pour le personnage {character!r} "
                f"(disponibles : {list(characters)})"
            )

        char_cfg = characters[character]
        # Dicts bruts -- même forme que l'ancien REGIONS[character]/KEY_MAP[character]
        # (wardenbot/game_config.py, avant migration). Exposés directement pour
        # les modules qui ont besoin d'itérer/faire plusieurs .get() dessus
        # (cf. wardenbot/state_update.py) sans passer par un appel de méthode
        # par clé -- pas de changement de comportement pour ce code-là.
        self.regions: dict = char_cfg.get("regions", {})
        self.keybinds: dict = char_cfg.get("keybinds", {})

    def get_region(self, name: str) -> Optional[dict]:
        """{'left','top','width','height'} ou None si la région n'existe pas
        -- même contrat que REGIONS[character].get(name) avant migration."""
        return self.regions.get(name)

    def get_key(self, name: str) -> Optional[str]:
        """Touche mappée ou None -- même contrat que
        KEY_MAP[character].get(name) avant migration."""
        return self.keybinds.get(name)


def validate_profile(config: ConfigManager, required_regions: "Optional[list[str]]" = None) -> None:
    """Vérifie qu'un profil est utilisable pour FAIRE TOURNER le bot -- pas
    seulement pour l'inspecter/l'éditer (ConfigManager lui-même reste
    permissif pour cet usage-là, cf. sa docstring : la calibration doit
    pouvoir OUVRIR un profil non calibré pour le remplir).

    Centralisée ici pour être réutilisée telle quelle par :
      - build_engine() (main.py), avant de démarrer pour de vrai (dry_run=False) ;
      - un futur bouton "Diagnostic" (Phase 5) ;
      - l'assistant de calibration (Phase 4), pour savoir s'il reste du
        travail avant de proposer "Terminer".

    required_regions : liste des noms de région ESSENTIELS pour que le
    personnage fonctionne (ex: la barre de Fury, la barre de buffs) --
    fournie par l'appelant (cf. game_config.py::CHARACTERS[...]["required_regions"]),
    jamais hardcodée ici : ce module ne doit rien connaître du jeu lui-même.

    Lève au premier problème trouvé (ProfileNotCalibratedError ou
    ProfileValidationError) plutôt que de laisser tourner un bot "à
    l'aveugle" -- cf. discussion (2026-07-14) : un échec explicite vaut mieux
    qu'un fonctionnement silencieux dégradé.
    """
    if not config.calibrated:
        raise ProfileNotCalibratedError(
            f"Le profil {config.profile!r} n'est pas calibré pour {config.character!r}. "
            "Lance l'assistant de calibration avant de démarrer le bot."
        )

    if not config.keybinds:
        raise ProfileValidationError(
            f"Le profil {config.profile!r} n'a aucune touche configurée pour {config.character!r}."
        )

    if required_regions:
        missing = [name for name in required_regions if config.get_region(name) is None]
        if missing:
            raise ProfileValidationError(
                f"Le profil {config.profile!r} n'a pas toutes les régions requises "
                f"pour {config.character!r} : {missing}"
            )
