# wardenbot

*🇫🇷 Version française d'abord · 🇬🇧 English version below*

---

## 🇫🇷 Français

> **⚠️ Important** — Pour le moment, le bot nécessite Windows configuré
> à **100% DPI scaling**. Les scaling **125% et 150% ne sont pas supportés**
> dans cette version : les coordonnées de capture/souris/InputWorker
> peuvent être décalées. Nous recommandons fortement de rester en 100%
> pendant l'utilisation. Le support complet 125%/150% arrivera plus tard,
> une fois la base (config JSON + assistant de calibration) solide.

Moteur de rotation Python pour cinq personnages sur deux jeux : **Meiko**,
**Tariq**, **Vigour** et **Helena** sur **Fellowship**, et **Saryn Prime**
sur **Warframe** (assistant 2 sorts, pas un autopilote complet — cf.
section Saryn). Remplace des hooks de décision opaques par du code Python
que tu contrôles et lis toi-même — la logique de rotation n'est jamais une
boîte noire. Utilisable en console (`main.py`) ou via un dashboard desktop
(`app.py`, cf. section dédiée plus bas).

Détection 100% par capture d'écran (jamais de lecture mémoire du jeu) :
template matching OpenCV, un détecteur YOLO entraîné pour les buffs qui se
ressemblent trop pour le template matching, et EasyOCR pour le texte/les
chiffres à l'écran.

### Les personnages

#### Meiko (`config/warden.yaml`, `hooks.py::meiko_pre_tick`)

Complète un combo en cours (1re pièce → 2e pièce → Fighting Techniques,
finisher universel off-GCD). La décision "Spirited Vortex ou Spirited
Strikes ?" au 3e stack de Warden of the Temple est un simple `if`/`else`
lisible, pas un hook opaque.

#### Tariq (`config/tariq.yaml`, `hooks.py::tariq_pre_tick`)

Rotation en liste de priorité classique (pas de séquence à compléter). Les
abilities scannent leurs conditions (`fury_above:N`, `flag:X`, `buff:X`,
`debuff:X`) dans l'ordre de priorité à chaque tick. Points notables :

- **Hammer Storm** part sur n'importe quel proc détecté, immédiatement (pas
  d'attente d'un 2e stack — le jeu n'affiche jamais "2" sur ce badge).
- **Focused Wrath** réduit de 50% le coût en Fury des 2 prochains Hammer
  Storm/Skull Crusher (mécanique vérifiée sur le wiki officiel) — consommé
  dès que le seuil réduit est atteint plutôt que d'attendre le seuil plein.
- **Wild Swing** est un pur filler : seulement quand rien d'autre n'est
  disponible et que la Fury n'est pas déjà pleine.
- Détection de buffs par **YOLO** (`wardenbot/yolo_detect.py`) plutôt que
  template matching : `hammer_storm_buff` et `skull_crusher_buff` partagent
  le même artwork et se faisaient confondre par `cv2.matchTemplate` — un
  modèle entraîné sur un dataset semi-auto-labellisé (`build_yolo_dataset.py`
  + `train_yolo.py`) les sépare correctement, y compris 3 buffs actifs en
  même temps.

#### Vigour (`config/vigour.yaml`, `hooks.py::vigour_pre_tick_early_game`)

Healer de groupe (build "early game", avant le palier 4 des talents — cf.
`game_config.py::CHARACTERS["vigour"]` pour la bascule statique vers la
rotation Legendary Cloak post-palier-4). Priorise dans l'ordre : soin
critique (Greater Heal sur l'allié le plus bas), cleanse, Circle of Light si
3+ alliés sous 70% HP (ciblé sur le tank, faute de meilleur point d'ancrage),
Rune of Renewal/Dawnbreaker Orb selon les orbes disponibles, Soulbrand en
dégâts si personne n'a besoin de soin, Dawnflare en filler. Lecture
**synchrone** (pas de thread de détection dédié, cf. Tariq) — la décision et
la détection sont assez légères pour tenir dans le même tick à 30Hz.

#### Helena (`config/helena.yaml`, `hooks.py::helena_pre_tick`)

Tank (mesure/pouvoir/bouclier), rotation en liste de priorité comme Tariq.
Réductions de cooldown en cascade suivies via `state.hooks`
(`HELENA_CDR_MS`) : Measured/Power Strike raccourcissent Shield Slam et
Shield Throw, Shield Slam/Shield Throw raccourcissent Shockwave, Shockwave
raccourcit Shields Up, Hold the Line reset Shield Slam. Shield Throw et Iron
Wall restent volontairement HORS auto-rotation (charge et urgence,
manuelles) — cf. commentaire de tête de `config/helena.yaml`.

#### Saryn Prime (`config/saryn.yaml`, Warframe — 2026-07-20)

Premier personnage hors Fellowship. **Assistant à 2 sorts (Toxic Lash,
Nourish), volontairement PAS un autopilote complet** — visée/déplacement et
le 3e sort restent à la main de l'utilisateur (cf. commentaire de tête du
YAML, réserve explicite sur les CGU Warframe). Chaque sort se relance dès
que son buff est retombé ET que l'Énergie dépasse 200 (`energy_above:200` —
lit une valeur ABSOLUE via OCR, pas un pourcentage 0-1.0 comme
`fury_above`/`toughness_above`, cf. `test_saryn.py`). Pas de GCD partagé
façon Fellowship : les deux abilities sont `off_gcd` + `skip_detection`
(gatées par condition, pas par template d'icône).

### Architecture

```
main.py                    point d'entrée console : build_engine(), boucle de debug (logger/hotkeys), __main__
app.py                      dashboard desktop (PyWebView) -- réutilise le wiring de main.py
ui.html                     interface du dashboard (appelle app.py::Api via window.pywebview.api.*)
CHANGELOG.md                historique des versions (Keep a Changelog)
config/*.yaml                abilities/rotation par personnage (warden/tariq/vigour/helena/saryn)
config/*.json, profiles/     KEY_MAP/REGIONS par profil d'affichage (résolution/DPI), cf. config_manager.py
test_wardenbot.py, test_tariq.py, test_helena.py, test_saryn.py, test_vigour.py, test_circuit.py
                              tests de logique pure (aucun écran requis, cf. section Tests)
tick_timing_test.py          mesure la durée réelle d'un tick (capture+YOLO+OCR+logique)
capture_helper.py            utilitaire Windows : trouver régions + capturer templates PNG
detect_check.py              affiche en direct le score de confiance de chaque template
ocr_check.py                 affiche en direct ce qu'EasyOCR lit dans une région
yolo_check.py                affiche en direct ce que le modèle YOLO détecte
build_yolo_dataset.py        construit un dataset YOLO à partir d'un clip vidéo
train_yolo.py                entraîne un modèle YOLO sur ce dataset
requirements.txt             pyyaml, mss, opencv-python-headless, pydirectinput, pywebview,
                              numpy, torch/torchvision (CUDA), easyocr, ultralytics -- versions épinglées
wardenbot/
  __init__.py                 __version__, source de vérité unique de la version
  engine.py                  State, Ability, RotationEngine (le tick loop)
  hooks.py                   logique de combo/rotation par personnage, en clair
  game_config.py              KEY_MAP, REGIONS, CHARACTERS (donnée + wiring par perso)
  config_manager.py           charge config/<profil>.json -> KEY_MAP/REGIONS, validation de profil
  state_update.py             lecture d'écran -> state, par perso (capture/matching/YOLO/OCR)
  freshness.py                classification FRESH/STALE/EXPIRED de l'âge d'une détection (pur, sans dépendance lourde)
  circuit.py                  coupe-circuit auto-pause (pur, stdlib) -- cf. section dédiée
  telemetry.py                journal de décisions (telemetry.jsonl) + incidents coupe-circuit
  kb_engine.py                suggestions de tuning à partir de la télémétrie (human-in-the-loop)
  diagnostics.py              watchdog (appels lents) + log d'erreurs non fatales
  capture.py                 capture d'écran (mss)
  matcher.py                 template matching (OpenCV)
  ocr.py                     lecture de texte/chiffres à l'écran (EasyOCR)
  yolo_detect.py              inférence du modèle YOLO entraîné
  bar_reader.py               lecture du % de remplissage d'une barre par scan de pixels
  input_sim.py                simulation de touches (pydirectinput), timing humanisé, protocole de confirmation
  calibration_wizard.py        assistant interactif de calibration des régions (bouton Calibration du dashboard)
  dpi_awareness.py             déclare le process DPI-aware (Per-Monitor V2 -> V1 -> System Aware)
templates/                    PNG capturés par personnage
dataset_tariq_buffs/          dataset d'entraînement YOLO (images + labels)
runs/detect/                  résultats d'entraînement YOLO (poids -- hors git, cf. section Packaging)
```

### Installation

```
pip install -r requirements.txt
```

`torch`/`torchvision` sont installés depuis l'index CUDA (`cu126`) pour
utiliser le GPU si disponible — sinon EasyOCR/YOLO retombent sur le CPU
(plus lent, mais fonctionnel).

Windows uniquement (`capture_helper.py` et la détection de touches
s'appuient sur `ctypes.windll`, pas de fallback cross-plateforme).

### Distribution (`.exe` packagé, PyInstaller)

```
python -m PyInstaller wardenbot.spec
```

Produit un dossier `dist/wardenbot/` (mode **onedir**, pas onefile — un
onefile ~600MB se ré-auto-extrairait dans un dossier temporaire à chaque
lancement) contenant `wardenbot.exe` (cible : `app.py`, le dashboard GUI) et
un sous-dossier `_internal/` (le "contents directory" par défaut de
PyInstaller 6.x) où vivent `templates/`, `config/`, `ui.html` et les poids
YOLO — **PAS directement à côté de l'exe**, une surprise confirmée par un
vrai crash au premier build. C'est le dossier `dist/wardenbot/` ENTIER (exe
+ `_internal/`) qui se distribue (zip).

`wardenbot/paths.py` distingue deux cas, suite à ce crash et au retour reçu
dessus :
- **`resource_path()`** : ressources bundlées en LECTURE SEULE
  (templates/config/ui/poids) — ancré sur `sys._MEIPASS` (PyInstaller y
  pointe toujours vers le dossier qui contient réellement les `datas=`,
  onedir comme onefile), donc `_internal/`.
- **`data_path()`** : état MUTABLE — `profiles/` (`calibration_wizard.py`
  y écrit un nouveau profil calibré) et `crash.log` — ancré au dossier de
  l'EXÉCUTABLE, jamais à `_internal/` : un profil vivant dans `_internal/`
  serait écrasé au prochain redéploiement du bundle, ou tout simplement pas
  inscriptible si le dossier d'installation ne l'est pas (ex. Program
  Files). Au premier lancement, `config_manager.py` copie les profils par
  défaut bundlés (`_internal/profiles`) vers cet emplacement inscriptible.

Les deux s'ancrent au dossier de l'exécutable/`_internal`, jamais au CWD —
un lancement depuis un raccourci avec un "dossier de démarrage" différent
trouve quand même ses ressources.

Poids YOLO entraînés (`runs/detect/train-5|6/weights/best.pt`) **hors git**
depuis 2026-07-26 (cf. `.gitignore`) — à récupérer en asset de release
séparé pour builder, pas depuis un clone frais. Ceci arrête seulement le
versionnement FUTUR : les 12 fichiers déjà commités avant cette date restent
dans l'historique git de tous les clones existants ; les en retirer pour de
bon exigerait une réécriture d'historique séparée et destructive (`git
filter-repo`), décision indépendante, non prise ici.

**3 risques connus, à lire avant de distribuer** (détaillés en commentaire
de tête de `wardenbot.spec`) :

1. **EasyOCR télécharge ses modèles** (~98MB, `~/.EasyOCR/model/`) au
   premier lancement si absents — ce build n'est PAS autonome hors-ligne
   pour la détection Tariq/Meiko tant que ce téléchargement n'a pas eu lieu
   une fois avec une connexion active.
2. **Antivirus/SmartScreen** : un `.exe` PyInstaller non signé déclenche
   souvent de faux positifs — pas de solution gratuite parfaite (la
   signature de code coûte).
3. **CPU vs GPU** : ce build embarque ~500MB de CUDA (`torch+cu126`). Une
   variante `torch==...+cpu` diviserait la taille par 2-3 si le public cible
   n'a pas forcément de GPU NVIDIA (décision à prendre selon la diffusion
   réelle prévue).

### Avant de lancer en jeu réel

1. Capture les templates PNG nécessaires (`templates/<perso>/`) et calibre
   les régions écran (`capture_helper.py mark`, ou clic droit sur une
   capture d'écran) dans `REGIONS[<perso>]` (`wardenbot/game_config.py`).
2. Remplis `KEY_MAP[<perso>]` (`wardenbot/game_config.py`) avec tes vraies touches.
3. Pour Tariq, si tu ajoutes/recalibres la détection des buffs : construis
   un dataset (`build_yolo_dataset.py`) et entraîne (`train_yolo.py`) avant
   de rebrancher `yolo_detect.detect()`.
4. Lance d'abord avec `dry_run=True` dans `main.py` (juste des `print`,
   aucune touche pressée) pour vérifier que les bonnes abilities sortent au
   bon moment, en comparant à `debug.log`. Passe en `dry_run=False`
   seulement une fois confiant.

### Tests

Portables (aucune dépendance lourde -- écran, GPU, OpenCV -- seuls
`pyyaml`/`numpy` requis ; ce sont ceux du CI, cf.
`.github/workflows/portable-tests.yml`) :

```
python test_wardenbot.py    # Meiko
python test_tariq.py        # Tariq
python test_helena.py       # Helena
python test_saryn.py        # Saryn (Warframe)
python test_vigour.py       # Vigour
python test_config_manager.py
python test_circuit.py      # coupe-circuit (wardenbot/circuit.py)
```

Logique pure, testée contre des `State`/horloges simulés et les vrais YAML
de chaque personnage. Doivent passer avant tout refactor de `hooks.py`,
`engine.py` ou `circuit.py`.

Le reste (`test_dpi_awareness.py`, `test_environment_detect.py`,
`test_region_selector.py`, `test_calibration_engine.py`,
`test_state_update.py`, `test_telemetry.py`, `test_kb_engine.py`,
`test_input_sim.py`, `test_diagnostic_report.py`, `test_calibration_wizard.py`)
a besoin de `ctypes.windll` (Windows), d'un vrai modèle YOLO/EasyOCR (lourd)
et/ou d'un affichage tkinter réel -- restent des tests locaux manuels, pas
dans le CI.

### Pourquoi ces choix techniques

- **mss** : capture d'écran nettement plus rapide que
  `pyautogui.screenshot()` en boucle serrée.
- **OpenCV `cv2.matchTemplate`** : confidence 0.0-1.0 classique, suffisant
  tant que les icônes à distinguer ne se ressemblent pas trop.
- **YOLO (ultralytics)** : quand le template matching confond deux icônes
  au même artwork — un détecteur entraîné apprend la distinction que la
  simple corrélation de pixels ne peut pas faire.
- **EasyOCR** plutôt que Tesseract/PaddleOCR : tourne sur `torch`/CUDA (GPU
  déjà nécessaire pour YOLO) ; PaddleOCR a été écarté faute de wheel
  compatible avec la version de Python de cette machine.
- **pydirectinput** plutôt que `pyautogui` : utilise `SendInput` avec des
  scan codes bas niveau, fiable sur des jeux qui ignorent les événements
  `SendMessage` classiques.

### Détection en thread séparé (Tariq)

`wardenbot/state_update.py::make_tariq_state_update_fn` fait tourner la
capture/matching/YOLO/OCR dans un **thread dédié** (10-15Hz), qui publie un
instantané immuable régulièrement. Le thread principal (jusqu'à 60Hz) ne
fait que lire ce dernier instantané et décider/presser — jamais de calcul
lourd dans la boucle de décision elle-même. Ça évite qu'un pic d'inférence GPU
(YOLO/EasyOCR peuvent occasionnellement prendre 1-3s au lieu de ~20-30ms)
ne bloque toute la rotation.

### Interface graphique (`app.py`)

Dashboard desktop (PyWebView, `ui.html`) qui pilote un vrai `RotationEngine`
dans le même process, en réutilisant tel quel le wiring de `main.py`
(`build_engine`, `make_debug_logger`, `make_error_handler`,
`make_circuit_wiring`) — aucune logique de rotation/détection/input n'est
réimplémentée. `BotController` expose start/pause/stop/changement de
personnage/calibration ; `Api` relaie ça à `window.pywebview.api.*` côté JS.
Lance avec `python app.py`.

### Télémétrie & tuning (`wardenbot/telemetry.py`, `wardenbot/kb_engine.py`)

Journal de décisions **opt-in** (`telemetry.jsonl`, une ligne JSON par
presse RÉELLE, jamais par tick) écrit sur un thread séparé
(`TelemetryWorker`) pour ne jamais bloquer la boucle de décision.
`wardenbot/kb_engine.py` lit ce journal + `press_confirm.log` (protocole de
confirmation des presses, cf. `input_sim.py`) pour **suggérer** des
ajustements de poids par ability — jamais appliqué automatiquement,
toujours une suggestion que l'utilisateur valide à la main
(human-in-the-loop).

### Fraîcheur de détection (`wardenbot/freshness.py`)

Classe l'âge d'une donnée perçue en `FRESH`/`STALE`/`EXPIRED` selon une
`FreshnessPolicy` propre à CHAQUE source (un buff YOLO et une lecture OCR ne
vieillissent pas à la même vitesse). Module pur (pas de dépendance
lourde) : il ne décide jamais quoi FAIRE d'une donnée périmée, seulement si
elle est encore exploitable — la décision reste à l'appelant (`hooks.py`).

### Robustesse

Une exception dans un tick (frame de capture invalide, glitch matcher/YOLO)
ne fait plus planter tout le bot en pleine partie :

- `RotationEngine.run_forever(on_error=...)` rattrape toute exception levée
  par `tick()`, journalise (`main.py` -> `wardenbot/diagnostics.py::log_error`
  -> `error.log`) et continue au tick suivant.
- Le thread de détection Tariq (`wardenbot/state_update.py::_run_detection_worker`)
  applique le même principe autour de chaque cycle de détection : un cycle
  qui échoue est journalisé et retenté, plutôt que de tuer silencieusement
  le thread daemon (ce qui aurait figé `state_update_fn` sur le dernier
  instantané connu indéfiniment, sans aucun crash visible).

#### Coupe-circuit (`wardenbot/circuit.py`)

Une panne qui continue à journaliser indéfiniment sans jamais s'arrêter
n'est PAS de la robustesse — au-delà d'un certain point, le bot doit
s'arrêter et prévenir. `wardenbot/circuit.py` (mécanisme pur, stdlib
uniquement, horloge injectable) déclenche une auto-pause sur 2 critères
indépendants :

1. **Panne franche** : erreurs consécutives sans AUCUN tick réussi pendant
   `max_streak_s` (10s par défaut) — fenêtre temporelle, pas un compteur
   brut, pour rester invariant au `tick_hz` du personnage (Tariq 60Hz vs
   Saryn 15Hz).
2. **Flapping** : ratio d'échecs au-dessus de `flap_error_ratio` (90% par
   défaut) sur une fenêtre glissante `flap_window_s` (30s) — attrape le cas
   où un tick réussit de temps en temps par hasard, ce qui empêcherait le
   critère 1 de se déclencher alors que le bot est fonctionnellement mort.

Au déclenchement : bip (`winsound.Beep`, Windows), pause automatique
(composée en OU logique avec la touche C côté `main.py`, ou pilotant
directement `self.status` côté `app.py`), et une ligne dans
`telemetry.jsonl` (`TelemetryWorker.log_incident` — raison, personnage,
dernière exception, `__version__`). Reprise manuelle (touche C ou bouton
Start) réinitialise le coupe-circuit ; si la panne persiste, il se
redéclenche naturellement quelques secondes plus tard — comportement
attendu, pas un bug.

---

## 🇬🇧 English

> **⚠️ Important** — For now, the bot requires Windows set to **100% DPI
> scaling**. **125% and 150% scaling are not supported** in this version:
> capture/mouse/InputWorker coordinates may be misaligned. We strongly
> recommend staying at 100% while using the bot. Full 125%/150% support will
> come later, once the foundation (JSON config + calibration assistant) is
> solid.

A Python rotation engine for five characters across two games: **Meiko**,
**Tariq**, **Vigour** and **Helena** on **Fellowship**, and **Saryn Prime**
on **Warframe** (a 2-spell assistant, not a full autopilot — cf. the Saryn
section). It replaces opaque decision hooks with Python code you control and
read yourself — the rotation logic is never a black box. Usable from the
console (`main.py`) or via a desktop dashboard (`app.py`, cf. dedicated
section below).

Detection is 100% screen-capture based (never reads game memory): OpenCV
template matching, a trained YOLO detector for buffs that look too similar for
template matching, and EasyOCR for on-screen text/numbers.

### The characters

#### Meiko (`config/warden.yaml`, `hooks.py::meiko_pre_tick`)

Completes an in-progress combo (1st piece → 2nd piece → Fighting Techniques,
a universal off-GCD finisher). The "Spirited Vortex or Spirited Strikes?"
decision at the 3rd stack of Warden of the Temple is a plain, readable
`if`/`else`, not an opaque hook.

#### Tariq (`config/tariq.yaml`, `hooks.py::tariq_pre_tick`)

A classic priority-list rotation (no sequence to complete). Abilities scan
their conditions (`fury_above:N`, `flag:X`, `buff:X`, `debuff:X`) in priority
order every tick. Notable points:

- **Hammer Storm** fires on any detected proc, immediately (no waiting for a
  2nd stack — the game never shows "2" on that badge).
- **Focused Wrath** reduces the Fury cost of the next 2 Hammer Storm/Skull
  Crusher by 50% (mechanic verified on the official wiki) — consumed as soon
  as the reduced threshold is met rather than waiting for the full threshold.
- **Wild Swing** is a pure filler: only when nothing else is available and
  Fury is not already full.
- Buff detection via **YOLO** (`wardenbot/yolo_detect.py`) rather than template
  matching: `hammer_storm_buff` and `skull_crusher_buff` share the same artwork
  and were being confused by `cv2.matchTemplate` — a model trained on a
  semi-auto-labelled dataset (`build_yolo_dataset.py` + `train_yolo.py`)
  separates them correctly, including 3 buffs active at once.

#### Vigour (`config/vigour.yaml`, `hooks.py::vigour_pre_tick_early_game`)

Group healer (the "early game" build, before talent tier 4 — cf.
`game_config.py::CHARACTERS["vigour"]` for the static switch to the
post-tier-4 Legendary Cloak rotation). Priority order: critical heal
(Greater Heal on the lowest ally), cleanse, Circle of Light if 3+ allies are
under 70% HP (targeted on the tank, for lack of a better anchor point), Rune
of Renewal/Dawnbreaker Orb depending on available orbs, Soulbrand for
damage if nobody needs healing, Dawnflare as filler. **Synchronous** read
(no dedicated detection thread, cf. Tariq) — decision and detection are
light enough to fit in the same 30Hz tick.

#### Helena (`config/helena.yaml`, `hooks.py::helena_pre_tick`)

Tank (measured/power/shield), priority-list rotation like Tariq. Cascading
cooldown reductions tracked via `state.hooks` (`HELENA_CDR_MS`):
Measured/Power Strike shorten Shield Slam and Shield Throw, Shield
Slam/Shield Throw shorten Shockwave, Shockwave shortens Shields Up, Hold the
Line resets Shield Slam. Shield Throw and Iron Wall deliberately stay OUT of
the auto-rotation (charge and emergency, manual) — cf. `config/helena.yaml`'s
header comment.

#### Saryn Prime (`config/saryn.yaml`, Warframe — 2026-07-20)

First non-Fellowship character. **A 2-spell assistant (Toxic Lash,
Nourish), deliberately NOT a full autopilot** — aim/movement and the 3rd
spell stay in the user's hands (cf. the YAML's header comment, an explicit
caveat about Warframe's ToS). Each spell refires as soon as its buff has
fallen off AND Energy is above 200 (`energy_above:200` — reads an ABSOLUTE
value via OCR, not a 0-1.0 percentage like `fury_above`/`toughness_above`,
cf. `test_saryn.py`). No shared GCD Fellowship-style: both abilities are
`off_gcd` + `skip_detection` (gated by condition, not by icon template).

### Architecture

```
main.py                    console entry point: build_engine(), debug loop (logger/hotkeys), __main__
app.py                      desktop dashboard (PyWebView) -- reuses main.py's wiring
ui.html                     dashboard UI (calls app.py::Api via window.pywebview.api.*)
CHANGELOG.md                version history (Keep a Changelog)
config/*.yaml                per-character abilities/rotation (warden/tariq/vigour/helena/saryn)
config/*.json, profiles/     KEY_MAP/REGIONS per display profile (resolution/DPI), cf. config_manager.py
test_wardenbot.py, test_tariq.py, test_helena.py, test_saryn.py, test_vigour.py, test_circuit.py
                              pure-logic tests (no screen required, cf. Tests section)
tick_timing_test.py          measures the real duration of a tick (capture+YOLO+OCR+logic)
capture_helper.py            Windows utility: find regions + capture PNG templates
detect_check.py              live display of each template's confidence score
ocr_check.py                 live display of what EasyOCR reads in a region
yolo_check.py                live display of what the YOLO model detects
build_yolo_dataset.py        builds a YOLO dataset from a video clip
train_yolo.py                trains a YOLO model on that dataset
requirements.txt             pyyaml, mss, opencv-python-headless, pydirectinput, pywebview,
                              numpy, torch/torchvision (CUDA), easyocr, ultralytics -- pinned versions
wardenbot/
  __init__.py                 __version__, single source of truth for the version
  engine.py                  State, Ability, RotationEngine (the tick loop)
  hooks.py                   per-character combo/rotation logic, in plain code
  game_config.py              KEY_MAP, REGIONS, CHARACTERS (data + per-character wiring)
  config_manager.py           loads config/<profile>.json -> KEY_MAP/REGIONS, profile validation
  state_update.py             screen read -> state, per character (capture/matching/YOLO/OCR)
  freshness.py                FRESH/STALE/EXPIRED classification of a detection's age (pure, no heavy dependency)
  circuit.py                  auto-pause circuit breaker (pure, stdlib) -- cf. dedicated section
  telemetry.py                decision journal (telemetry.jsonl) + circuit-breaker incidents
  kb_engine.py                tuning suggestions from telemetry (human-in-the-loop)
  diagnostics.py              watchdog (slow calls) + non-fatal error logging
  capture.py                 screen capture (mss)
  matcher.py                 template matching (OpenCV)
  ocr.py                     on-screen text/number reading (EasyOCR)
  yolo_detect.py              inference of the trained YOLO model
  bar_reader.py               reads a bar's fill % by pixel scanning
  input_sim.py                keypress simulation (pydirectinput), humanized timing, confirmation protocol
  calibration_wizard.py        interactive region-calibration assistant (dashboard's Calibration button)
  dpi_awareness.py             declares the process DPI-aware (Per-Monitor V2 -> V1 -> System Aware)
templates/                    PNGs captured per character
dataset_tariq_buffs/          YOLO training dataset (images + labels)
runs/detect/                  YOLO training results (weights -- out of git, cf. Packaging section)
```

### Installation

```
pip install -r requirements.txt
```

`torch`/`torchvision` are installed from the CUDA index (`cu126`) to use the
GPU if available — otherwise EasyOCR/YOLO fall back to the CPU (slower, but
functional).

Windows only (`capture_helper.py` and key detection rely on `ctypes.windll`,
no cross-platform fallback).

### Distribution (packaged `.exe`, PyInstaller)

```
python -m PyInstaller wardenbot.spec
```

Produces a `dist/wardenbot/` folder (**onedir** mode, not onefile — a
~600MB onefile would re-self-extract to a temp folder on every launch)
containing `wardenbot.exe` (target: `app.py`, the GUI dashboard) and an
`_internal/` subfolder (PyInstaller 6.x's default "contents directory")
where `templates/`, `config/`, `ui.html` and the YOLO weights live — **NOT
directly beside the exe**, a surprise confirmed by a real crash on the
first build. It's the ENTIRE `dist/wardenbot/` folder (exe + `_internal/`)
that gets distributed (zipped).

`wardenbot/paths.py` distinguishes two cases, following that crash and the
feedback on it:
- **`resource_path()`**: READ-ONLY bundled resources
  (templates/config/ui/weights) — anchored on `sys._MEIPASS` (PyInstaller
  always points it at the folder that actually holds `datas=`, onedir or
  onefile), i.e. `_internal/`.
- **`data_path()`**: MUTABLE state — `profiles/` (`calibration_wizard.py`
  writes a newly-calibrated profile there) and `crash.log` — anchored to
  the EXECUTABLE's own folder, never `_internal/`: a profile living in
  `_internal/` would get overwritten on the next bundle redeployment, or
  simply be unwritable if the install folder isn't (e.g. Program Files).
  On first launch, `config_manager.py` copies the bundled default profiles
  (`_internal/profiles`) to this writable location.

Both anchor to the executable's folder/`_internal`, never the CWD — a
launch from a shortcut with a different "start in" folder still finds its
resources.

Trained YOLO weights (`runs/detect/train-5|6/weights/best.pt`) are **out of
git** as of 2026-07-26 (cf. `.gitignore`) — fetch them from a separate
release asset to build, not from a fresh clone. This only stops FUTURE
tracking: the 12 files already committed before that date remain in every
existing clone's git history; actually removing them would require a
separate, destructive history rewrite (`git filter-repo`), an independent
decision not taken here.

**3 known risks, read before distributing** (detailed in `wardenbot.spec`'s
header comment):

1. **EasyOCR downloads its models** (~98MB, `~/.EasyOCR/model/`) on first
   launch if absent — this build is NOT offline-autonomous for
   Tariq/Meiko detection until that download has happened once with an
   active connection.
2. **Antivirus/SmartScreen**: an unsigned PyInstaller `.exe` routinely
   triggers false positives — no free perfect fix (code signing costs
   money).
3. **CPU vs GPU**: this build embeds ~500MB of CUDA (`torch+cu126`). A
   `torch==...+cpu` variant would cut the size 2-3x if the target audience
   doesn't necessarily have an NVIDIA GPU (decision depends on actual
   intended distribution).

### Before running in a real game

1. Capture the needed PNG templates (`templates/<character>/`) and calibrate
   the screen regions (`capture_helper.py mark`, or right-click on a
   screenshot) into `REGIONS[<character>]` (`wardenbot/game_config.py`).
2. Fill `KEY_MAP[<character>]` (`wardenbot/game_config.py`) with your real keys.
3. For Tariq, if you add/recalibrate buff detection: build a dataset
   (`build_yolo_dataset.py`) and train (`train_yolo.py`) before re-wiring
   `yolo_detect.detect()`.
4. First run with `dry_run=True` in `main.py` (just `print`s, no key pressed)
   to check that the right abilities come out at the right time, comparing
   against `debug.log`. Switch to `dry_run=False` only once confident.

### Tests

Portable (no heavy dependency -- screen, GPU, OpenCV -- only
`pyyaml`/`numpy` required; these are the CI ones, cf.
`.github/workflows/portable-tests.yml`):

```
python test_wardenbot.py    # Meiko
python test_tariq.py        # Tariq
python test_helena.py       # Helena
python test_saryn.py        # Saryn (Warframe)
python test_vigour.py       # Vigour
python test_config_manager.py
python test_circuit.py      # circuit breaker (wardenbot/circuit.py)
```

Pure logic, tested against simulated `State`s/clocks and each character's
real YAML. Must pass before any refactor of `hooks.py`, `engine.py` or
`circuit.py`.

The rest (`test_dpi_awareness.py`, `test_environment_detect.py`,
`test_region_selector.py`, `test_calibration_engine.py`,
`test_state_update.py`, `test_telemetry.py`, `test_kb_engine.py`,
`test_input_sim.py`, `test_diagnostic_report.py`, `test_calibration_wizard.py`)
needs `ctypes.windll` (Windows), a real YOLO/EasyOCR model (heavy), and/or a
real tkinter display -- these stay local manual tests, not in CI.

### Why these technical choices

- **mss**: screen capture significantly faster than `pyautogui.screenshot()`
  in a tight loop.
- **OpenCV `cv2.matchTemplate`**: classic 0.0-1.0 confidence, enough as long
  as the icons to distinguish don't look too similar.
- **YOLO (ultralytics)**: when template matching confuses two icons with the
  same artwork — a trained detector learns the distinction that plain pixel
  correlation cannot.
- **EasyOCR** rather than Tesseract/PaddleOCR: runs on `torch`/CUDA (GPU
  already needed for YOLO); PaddleOCR was ruled out for lack of a wheel
  compatible with this machine's Python version.
- **pydirectinput** rather than `pyautogui`: uses `SendInput` with low-level
  scan codes, reliable on games that ignore classic `SendMessage` events.

### Detection on a separate thread (Tariq)

`wardenbot/state_update.py::make_tariq_state_update_fn` runs
capture/matching/YOLO/OCR on a **dedicated thread** (10-15Hz), which
publishes an immutable snapshot regularly. The main thread (up to 60Hz) only
reads that latest snapshot and decides/presses — never any heavy computation
in the decision loop itself. This prevents a GPU inference spike (YOLO/EasyOCR
can occasionally take 1-3s instead of ~20-30ms) from blocking the whole
rotation.

### Desktop dashboard (`app.py`)

A PyWebView desktop dashboard (`ui.html`) that drives a real
`RotationEngine` in the same process, reusing `main.py`'s wiring as-is
(`build_engine`, `make_debug_logger`, `make_error_handler`,
`make_circuit_wiring`) — no rotation/detection/input logic is
reimplemented. `BotController` exposes start/pause/stop/character
switch/calibration; `Api` relays that to `window.pywebview.api.*` on the JS
side. Launch with `python app.py`.

### Telemetry & tuning (`wardenbot/telemetry.py`, `wardenbot/kb_engine.py`)

An **opt-in** decision journal (`telemetry.jsonl`, one JSON line per REAL
press, never per tick) written on a separate thread (`TelemetryWorker`) so
it never blocks the decision loop. `wardenbot/kb_engine.py` reads this
journal + `press_confirm.log` (the press confirmation protocol, cf.
`input_sim.py`) to **suggest** per-ability weight adjustments — never
applied automatically, always a suggestion the user reviews by hand
(human-in-the-loop).

### Detection freshness (`wardenbot/freshness.py`)

Classifies the age of a perceived piece of data into
`FRESH`/`STALE`/`EXPIRED` according to a `FreshnessPolicy` specific to EACH
source (a YOLO buff and an OCR reading don't age at the same speed). A pure
module (no heavy dependency): it never decides what to DO with stale data,
only whether it's still usable — the decision stays with the caller
(`hooks.py`).

### Robustness

An exception in a tick (invalid capture frame, matcher/YOLO glitch) no longer
crashes the whole bot mid-game:

- `RotationEngine.run_forever(on_error=...)` catches any exception raised by
  `tick()`, logs it (`main.py` -> `wardenbot/diagnostics.py::log_error` ->
  `error.log`) and continues at the next tick.
- The Tariq detection thread (`wardenbot/state_update.py::_run_detection_worker`)
  applies the same principle around each detection cycle: a failing cycle is
  logged and retried, rather than silently killing the daemon thread (which
  would have frozen `state_update_fn` on the last known snapshot indefinitely,
  with no visible crash).

#### Circuit breaker (`wardenbot/circuit.py`)

A fault that keeps logging forever without ever stopping is NOT robustness
— past a certain point, the bot must stop and warn. `wardenbot/circuit.py`
(pure mechanism, stdlib only, injectable clock) triggers an auto-pause on 2
independent criteria:

1. **Hard failure**: consecutive errors with NO successful tick at all for
   `max_streak_s` (10s by default) — a time window, not a raw count, to
   stay invariant across character `tick_hz` (Tariq 60Hz vs Saryn 15Hz).
2. **Flapping**: an error ratio above `flap_error_ratio` (90% by default)
   over a sliding `flap_window_s` (30s) window — catches the case where a
   tick occasionally succeeds by chance, which would prevent criterion 1
   from ever firing even though the bot is functionally dead.

On trip: a beep (`winsound.Beep`, Windows), an automatic pause (composed
via logical OR with the C hotkey on `main.py`'s side, or directly driving
`self.status` on `app.py`'s side), and a line in `telemetry.jsonl`
(`TelemetryWorker.log_incident` — reason, character, last exception,
`__version__`). A manual resume (C key or Start button) resets the circuit
breaker; if the fault persists, it naturally re-trips a few seconds later —
intended behavior, not a bug.
