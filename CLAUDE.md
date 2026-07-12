# wardenbot — contexte projet

Moteur de rotation Python pour **Meiko / build "Warden"** (Monk tank) sur le
jeu **Fellowship**. Remplace la partie "décision de combo", auparavant gérée
via des hooks Python opaques (`meiko_combo_pre_tick` /
`meiko_combo_post_tick`) auxquels l'utilisateur n'avait pas accès (et dont le
mainteneur est surbooké), par du code Python que l'utilisateur possède et peut
éditer directement. Objectif d'origine : garantir
qu'un combo précis (Spirited Vortex) soit prioritaire sur un autre
(Spirited Strikes) quand un debuff atteint 3 stacks — impossible à garantir
avec les hooks opaques, trivial avec du code qu'on lit soi-même.

## Mécanique de jeu (Meiko)

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

## Architecture

```
main.py                  point d'entrée, wiring KEY_MAP/REGIONS, build_engine()
config/warden.yaml        les 5 abilities scannées par priorité normale
test_wardenbot.py         tests de logique pure (aucun écran requis)
capture_helper.py         utilitaire Windows (ctypes) : trouver régions + capturer templates PNG
detect_check.py           affiche en direct le score de confiance de chaque template
requirements.txt          pyyaml, mss, opencv-python-headless, pydirectinput, numpy, torch/torchvision (CUDA cu126), easyocr
wardenbot/
  engine.py                State, Ability, RotationEngine (le tick loop)
  hooks.py                 *** LA logique combo Meiko, en Python lisible ***
  capture.py                capture d'écran (mss)
  matcher.py                template matching (OpenCV, seuil défaut 0.70)
  input_sim.py               simulation de touches (pydirectinput, PAS pyautogui)
templates/                  PNG capturés (pas versionnés a priori)
```

### `wardenbot/engine.py`
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

### `wardenbot/hooks.py` — le fichier important
`meiko_pre_tick(state)` implémente toute la logique de combo en if/else
lisible. `meiko_post_tick` est actuellement un no-op (l'état des buffs
vient de la détection écran, pas d'une estimation).

Convention des noms : `wind_kick_2` / `earth_fist_2` / `spirit_palm_2`
désignent la 2e pièce d'un combo (même touche physique que la version sans
suffixe, cf. `KEY_MAP`). Utile car Wind Kick/Earth Fist/Spirit Palm jouent
chacun un rôle différent selon le contexte (ouvreur vs 2e pièce).

### `main.py`
`KEY_MAP` (nom logique → touche réelle) et `REGIONS` (nom logique → dict
région écran `{left, top, width, height}`) sont **édités à la main par
l'utilisateur au fur et à mesure des captures — ne jamais écraser ce fichier en
entier, toujours diff/merge**. `make_state_update_fn` lit l'écran à chaque
tick et remplit `state.resources`/`state.debuffs`/`state.buffs`.

## Bugs déjà rencontrés et corrigés (ne pas réintroduire)

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

## État actuel (à vérifier/mettre à jour, ça évolue vite)

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

## Contraintes / conventions

- **Windows uniquement** (l'utilisateur utilise PowerShell ; `capture_helper.py`
  s'appuie sur `ctypes.windll`, pas de fallback cross-platform prévu).
- Seuil de confiance template matching par défaut : **0.70**.
- Input simulation : **pydirectinput**, pas pyautogui (fiabilité en jeu).
- `test_wardenbot.py` doit rester exécutable sans écran ni dépendances
  lourdes (seul PyYAML requis) — ne pas lui faire importer mss/cv2/pydirectinput.
- `python test_wardenbot.py` doit passer avant tout refactor de `hooks.py`
  ou `engine.py`.

## Pistes ouvertes / suite possible

- Valider en jeu réel la détection des 2 buffs + earthfall_lit fraîchement
  branchée (fix du bug #3).
- Détection de charges pour Stone Shield (lecture d'un badge-chiffre à
  l'écran) + santé réelle pour Serenity, si l'utilisateur veut que ces deux
  abilities s'activent un jour.
- Portage éventuel du même moteur pour d'autres personnages de l'utilisateur
  (Tariq, Helena) — non commencé, discuté seulement comme piste.
