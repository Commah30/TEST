# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Ce projet n'a pas de schéma SemVer strict pour l'instant (usage personnel,
pas de consommateurs externes de l'API Python) -- la version sert avant
tout à identifier quelle build tourne (logs, télémétrie, `.exe` packagé),
pas à communiquer des garanties de compatibilité.

## [0.1.0] - 2026-07-26

Première version numérotée. État du projet à ce jalon, pour référence :

### Ajouté
- `wardenbot/__init__.py::__version__` comme source de vérité unique de la
  version (loggée au démarrage de `main.py`, exposée par `app.py::Api` pour
  le dashboard, incluse dans chaque enregistrement d'incident de
  `telemetry.jsonl`).
- Ce fichier `CHANGELOG.md`.
- `wardenbot/circuit.py` : coupe-circuit auto-pause (mécanisme pur, stdlib,
  horloge injectable), 2 critères indépendants (panne franche + flapping),
  branché sur `main.py` (touche C) et `app.py::BotController`, alerte
  `winsound.Beep` + incident journalisé dans `telemetry.jsonl`
  (`TelemetryWorker.log_incident`, nouveau type d'enregistrement).
- `wardenbot/paths.py` : `resource_path()` (ressources bundlées en lecture
  seule -- templates/config/ui.html/poids YOLO, ancré sur `sys._MEIPASS`)
  ET `data_path()` (état mutable -- `profiles/`, `crash.log`, ancré au
  dossier de l'exécutable). Deux fonctions distinctes suite à un vrai crash
  reproduit au premier build packagé : `profiles/` avait d'abord été traité
  à tort comme lecture seule, alors que `calibration_wizard.py` y écrit.
  `wardenbot/config_manager.py` copie les profils par défaut vers
  l'emplacement inscriptible au premier lancement.
- `app.py` : gestionnaire de crash (`sys.excepthook`/`threading.excepthook`)
  écrivant vers `crash.log` (à côté de l'exe, `data_path()`) -- seule trace
  exploitable d'un crash en `console=False`, sans quoi la fenêtre
  "Unhandled exception in script" de PyInstaller se ferme sans rien
  laisser lire. UTF-8 stdout/stderr dupliqué directement dans `app.py`
  (défensif sur un flux `None`) plutôt que de dépendre implicitement de
  l'import de `main.py`.
- `wardenbot.spec` restructuré en mode `onedir` (cible `app.py`), poids YOLO
  entraînés retirés du suivi git (`.gitignore`, désormais distribués en
  asset de release séparé — n'efface pas l'historique existant).
- `requirements.txt` épinglé sur les versions réellement installées/vérifiées.
- CI (`portable-tests.yml`) : ajout de `test_helena.py`/`test_saryn.py`/
  `test_vigour.py`/`test_circuit.py`/`test_paths.py`.
- Tous les logs de SESSION (`debug.log`, `error.log`, `capture_watchdog.log`,
  `focus_check.log`, `input_timing.log`, `press_confirm.log`,
  `telemetry.jsonl`) migrés vers `data_path()` -- suite au retour reçu :
  un lancement packagé depuis un CWD différent du dossier de l'exe (test de
  packaging depuis `System32`, ou un raccourci avec "Démarrer dans"
  vide/différent) aurait fait échouer ces écritures, ou pire, réussir
  silencieusement ailleurs (VirtualStore) -- en particulier `telemetry.jsonl`,
  le canal du coupe-circuit (`log_incident`), dont c'est précisément le
  contrat de ne jamais échouer silencieusement. `kb/`
  (`wardenbot/kb_engine.py`) reste volontairement hors scope : outil CLI de
  dev, CWD maîtrisé par définition, jamais appelé par `app.py`.

- `app.py::BotController` active désormais la télémétrie
  (`build_engine(..., enable_telemetry=True)`, comme `main.py::__main__`) --
  avant ça, `log_incident` (coupe-circuit) était un no-op silencieux pour
  toute session lancée depuis le dashboard GUI, indépendamment de tout
  chemin de fichier : le coupe-circuit bipait/pausait très bien, mais
  n'écrivait jamais dans `telemetry.jsonl`. En activant, un défaut adjacent
  s'est révélé et a été corrigé du même geste : `main.py::build_engine`'s
  `_shutdown()` (fuite confirmée, audit 2026-07-18, pour `input_worker`/le
  thread de détection) n'incluait PAS `telemetry.stop()` -- inoffensif tant
  que `telemetry` était toujours `None` pour `BotController`, mais serait
  devenu une vraie fuite (thread daemon + fichier ouvert abandonnés à
  chaque changement de personnage via `set_profile()`) maintenant que
  `app.py` l'active. `_shutdown()` arrête maintenant aussi `telemetry` s'il
  existe.

### Limitations connues (documentées, pas des bugs à corriger par réflexe)
- `wardenbot/config_manager.py::_seed_default_profiles_if_missing()` copie
  les profils par défaut vers l'emplacement inscriptible UNE SEULE FOIS, si
  absent. Si une future version du bundle corrige un profil par défaut, les
  installations existantes ne le verront JAMAIS (le dossier existe déjà,
  seed sauté) -- comportement voulu (ne jamais écraser un profil déjà
  calibré par l'utilisateur), mais à revisiter le jour où les profils par
  défaut évoluent réellement après une release.

### Existant à ce jalon (pas nouveau, pour contexte)
- Personnages : Meiko (Fellowship, Monk tank), Tariq (Fellowship, YOLO +
  EasyOCR), Vigour (Fellowship), Helena (Fellowship, tank), Saryn Prime
  (Warframe).
- `app.py` : dashboard `pywebview` (`BotController`/`Api`), start/pause/
  stop/set_profile/calibrate/logs.
- `wardenbot/telemetry.py` : journal de décisions opt-in (`telemetry.jsonl`).
- `wardenbot/kb_engine.py` : suggestions de tuning à partir de la télémétrie
  (human-in-the-loop, jamais appliqué automatiquement).
- `wardenbot/freshness.py` : politique de fraîcheur de détection
  (`FreshnessPolicy`/`DetectionMeta`).
- Protocole de confirmation des presses (`InputWorker`/
  `RotationEngine.report_press_outcome`).
- `RotationEngine.run_forever(on_error=...)` : robustesse face aux
  exceptions de tick (ne tue plus le bot).
