# -*- mode: python ; coding: utf-8 -*-
#
# Packaging PyInstaller (audit "RPA professionnel", 2026-07-26). Décisions
# prises et NE PAS re-débattre à ce stade (cf. le plan qui a produit ce
# fichier) :
#
# - onedir, PAS onefile : un onefile ~600MB (torch/cuda+ultralytics+easyocr,
#   cf. l'ancien wardenbot.zip, supprimé) se ré-auto-extrairait dans un
#   dossier TEMPORAIRE À CHAQUE lancement (démarrage de plusieurs dizaines de
#   secondes, usure disque). En onedir, les données sont déjà présentes en
#   permanence sur disque (dans _internal/, cf. wardenbot/paths.py::
#   resource_path() qui s'ancre sur sys._MEIPASS -- PyInstaller pointe
#   TOUJOURS _MEIPASS vers le dossier qui contient réellement datas=, aussi
#   bien en onedir (_internal/, stable) qu'en onefile (extraction temporaire
#   à chaque lancement) : la différence onedir/onefile porte sur COMMENT ce
#   dossier existe, pas sur quel attribut lire pour le trouver -- correction
#   du 2026-07-26 après un vrai crash reproduit, cf. wardenbot/paths.py).
#   Distribution en zip du dossier dist/wardenbot/ entier (exe + _internal/).
# - Cible = app.py (dashboard GUI PyWebView), PAS main.py (console) -- choix
#   utilisateur explicite (2026-07-26) : c'est l'expérience "RPA
#   professionnel" la plus aboutie du projet. console=False (fenêtre GUI,
#   pas de terminal) -- les erreurs restent journalisées dans error.log/
#   debug.log/telemetry.jsonl (cf. wardenbot/diagnostics.py,
#   wardenbot/telemetry.py) et le coupe-circuit (wardenbot/circuit.py) alerte
#   activement (winsound.Beep) plutôt que de compter sur un terminal visible.
#
# Risques CONNUS à traiter, PAS à découvrir après coup (cf. le plan) :
#
# 1. EasyOCR télécharge ses modèles de reconnaissance (~/.EasyOCR/model/,
#    craft_mlt_25k.pth ~83MB + english_g2.pth ~15MB) au premier appel
#    d'easyocr.Reader() si absents -- wardenbot/ocr.py ne les embarque PAS
#    dans ce build (aucun model_storage_directory personnalisé configuré,
#    cf. ocr.py) : un .exe packagé N'EST PAS autonome hors-ligne pour la
#    détection Tariq/Meiko tant que ces ~98MB n'ont pas été téléchargés une
#    première fois avec une connexion réseau active. Documenté ici plutôt
#    que découvert par un utilisateur derrière un proxy/pare-feu.
# 2. Antivirus/SmartScreen : un exécutable PyInstaller non signé déclenche
#    régulièrement de faux positifs antivirus et l'avertissement SmartScreen
#    de Windows -- pas de solution gratuite parfaite (la signature de code
#    coûte). Risque CONNU à documenter dans les instructions de distribution,
#    pas un bug de ce .spec.
# 3. CPU vs GPU (torch+cu126) : ce build embarque ~500MB de CUDA. Une
#    variante CPU-only (torch==...+cpu dans requirements.txt) diviserait la
#    taille par 2-3 -- décision reportée : dépend de qui reçoit réellement ce
#    .exe (soi-même sur une machine avec GPU connu, ou distribution plus
#    large à des configurations inconnues).
#
# hiddenimports volontairement VIDE : torch/ultralytics/easyocr font des
# imports dynamiques qui échappent souvent à l'analyse statique de
# PyInstaller -- à remplir après un premier échec de build/run RÉEL (erreur
# ModuleNotFoundError au lancement du .exe produit), pas deviné à l'avance
# sans preuve (même principe que gcd_ms/les seuils du coupe-circuit : jamais
# d'assomption non vérifiée).

datas = [
    ("templates", "templates"),
    ("config/warden.yaml", "config"),
    ("config/tariq.yaml", "config"),
    ("config/vigour.yaml", "config"),
    ("config/helena.yaml", "config"),
    ("config/saryn.yaml", "config"),
    # profiles/ ici n'est que le SEED par défaut, en lecture seule dans
    # _internal/profiles (resource_path) -- PAS l'emplacement réellement lu/
    # écrit au runtime. calibration_wizard.py ÉCRIT dans PROFILES_DIR
    # (wardenbot/config_manager.py, data_path("profiles") -- ancré au
    # dossier de l'EXE, pas _internal/) : un profil vivant dans _internal/
    # serait écrasé au prochain redéploiement du bundle, ou pas inscriptible
    # du tout si le dossier d'installation ne l'est pas. Au 1er lancement,
    # wardenbot/config_manager.py::_seed_default_profiles_if_missing() copie
    # CE dossier bundlé vers l'emplacement inscriptible -- cf.
    # wardenbot/paths.py pour la distinction resource_path()/data_path().
    ("profiles", "profiles"),
    ("ui.html", "."),
    # Poids YOLO entraînés (2026-07-26 : hors git désormais, cf. .gitignore
    # -- récupérés depuis un asset de release séparé, pas depuis un clone
    # frais). Seuls train-5 (Tariq) et train-6 (Meiko) sont réellement
    # référencés par wardenbot/yolo_detect.py -- train/, train-2/, train-3/,
    # train-4/ sont des runs obsolètes/de comparaison, jamais chargés au
    # runtime, volontairement PAS embarqués (bloat sans bénéfice).
    ("runs/detect/train-5/weights/best.pt", "runs/detect/train-5/weights"),
    ("runs/detect/train-6/weights/best.pt", "runs/detect/train-6/weights"),
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='wardenbot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# COLLECT (onedir) : rassemble exe + binaries + datas dans UN SEUL dossier
# (dist/wardenbot/) -- c'est ce dossier entier qui se distribue en zip, PAS
# le seul exe/. Absent de l'ancien .spec (qui bundlait a.binaries/a.datas
# directement dans EXE(), forme onefile-implicite) -- cf. commentaire de
# tête pour la justification onedir.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='wardenbot',
)
