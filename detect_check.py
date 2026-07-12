"""
detect_check.py — vérifie en direct le score de confiance de chaque
template capturé, SANS presser aucune touche. À lancer avant de passer
dry_run=False dans main.py, pour confirmer que la détection fonctionne
vraiment sur ta config (résolution, échelle d'interface, etc.) avant de
laisser le bot toucher au jeu.

USAGE :
    python detect_check.py <perso>                              -> tous les templates connus de <perso>
    python detect_check.py <perso> spirit_palm                   -> un seul template (plus lisible)
    python detect_check.py <perso> spirited_vortex spirited_strikes  -> plusieurs, côte à côte
                                                                        (utile pour un test croisé)

<perso> est "meiko" ou "tariq" -- pointe vers templates/<perso>/ et
REGIONS[<perso>] (main.py).

Lis les scores en jeu : positionne-toi dans l'état "ready"/"actif" pour
chaque ability et vérifie que le score dépasse 0.70 (le seuil par défaut,
marqué "OK"). Passe l'ability en cooldown / fais disparaître le debuff et
vérifie que le score chute clairement -- c'est cet écart qui te dit si le
template est assez précis ou s'il faut le recapturer avec un cadrage plus
serré.
"""
from __future__ import annotations

import sys
import time

from wardenbot.capture import ScreenCapture
from wardenbot.matcher import TemplateMatcher
from main import REGIONS

THRESHOLD = 0.70

# Templates qui n'ont pas leur propre entrée dans REGIONS -- ils sont cherchés
# à l'intérieur d'une région plus large partagée (cf. main.py state_update_fn).
# Spécifique à Meiko pour l'instant.
TEMPLATE_REGION_OVERRIDES = {
    "spirited_vortex": "buff_bar",
    "spirited_strikes": "buff_bar",
    "warden_of_the_temple_3": "buff_bar",
}


def main():
    args = sys.argv[1:]
    if not args or args[0] not in REGIONS:
        print(f"Usage: python detect_check.py <perso> [template...]  (perso: {list(REGIONS.keys())})")
        return
    character = args[0]
    requested = args[1:]

    regions = REGIONS[character]
    overrides = TEMPLATE_REGION_OVERRIDES if character == "meiko" else {}
    names = requested if requested else list(regions.keys()) + list(overrides.keys())

    region_names = [overrides.get(n, n) for n in names]
    unknown = [rn for n, rn in zip(names, region_names) if rn not in regions]
    if unknown:
        print(f"Pas de région définie dans REGIONS[{character!r}] pour : {unknown}")
        return
    if not names:
        print(f"REGIONS[{character!r}] est vide -- rien à vérifier.")
        return

    capturer = ScreenCapture()
    matcher = TemplateMatcher(f"templates/{character}")

    print("Ctrl+C pour arrêter.\n")
    try:
        while True:
            parts = []
            for name, region_name in zip(names, region_names):
                region = regions[region_name]
                frame = capturer.grab(region)
                try:
                    conf = matcher.confidence(frame, name)
                    marker = "OK " if conf >= THRESHOLD else "   "
                    parts.append(f"{name}: {conf:.2f}{marker}")
                except FileNotFoundError:
                    parts.append(f"{name}: [pas de PNG capturé]")
            print("\r" + " | ".join(parts) + "     ", end="", flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nArrêté.")


if __name__ == "__main__":
    main()
