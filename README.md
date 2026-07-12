# wardenbot

Moteur de rotation minimal pour Meiko (Fellowship), pensé pour remplacer la
partie "combo decision", auparavant gérée par des hooks opaques, par du
Python que tu contrôles entièrement.
Le problème qu'on n'arrivait pas à trancher (est-ce que la logique choisit
Vortex ou Strikes au moment du 3e stack de Warden of the Temple ?) est réglé
par construction : c'est un `if`/`else` dans `hooks.py` que tu lis toi-même,
pas une boîte noire.

Tests validés (`python test_wardenbot.py`) : la logique "3 stacks -> Vortex
même si Strikes a un timer plus bas" est déterministe et vérifiée sans écran
ni jeu.

## Installation

```
pip install -r requirements.txt --break-system-packages   # si besoin sur Linux
```

## Structure

- `wardenbot/capture.py` — capture d'écran (mss)
- `wardenbot/matcher.py` — template matching (OpenCV)
- `wardenbot/input_sim.py` — simulation de touches (pydirectinput)
- `wardenbot/engine.py` — moteur : chargement YAML, conditions, scan de
  priorité, boucle principale
- `wardenbot/hooks.py` — **la logique combo Meiko, à lire/modifier toi-même**
- `config/warden.yaml` — les abilities hors-combo (Stone Shield, Shatter
  Earth, Serenity) + les 1res pièces de combo (Earth Fist, Wind Kick)
- `main.py` — assemble tout, à adapter avec tes touches/régions réelles
- `test_wardenbot.py` — preuve que la décision 3-stacks fonctionne, sans jeu

## Ce qui manque avant de tourner en jeu réel

1. **Templates** : capture une image PNG par ability + par état à détecter
   (dont `warden_of_the_temple_3`, l'icône du debuff à 3 stacks) dans
   `templates/`.
2. **Régions** : remplis `REGIONS` dans `main.py` avec les coordonnées
   écran de chaque icône (left/top/width/height).
3. **Touches** : remplis `KEY_MAP` dans `main.py` avec tes vrais binds.
4. **Ressources** : `state.resources["stormfall"]` / `["earthfall"]` doivent
   être mis à jour par TA détection (lecture des orbes de charge) — pour
   l'instant rien ne les alimente automatiquement, `is_ready_fn`/detection à
   brancher dans `main.py`.
5. **Debuff 3-stacks** : idem, `state.debuffs["warden_of_the_temple_3"]` doit
   être mis à jour par un vrai match du template capturé, pas codé en dur.
6. Teste d'abord avec `dry_run=True` (juste des `print`), vérifie que la
   séquence affichée en jeu réel (regarde les logs) correspond à ce que
   `test_wardenbot.py` prédit, PUIS seulement passe en `dry_run=False`.

## Pourquoi Python / mss / OpenCV / pydirectinput

- `mss` : capture d'écran nettement plus rapide que `pyautogui.screenshot()`
  en boucle serrée.
- OpenCV `cv2.matchTemplate` : principe de confidence 0.0-1.0 classique
  (`detection.buff_threshold` etc.).
- `pydirectinput` plutôt que `pyautogui` pour l'envoi de touches : plus
  fiable sur les jeux qui ignorent les événements `SendInput` classiques.
