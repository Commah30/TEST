# wardenbot

> **⚠️ Important (FR)** — Pour le moment, le bot nécessite Windows configuré
> à **100% DPI scaling**. Les scaling **125% et 150% ne sont pas supportés**
> dans cette version : les coordonnées de capture/souris/InputWorker
> peuvent être décalées. Nous recommandons fortement de rester en 100%
> pendant l'utilisation. Le support complet 125%/150% arrivera plus tard,
> une fois la base (config JSON + assistant de calibration) solide.
>
> **⚠️ Important (EN)** — For now, the bot requires Windows set to **100%
> DPI scaling**. **125% and 150% scaling are not supported** in this
> version: capture/mouse/InputWorker coordinates may be misaligned. We
> strongly recommend staying at 100% while using the bot. Full 125%/150%
> support will come later, once the foundation (JSON config + calibration
> assistant) is solid.

Moteur de rotation Python pour deux personnages sur **Fellowship** :
**Meiko** (Warden, combo defensif) et **Tariq** (Spam V2, rotation en liste
de priorité). Remplace des hooks de décision opaques par du code Python que
tu contrôles et lis toi-même — la logique de rotation n'est jamais une
boîte noire.

Détection 100% par capture d'écran (jamais de lecture mémoire du jeu) :
template matching OpenCV, un détecteur YOLO entraîné pour les buffs qui se
ressemblent trop pour le template matching, et EasyOCR pour le texte/les
chiffres à l'écran.

## Les deux personnages

### Meiko (`config/warden.yaml`, `hooks.py::meiko_pre_tick`)

Complète un combo en cours (1re pièce → 2e pièce → Fighting Techniques,
finisher universel off-GCD). La décision "Spirited Vortex ou Spirited
Strikes ?" au 3e stack de Warden of the Temple est un simple `if`/`else`
lisible, pas un hook opaque.

### Tariq (`config/tariq.yaml`, `hooks.py::tariq_pre_tick`)

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

## Architecture

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

## Installation

```
pip install -r requirements.txt
```

`torch`/`torchvision` sont installés depuis l'index CUDA (`cu126`) pour
utiliser le GPU si disponible — sinon EasyOCR/YOLO retombent sur le CPU
(plus lent, mais fonctionnel).

Windows uniquement (`capture_helper.py` et la détection de touches
s'appuient sur `ctypes.windll`, pas de fallback cross-plateforme).

## Avant de lancer en jeu réel

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

## Tests

```
python test_wardenbot.py
python test_tariq.py
```

Aucune dépendance lourde (écran, GPU, OpenCV) : logique pure, testée contre
des `State` simulés et le vrai `config/tariq.yaml`/`config/warden.yaml`.
Doivent passer avant tout refactor de `hooks.py` ou `engine.py`.

## Pourquoi ces choix techniques

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

## Détection en thread séparé (Tariq)

`wardenbot/state_update.py::make_tariq_state_update_fn` fait tourner la
capture/matching/YOLO/OCR dans un **thread dédié** (10-15Hz), qui publie un
instantané immuable régulièrement. Le thread principal (jusqu'à 60Hz) ne
fait que lire ce dernier instantané et décider/presser — jamais de calcul
lourd dans la boucle de décision elle-même. Ça évite qu'un pic d'inférence GPU
(YOLO/EasyOCR peuvent occasionnellement prendre 1-3s au lieu de ~20-30ms)
ne bloque toute la rotation.

## Robustesse

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
