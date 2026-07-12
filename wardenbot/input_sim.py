"""
input_sim.py — simulation de touches clavier.

pydirectinput plutôt que pyautogui : beaucoup de jeux (et notamment les jeux
DirectX plein écran) ignorent les événements SendInput classiques de
pyautogui mais réagissent à pydirectinput (scan codes bas niveau). Si tu
constates des touches "avalées" en jeu mais qui marchent en dry-run, c'est
typiquement ce genre de souci — teste d'abord en jeu réel avant de creuser le
reste du moteur.

NOTE (2026-07-12) : pydirectinput utilise DÉJÀ SendInput (avec scan codes)
en interne -- ce n'est pas pyautogui/SendMessage. Rien à changer de ce
côté-là. Ce qui manquait vraiment : tous les délais (durée de presse,
espacement des modificateurs) étaient des constantes FIXES (time.sleep(0.05)
partout, durée de presse par défaut de pydirectinput identique à chaque
appel) -- un motif parfaitement régulier, contrairement à une vraie presse
humaine. HUMANIZE_JITTER_* introduit une variation aléatoire (durée de
presse, espacement modificateur/touche) sur chaque appel -- répond aussi à
un vrai souci de fiabilité déjà rencontré : le bug Shift+3 (modificateur
perdu, cf. CLAUDE.md/historique) venait de transitions keyDown/keyUp trop
rapprochées et parfaitement déterministes.
"""
from __future__ import annotations

import random
import time

try:
    import pydirectinput as _input
    _input.PAUSE = 0  # on gère notre propre timing (gcd_ms), pas celui de la lib
except ImportError:
    _input = None

# Fourchettes de jitter (secondes) -- valeurs plausibles pour une vraie presse
# humaine (ni trop courtes/robotiques, ni assez longues pour rater un GCD).
HUMANIZE_HOLD_S = (0.035, 0.09)      # durée keyDown -> keyUp de la touche principale
HUMANIZE_MOD_GAP_S = (0.04, 0.09)    # espacement modificateur -> touche principale
HUMANIZE_MOD_RELEASE_S = (0.03, 0.07)  # espacement avant de relâcher les modificateurs

# Profil allégé (2026-07-12) -- pour les abilities qui spamment beaucoup
# (wild_swing en filler, skull_crusher en dump de Fury) : la variance reste
# réelle (toujours randomisée, pas de motif fixe) mais sur une fourchette
# plus courte -- moins de temps perdu cumulé sur des dizaines de presses/min,
# tout en gardant l'essentiel (jamais deux presses identiques au ms près).
# Réservé aux abilities à faible enjeu -- PAS touché pour les procs/cooldowns
# importants, où la marge de sécurité contre un GCD raté compte plus que
# quelques ms économisés.
HUMANIZE_HOLD_S_LIGHT = (0.015, 0.03)


class InputSimulator:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run or _input is None
        if self.dry_run and _input is None:
            print("[input_sim] pydirectinput non installé -> mode dry-run forcé.")

    def press(self, key: str, label: str | None = None, light: bool = False):
        """
        key : soit une touche seule ("3"), soit une combinaison avec
        modificateur(s) séparés par "+" ("shift+3", "ctrl+alt+x"...) --
        maintient les modificateurs enfoncés (keyDown) pendant la presse de
        la touche principale, puis les relâche (keyUp) dans l'ordre inverse.
        Tous les délais sont randomisés (cf. HUMANIZE_* ci-dessus) plutôt que
        fixes -- pas de motif de timing identique d'une presse à l'autre.

        light=True : utilise HUMANIZE_HOLD_S_LIGHT au lieu de HUMANIZE_HOLD_S
        pour la touche principale -- réservé aux fillers/spam (cf. wild_swing
        dans main.py), jamais aux combos à modificateur (l'espacement
        modificateur reste HUMANIZE_MOD_GAP_S/RELEASE_S dans tous les cas,
        c'est lui qui protège contre le bug Shift+3).
        """
        if self.dry_run:
            tag = f"{label} -> " if label else ""
            print(f"[DRY RUN] {tag}press: {key}  (t={time.time():.3f})")
            return
        hold_range = HUMANIZE_HOLD_S_LIGHT if light else HUMANIZE_HOLD_S
        if "+" in key:
            # Petits délais entre chaque étape -- des appels keyDown/press/
            # keyUp dos-à-dos sans pause peuvent faire perdre l'état du
            # modificateur au jeu (le press() de la touche principale part
            # avant que le jeu ait enregistré le keyDown de shift), et le
            # jeu reçoit alors la touche SEULE au lieu de la combinaison
            # (ex. "3" au lieu de "shift+3" -- suspecté sur Culling Strike).
            *modifiers, base_key = key.split("+")
            for mod in modifiers:
                _input.keyDown(mod.strip().lower())
                time.sleep(random.uniform(*HUMANIZE_MOD_GAP_S))
            base_key = base_key.strip()
            _input.keyDown(base_key)
            time.sleep(random.uniform(*hold_range))
            _input.keyUp(base_key)
            time.sleep(random.uniform(*HUMANIZE_MOD_RELEASE_S))
            for mod in reversed(modifiers):
                _input.keyUp(mod.strip().lower())
        else:
            _input.keyDown(key)
            time.sleep(random.uniform(*hold_range))
            _input.keyUp(key)