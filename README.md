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

Moteur de rotation Python pour deux personnages sur **Fellowship** :
**Meiko** (Warden, combo defensif) et **Tariq** (Spam V2, rotation en liste
de priorité). Remplace des hooks de décision opaques par du code Python que
tu contrôles et lis toi-même — la logique de rotation n'est jamais une
boîte noire.

Détection 100% par capture d'écran (jamais de lecture mémoire du jeu) :
template matching OpenCV, un détecteur YOLO entraîné pour les buffs qui se
ressemblent trop pour le template matching, et EasyOCR pour le texte/les
chiffres à l'écran.

### Les deux personnages

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

### Architecture

```
main.py                    point d'entrée : build_engine(), boucle de debug (logger/hotkeys), __main__
config/warden.yaml           les abilities de Meiko (combo + hors-combo)
config/tariq.yaml            la rotation de Tariq (liste de priorité)
test_wardenbot.py            tests de logique pure Meiko (aucun écran requis)
test_tariq.py                tests de logique pure Tariq (aucun écran requis)
tick_timing_test.py          mesure la durée réelle d'un tick (capture+YOLO+OCR+logique)
capture_helper.py            utilitaire Windows : trouver régions + capturer templates PNG
detect_check.py              affiche en direct le score de confiance de chaque template
ocr_check.py                 affiche en direct ce qu'EasyOCR lit dans une région
yolo_check.py                affiche en direct ce que le modèle YOLO détecte
build_yolo_dataset.py        construit un dataset YOLO à partir d'un clip vidéo
train_yolo.py                entraîne un modèle YOLO sur ce dataset
requirements.txt             pyyaml, mss, opencv-python-headless, pydirectinput,
                              numpy, torch/torchvision (CUDA), easyocr, ultralytics
wardenbot/
  engine.py                  State, Ability, RotationEngine (le tick loop)
  hooks.py                   logique de combo/rotation par personnage, en clair
  game_config.py              KEY_MAP, REGIONS, CHARACTERS (donnée + wiring par perso)
  state_update.py             lecture d'écran -> state, par perso (capture/matching/YOLO)
  diagnostics.py              watchdog (appels lents) + log d'erreurs non fatales
  capture.py                 capture d'écran (mss)
  matcher.py                 template matching (OpenCV)
  ocr.py                     lecture de texte/chiffres à l'écran (EasyOCR)
  yolo_detect.py              inférence du modèle YOLO entraîné
  bar_reader.py               lecture du % de remplissage d'une barre par scan de pixels
  input_sim.py                simulation de touches (pydirectinput), timing humanisé
templates/                    PNG capturés par personnage
dataset_tariq_buffs/          dataset d'entraînement YOLO (images + labels)
runs/detect/                  résultats d'entraînement YOLO (poids inclus)
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

```
python test_wardenbot.py
python test_tariq.py
```

Aucune dépendance lourde (écran, GPU, OpenCV) : logique pure, testée contre
des `State` simulés et le vrai `config/tariq.yaml`/`config/warden.yaml`.
Doivent passer avant tout refactor de `hooks.py` ou `engine.py`.

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

---

## 🇬🇧 English

> **⚠️ Important** — For now, the bot requires Windows set to **100% DPI
> scaling**. **125% and 150% scaling are not supported** in this version:
> capture/mouse/InputWorker coordinates may be misaligned. We strongly
> recommend staying at 100% while using the bot. Full 125%/150% support will
> come later, once the foundation (JSON config + calibration assistant) is
> solid.

A Python rotation engine for two characters in **Fellowship**: **Meiko**
(Warden, defensive combo) and **Tariq** (Spam V2, priority-list rotation). It
replaces opaque decision hooks with Python code you control and read yourself
— the rotation logic is never a black box.

Detection is 100% screen-capture based (never reads game memory): OpenCV
template matching, a trained YOLO detector for buffs that look too similar for
template matching, and EasyOCR for on-screen text/numbers.

### The two characters

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

### Architecture

```
main.py                    entry point: build_engine(), debug loop (logger/hotkeys), __main__
config/warden.yaml           Meiko's abilities (combo + non-combo)
config/tariq.yaml            Tariq's rotation (priority list)
test_wardenbot.py            pure-logic tests for Meiko (no screen required)
test_tariq.py                pure-logic tests for Tariq (no screen required)
tick_timing_test.py          measures the real duration of a tick (capture+YOLO+OCR+logic)
capture_helper.py            Windows utility: find regions + capture PNG templates
detect_check.py              live display of each template's confidence score
ocr_check.py                 live display of what EasyOCR reads in a region
yolo_check.py                live display of what the YOLO model detects
build_yolo_dataset.py        builds a YOLO dataset from a video clip
train_yolo.py                trains a YOLO model on that dataset
requirements.txt             pyyaml, mss, opencv-python-headless, pydirectinput,
                              numpy, torch/torchvision (CUDA), easyocr, ultralytics
wardenbot/
  engine.py                  State, Ability, RotationEngine (the tick loop)
  hooks.py                   per-character combo/rotation logic, in plain code
  game_config.py              KEY_MAP, REGIONS, CHARACTERS (data + per-character wiring)
  state_update.py             screen read -> state, per character (capture/matching/YOLO)
  diagnostics.py              watchdog (slow calls) + non-fatal error logging
  capture.py                 screen capture (mss)
  matcher.py                 template matching (OpenCV)
  ocr.py                     on-screen text/number reading (EasyOCR)
  yolo_detect.py              inference of the trained YOLO model
  bar_reader.py               reads a bar's fill % by pixel scanning
  input_sim.py                keypress simulation (pydirectinput), humanized timing
templates/                    PNGs captured per character
dataset_tariq_buffs/          YOLO training dataset (images + labels)
runs/detect/                  YOLO training results (weights included)
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

```
python test_wardenbot.py
python test_tariq.py
```

No heavy dependency (screen, GPU, OpenCV): pure logic, tested against
simulated `State`s and the real `config/tariq.yaml`/`config/warden.yaml`.
Must pass before any refactor of `hooks.py` or `engine.py`.

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
