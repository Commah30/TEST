# Contribuer à wardenbot

Ce projet suit un workflow de **revue à deux** : aucun changement n'entre dans
la branche `master` sans qu'une Pull Request ait été relue et approuvée par
l'autre personne. Personne ne pousse directement sur `master`.

## Règle d'or

- **Jamais de `git push` direct sur `master`.** Tout passe par une branche + une
  Pull Request.
- **Une PR = au moins une approbation de l'autre contributeur** avant de
  fusionner. Avec deux personnes, ça veut dire que chaque changement est
  vérifié par les deux : l'auteur ne peut pas approuver sa propre PR.
- **Les tests portables doivent passer** (localement et en CI) avant de
  demander la revue.

## Étapes pour un changement

```bash
# 1. Partir d'un master à jour
git checkout master
git pull

# 2. Créer une branche dédiée (nom court et parlant)
git checkout -b fix/mon-sujet        # ou feat/... , chore/...

# 3. Travailler, committer par petits lots cohérents
git add <fichiers>
git commit -m "Message clair au présent"

# 4. Lancer les tests portables AVANT de pousser
python test_wardenbot.py
python test_tariq.py
python test_config_manager.py

# 5. Pousser la branche et ouvrir une Pull Request sur GitHub
git push -u origin fix/mon-sujet
# puis : GitHub -> "Compare & pull request"
```

## Revue

- L'autre contributeur relit la PR, commente, et **approuve** si c'est bon.
- La CI (`.github/workflows/portable-tests.yml`) rejoue automatiquement
  `test_wardenbot.py`, `test_tariq.py`, `test_config_manager.py` sur chaque
  push et chaque PR. **Elle doit être verte** avant de fusionner.
- Une fois approuvée + CI verte : fusion dans `master` (de préférence
  "Squash and merge" pour garder un historique propre), puis suppression de la
  branche.

## Tests

- `test_wardenbot.py`, `test_tariq.py`, `test_config_manager.py` sont
  **portables** : aucun écran, aucun GPU, seulement `pyyaml`+`numpy`. Ce sont
  eux que la CI fait tourner. Ils doivent rester exécutables sans dépendances
  lourdes -- ne pas leur faire importer `mss`/`cv2`/`pydirectinput`/`torch`.
- **Règle projet** : `python test_wardenbot.py` doit passer avant tout refactor
  de `wardenbot/hooks.py` ou `wardenbot/engine.py`.
- Les autres tests (`test_dpi_awareness.py`, `test_region_selector.py`,
  `test_calibration_*`, `test_environment_detect.py`, `test_diagnostic_report.py`)
  ont besoin de Windows (`ctypes.windll`), d'un vrai modèle YOLO et/ou d'un
  affichage tkinter -- ils restent des tests **locaux manuels**, pas dans la CI.

## Ce qui n'est pas versionné

Voir `.gitignore` : les logs d'exécution (`debug.log`, `error.log`, etc.), les
`__pycache__`, les builds PyInstaller et les captures temporaires ne sont
jamais committés. Ne pas les forcer dans une PR.

## Contexte projet

Lire `CLAUDE.md` (mécanique de jeu Meiko/Tariq, architecture, bugs déjà
corrigés à ne pas réintroduire) et `README.md` avant de toucher à la logique de
rotation (`wardenbot/hooks.py`, `wardenbot/engine.py`).
