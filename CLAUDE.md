# wardenbot — contexte projet · project context

*🇫🇷 Version française d'abord · 🇬🇧 English version below*

---

## 🇫🇷 Français

Moteur de rotation Python pour **Meiko / build "Warden"** (Monk tank) sur le
jeu **Fellowship**. Remplace la partie "décision de combo", auparavant gérée
via des hooks Python opaques (`meiko_combo_pre_tick` /
`meiko_combo_post_tick`) auxquels l'utilisateur n'avait pas accès (et dont le
mainteneur est surbooké), par du code Python que l'utilisateur possède et peut
éditer directement. Objectif d'origine : garantir
qu'un combo précis (Spirited Vortex) soit prioritaire sur un autre
(Spirited Strikes) quand un debuff atteint 3 stacks — impossible à garantir
avec les hooks opaques, trivial avec du code qu'on lit soi-même.

### Mécanique de jeu (Meiko)

3 abilities "starter" : **Spirit Palm (SP)**, **Wind Kick (WK)**, **Earth
Fist (EF)**. Chaque combo = 1re pièce (ouvreur) + 2e pièce + **Fighting
Techniques** (finisher universel, off-GCD, icône qui "morphe" selon le
combo donc AUCUN template statique fiable dessus).

Table des combos :

| 1re pièce | 2e pièce | Résultat |
|---|---|---|
| Spirit Palm | Wind Kick | Double Palm Strike (défensif, DR/Parry) |
| Spirit Palm | Earth Fist | **Spirited Vortex** (défensif, Dodge/AoE DoT/slow) |
| Wind Kick | Spirit Palm | Rising Storm (build charges Stormfall) |
| Wind Kick | Earth Fist | **Lashing Stormkick** (spend Stormfall, AoE) — stack le debuff Warden of the Temple |
| Earth Fist | Spirit Palm | Rising Earth (build charges Earthfall) |
| Earth Fist | Wind Kick | Earthfist Barrage (spend Earthfall, ST) |

**Warden of the Temple** : debuff qui stack jusqu'à 3 via Lashing
Stormkick. Icône affiche un badge "x3" à 3 stacks (visuellement distinct,
capturable comme template à part : `warden_of_the_temple_3`). À 3 stacks,
le prochain Spirited Vortex obtient +50% dégâts / +30% dodge — **règle
métier centrale : à 3 stacks, priorité absolue à Spirited Vortex sur
Spirited Strikes**, implémentée et testée dans `hooks.py`.

`gcd_ms = 1100` — mesuré empiriquement par l'utilisateur (capture 30fps de la
barre d'abilities), ne pas changer sans nouvelle mesure.

### Architecture

```
main.py                    point d'entrée : build_engine(), boucle de debug (logger/hotkeys), __main__
config/warden.yaml        les 5 abilities scannées par priorité normale
test_wardenbot.py         tests de logique pure (aucun écran requis)
capture_helper.py         utilitaire Windows (ctypes) : trouver régions + capturer templates PNG
detect_check.py           affiche en direct le score de confiance de chaque template
requirements.txt          pyyaml, mss, opencv-python-headless, pydirectinput, numpy, torch/torchvision (CUDA cu126), easyocr, ultralytics
wardenbot/
  engine.py                 State, Ability, RotationEngine (le tick loop)
  hooks.py                  *** LA logique combo Meiko, en Python lisible ***
  game_config.py             KEY_MAP, REGIONS, CHARACTERS (donnée + wiring par perso)
  state_update.py            lecture d'écran -> state, par perso (extrait de main.py le 2026-07-12)
  diagnostics.py             watchdog (appels lents) + log_error (exceptions non fatales)
  capture.py                 capture d'écran (mss)
  matcher.py                 template matching (OpenCV, seuil défaut 0.70)
  input_sim.py                simulation de touches (pydirectinput, PAS pyautogui)
templates/                   PNG capturés (pas versionnés a priori)
```

Le projet couvre aussi un second personnage, **Tariq** (`config/tariq.yaml`,
`hooks.py::tariq_pre_tick`, YOLO + EasyOCR pour la détection) — cf.
README.md pour le détail, ce fichier CLAUDE.md documente surtout la
mécanique Meiko d'origine du projet.

#### `wardenbot/engine.py`
- `State` : last_cast, last_cast_time, cast_history, cooldown_until,
  gcd_until, flags, debuffs, buffs, resources, health_pct, mode ("st"/"aoe").
- `Ability` : name, priority_st/priority_aoe, condition, cooldown_ms,
  off_gcd, skip_detection, requires_ready, template.
- `RotationEngine.tick()` : appelle `state_update_fn` (lecture écran) →
  `pre_tick_hook` (peut court-circuiter le scan normal) → sinon scan de
  priorité normal → `post_tick_hook`.
- `off_gcd_hook_abilities` (frozenset, défaut `{"fighting_techniques"}`) :
  noms pressés par le hook qui NE posent PAS de GCD. Tout le reste pressé
  par le hook pose un GCD comme une ability normale.

#### `wardenbot/hooks.py` — le fichier important
`meiko_pre_tick(state)` implémente toute la logique de combo en if/else
lisible. `meiko_post_tick` est actuellement un no-op (l'état des buffs
vient de la détection écran, pas d'une estimation).

Convention des noms : `wind_kick_2` / `earth_fist_2` / `spirit_palm_2`
désignent la 2e pièce d'un combo (même touche physique que la version sans
suffixe, cf. `KEY_MAP`). Utile car Wind Kick/Earth Fist/Spirit Palm jouent
chacun un rôle différent selon le contexte (ouvreur vs 2e pièce).

#### `wardenbot/game_config.py` (anciennement dans `main.py`, extrait le 2026-07-12)
`KEY_MAP` (nom logique → touche réelle) et `REGIONS` (nom logique → dict
région écran `{left, top, width, height}`) sont **édités à la main par
l'utilisateur au fur et à mesure des captures — ne jamais écraser ce fichier en
entier, toujours diff/merge**. `CHARACTERS` relie chaque perso à son YAML,
ses templates, ses hooks et sa fabrique de `state_update_fn` (celles-ci
vivent dans `wardenbot/state_update.py`, qui lit l'écran à chaque tick/cycle
et remplit `state.resources`/`state.debuffs`/`state.buffs`).

#### Robustesse (ajouté le 2026-07-12)
`RotationEngine.run_forever(on_error=...)` rattrape toute exception levée
par `tick()` (frame de capture invalide, glitch matcher/YOLO...) au lieu de
laisser planter tout le bot en pleine partie — `main.py` journalise via
`wardenbot/diagnostics.py::log_error` (fichier `error.log`, gitignored) et
la boucle continue au tick suivant. Même principe côté thread de détection
Tariq (`wardenbot/state_update.py::_run_detection_worker`) : un cycle qui
échoue est journalisé et retenté, plutôt que de tuer silencieusement le
thread daemon (ce qui gèlerait `state_update_fn` sur le dernier snapshot
connu indéfiniment, sans aucun crash visible — un des pièges "ça a l'air
figé sans raison" déjà chassés sur ce projet).

### Bugs déjà rencontrés et corrigés (ne pas réintroduire)

1. **"Spam Wind Kick" en boucle** — `pre_tick_hook` décidait la suite du
   combo ~30ms après la 1re pièce, bien avant que le jeu ait fini son GCD
   de 1.1s ; le press partait mais le jeu l'ignorait. **Fix** : gate GCD
   explicite dans `meiko_pre_tick` (`if now < state.gcd_until: return
   None`), + `engine.py` pose maintenant un GCD après les presses
   déclenchées par le hook (sauf celles dans `off_gcd_hook_abilities`).

2. **Fighting Techniques presse mais le sort ne part pas** — le finisher
   partait ~30ms après la 2e pièce, probablement trop tôt pour que le jeu
   ait enregistré cette 2e pièce comme complétant un combo valide. **Fix** :
   `FINISHER_DELAY_S = 0.15` (150ms tampon) dans `hooks.py` avant de
   presser le finisher.

3. **Combo défensif qui spam / boucle infinie, Storm loop ne tourne
   jamais** — root cause : `meiko_post_tick` ESTIMAIT la durée restante des
   buffs Spirited Vortex/Strikes via un timer décrémenté, initialisé à 0.0
   au démarrage du bot indépendamment de l'état réel en jeu → le hook
   croyait quasi en permanence qu'un des deux buffs avait besoin d'un
   refresh, et ne laissait jamais la boucle Storm/DPS s'exécuter. **Fix** :
   remplacé l'estimation par une vraie détection écran. `state.buffs["spirited_vortex"]`
   / `["spirited_strikes"]` sont lus à chaque tick via `state_update_fn`
   (templates `buff_spirited_vortex` / `buff_spirited_strikes` sur la barre
   de buffs). `meiko_pre_tick` lit `state.buffs.get(...)` directement.

**Principe général qui a émergé de ces 3 bugs** : ne jamais estimer un état
qui peut être détecté directement à l'écran. Un timer/estimation dérive et
ne connaît pas l'état réel au démarrage ; une détection template lit l'état
tel qu'il est, à chaque tick.

### État actuel (à vérifier/mettre à jour, ça évolue vite)

Templates capturés à ce stade : `spirit_palm`, `wind_kick`, `earth_fist`,
`fighting_techniques` (capturé mais **non utilisé pour la détection** —
l'icône "morphe", le moteur presse ce nom à l'aveugle via le hook, sans
jamais vérifier de template dessus), `stormfall_lit`, `warden_of_the_temple_3`.

Restent à capturer/vérifier : `earthfall_lit`, `buff_spirited_vortex`,
`buff_spirited_strikes` (support tout juste ajouté côté code, pas encore
validé en jeu réel au moment de ce résumé).

`stone_shield` (condition `flag:need_shield`) et `serenity` (condition
`health_below:60`) sont définis dans `config/warden.yaml` mais **ne se
déclencheront jamais en l'état** : rien ne pose `state.flags["need_shield"]`
ni ne met à jour `state.health_pct` (reste à 100.0 par défaut). C'est un
gap connu et volontairement reporté, pas un bug à corriger par réflexe.

### Contraintes / conventions

- **Windows uniquement** (l'utilisateur utilise PowerShell ; `capture_helper.py`
  s'appuie sur `ctypes.windll`, pas de fallback cross-platform prévu).
- Seuil de confiance template matching par défaut : **0.70**.
- Input simulation : **pydirectinput**, pas pyautogui (fiabilité en jeu).
- `test_wardenbot.py` doit rester exécutable sans écran ni dépendances
  lourdes (seul PyYAML requis) — ne pas lui faire importer mss/cv2/pydirectinput.
- `python test_wardenbot.py` doit passer avant tout refactor de `hooks.py`
  ou `engine.py`.

### Pistes ouvertes / suite possible

- Valider en jeu réel la détection des 2 buffs + earthfall_lit fraîchement
  branchée (fix du bug #3).
- Détection de charges pour Stone Shield (lecture d'un badge-chiffre à
  l'écran) + santé réelle pour Serenity, si l'utilisateur veut que ces deux
  abilities s'activent un jour.
- Portage éventuel du même moteur pour d'autres personnages de l'utilisateur
  (Tariq, Helena) — non commencé, discuté seulement comme piste.

---

## 🇬🇧 English

A Python rotation engine for **Meiko / "Warden" build** (Monk tank) in the
game **Fellowship**. It replaces the "combo decision" part, previously handled
by opaque Python hooks (`meiko_combo_pre_tick` / `meiko_combo_post_tick`) that
l'utilisateur had no access to (and whose maintainer is overbooked), with Python code
l'utilisateur owns and can edit directly. Original goal: guarantee that a specific
combo (Spirited Vortex) takes priority over another (Spirited Strikes) when a
debuff reaches 3 stacks — impossible to guarantee with the opaque hooks,
trivial with code you read yourself.

### Game mechanics (Meiko)

3 "starter" abilities: **Spirit Palm (SP)**, **Wind Kick (WK)**, **Earth Fist
(EF)**. Each combo = 1st piece (opener) + 2nd piece + **Fighting Techniques**
(universal finisher, off-GCD, an icon that "morphs" depending on the combo, so
NO reliable static template on it).

Combo table:

| 1st piece | 2nd piece | Result |
|---|---|---|
| Spirit Palm | Wind Kick | Double Palm Strike (defensive, DR/Parry) |
| Spirit Palm | Earth Fist | **Spirited Vortex** (defensive, Dodge/AoE DoT/slow) |
| Wind Kick | Spirit Palm | Rising Storm (builds Stormfall charges) |
| Wind Kick | Earth Fist | **Lashing Stormkick** (spends Stormfall, AoE) — stacks the Warden of the Temple debuff |
| Earth Fist | Spirit Palm | Rising Earth (builds Earthfall charges) |
| Earth Fist | Wind Kick | Earthfist Barrage (spends Earthfall, ST) |

**Warden of the Temple**: a debuff that stacks up to 3 via Lashing Stormkick.
The icon shows an "x3" badge at 3 stacks (visually distinct, capturable as a
separate template: `warden_of_the_temple_3`). At 3 stacks, the next Spirited
Vortex gets +50% damage / +30% dodge — **central business rule: at 3 stacks,
absolute priority to Spirited Vortex over Spirited Strikes**, implemented and
tested in `hooks.py`.

`gcd_ms = 1100` — measured empirically by l'utilisateur (30fps capture of the ability
bar), do not change without a new measurement.

### Architecture

```
main.py                    entry point: build_engine(), debug loop (logger/hotkeys), __main__
config/warden.yaml        the 5 abilities scanned by normal priority
test_wardenbot.py         pure-logic tests (no screen required)
capture_helper.py         Windows utility (ctypes): find regions + capture PNG templates
detect_check.py           live display of each template's confidence score
requirements.txt          pyyaml, mss, opencv-python-headless, pydirectinput, numpy, torch/torchvision (CUDA cu126), easyocr, ultralytics
wardenbot/
  engine.py                 State, Ability, RotationEngine (the tick loop)
  hooks.py                  *** THE Meiko combo logic, in readable Python ***
  game_config.py             KEY_MAP, REGIONS, CHARACTERS (data + per-character wiring)
  state_update.py            screen read -> state, per character (extracted from main.py on 2026-07-12)
  diagnostics.py             watchdog (slow calls) + log_error (non-fatal exceptions)
  capture.py                 screen capture (mss)
  matcher.py                 template matching (OpenCV, default threshold 0.70)
  input_sim.py                keypress simulation (pydirectinput, NOT pyautogui)
templates/                   captured PNGs (not versioned a priori)
```

The project also covers a second character, **Tariq** (`config/tariq.yaml`,
`hooks.py::tariq_pre_tick`, YOLO + EasyOCR for detection) — see README.md for
the details; this CLAUDE.md mainly documents the project's original Meiko
mechanics.

#### `wardenbot/engine.py`
- `State`: last_cast, last_cast_time, cast_history, cooldown_until, gcd_until,
  flags, debuffs, buffs, resources, health_pct, mode ("st"/"aoe").
- `Ability`: name, priority_st/priority_aoe, condition, cooldown_ms, off_gcd,
  skip_detection, requires_ready, template.
- `RotationEngine.tick()`: calls `state_update_fn` (screen read) →
  `pre_tick_hook` (can short-circuit the normal scan) → otherwise the normal
  priority scan → `post_tick_hook`.
- `off_gcd_hook_abilities` (frozenset, default `{"fighting_techniques"}`):
  names pressed by the hook that do NOT set a GCD. Everything else pressed by
  the hook sets a GCD like a normal ability.

#### `wardenbot/hooks.py` — the important file
`meiko_pre_tick(state)` implements all the combo logic in readable if/else.
`meiko_post_tick` is currently a no-op (buff state comes from screen detection,
not from an estimate).

Naming convention: `wind_kick_2` / `earth_fist_2` / `spirit_palm_2` denote the
2nd piece of a combo (same physical key as the suffix-less version, cf.
`KEY_MAP`). Useful because Wind Kick/Earth Fist/Spirit Palm each play a
different role depending on context (opener vs 2nd piece).

#### `wardenbot/game_config.py` (formerly in `main.py`, extracted on 2026-07-12)
`KEY_MAP` (logical name → real key) and `REGIONS` (logical name → screen-region
dict `{left, top, width, height}`) are **hand-edited by l'utilisateur as captures
progress — never overwrite this file wholesale, always diff/merge**.
`CHARACTERS` links each character to its YAML, templates, hooks and its
`state_update_fn` factory (these live in `wardenbot/state_update.py`, which
reads the screen each tick/cycle and fills
`state.resources`/`state.debuffs`/`state.buffs`).

#### Robustness (added on 2026-07-12)
`RotationEngine.run_forever(on_error=...)` catches any exception raised by
`tick()` (invalid capture frame, matcher/YOLO glitch...) instead of letting the
whole bot crash mid-game — `main.py` logs via
`wardenbot/diagnostics.py::log_error` (`error.log` file, gitignored) and the
loop continues at the next tick. Same principle on the Tariq detection thread
(`wardenbot/state_update.py::_run_detection_worker`): a failing cycle is logged
and retried, rather than silently killing the daemon thread (which would freeze
`state_update_fn` on the last known snapshot indefinitely, with no visible
crash — one of the "looks frozen for no reason" traps already hunted down on
this project).

### Bugs already encountered and fixed (do not reintroduce)

1. **"Spam Wind Kick" loop** — `pre_tick_hook` decided the combo continuation
   ~30ms after the 1st piece, well before the game had finished its 1.1s GCD;
   the press was sent but the game ignored it. **Fix**: explicit GCD gate in
   `meiko_pre_tick` (`if now < state.gcd_until: return None`), + `engine.py`
   now sets a GCD after hook-triggered presses (except those in
   `off_gcd_hook_abilities`).

2. **Fighting Techniques presses but the spell doesn't fire** — the finisher
   went out ~30ms after the 2nd piece, probably too early for the game to have
   registered that 2nd piece as completing a valid combo. **Fix**:
   `FINISHER_DELAY_S = 0.15` (150ms buffer) in `hooks.py` before pressing the
   finisher.

3. **Defensive combo spams / infinite loop, Storm loop never runs** — root
   cause: `meiko_post_tick` ESTIMATED the remaining duration of the Spirited
   Vortex/Strikes buffs via a decremented timer, initialized to 0.0 at bot
   startup regardless of the real in-game state → the hook believed almost
   permanently that one of the two buffs needed a refresh, and never let the
   Storm/DPS loop run. **Fix**: replaced the estimate with real screen
   detection. `state.buffs["spirited_vortex"]` / `["spirited_strikes"]` are
   read every tick via `state_update_fn` (templates `buff_spirited_vortex` /
   `buff_spirited_strikes` on the buff bar). `meiko_pre_tick` reads
   `state.buffs.get(...)` directly.

**General principle that emerged from these 3 bugs**: never estimate a state
that can be detected directly on screen. A timer/estimate drifts and doesn't
know the real state at startup; a template detection reads the state as it is,
every tick.

### Current status (to verify/update, it moves fast)

Templates captured so far: `spirit_palm`, `wind_kick`, `earth_fist`,
`fighting_techniques` (captured but **not used for detection** — the icon
"morphs", the engine presses this name blindly via the hook, without ever
checking a template on it), `stormfall_lit`, `warden_of_the_temple_3`.

Still to capture/verify: `earthfall_lit`, `buff_spirited_vortex`,
`buff_spirited_strikes` (support just added on the code side, not yet validated
in a real game at the time of this summary).

`stone_shield` (condition `flag:need_shield`) and `serenity` (condition
`health_below:60`) are defined in `config/warden.yaml` but **will never trigger
as is**: nothing sets `state.flags["need_shield"]` nor updates
`state.health_pct` (stays at 100.0 by default). This is a known, deliberately
deferred gap, not a bug to fix reflexively.

### Constraints / conventions

- **Windows only** (l'utilisateur uses PowerShell; `capture_helper.py` relies on
  `ctypes.windll`, no cross-platform fallback planned).
- Default template-matching confidence threshold: **0.70**.
- Input simulation: **pydirectinput**, not pyautogui (in-game reliability).
- `test_wardenbot.py` must stay runnable without a screen or heavy dependencies
  (only PyYAML required) — do not make it import mss/cv2/pydirectinput.
- `python test_wardenbot.py` must pass before any refactor of `hooks.py` or
  `engine.py`.

### Open leads / possible next steps

- Validate in a real game the detection of the 2 buffs + freshly-wired
  earthfall_lit (fix for bug #3).
- Charge detection for Stone Shield (reading an on-screen number badge) + real
  health for Serenity, if l'utilisateur ever wants these two abilities to activate.
- Possible port of the same engine to other l'utilisateur characters (Tariq, Helena)
  — not started, discussed only as a lead.
