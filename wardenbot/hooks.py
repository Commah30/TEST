"""
hooks.py — logique de rotation par personnage, en Python pur et 100%
transparent. Section Meiko (combo) en haut, section Tariq (rotation en
liste de priorité) en bas.

MEIKO -- C'est ICI que se joue le problème qu'on n'arrivait pas à garantir
auparavant : la décision "quelle 2e pièce presser après Spirit Palm" est un
simple if/else que tu lis et modifies toi-même. Pas de hook opaque, pas de
course contre un timer caché -- l'ordre d'exécution est celui du fichier.

Combo table (cf. ton meiko.yaml) :
  Spirit Palm + Wind Kick  + FT = Double Palm Strike (défensif, DR/Parry)
  Spirit Palm + Earth Fist + FT = Spirited Vortex     (défensif, Dodge/DoT/slow)
  Wind Kick   + Spirit Palm+ FT = Rising Storm        (build Stormfall)
  Wind Kick   + Earth Fist + FT = Lashing Stormkick   (spend Stormfall, AoE) -- stack Warden of the Temple
  Earth Fist  + Spirit Palm+ FT = Rising Earth        (build Earthfall)
  Earth Fist  + Wind Kick   + FT = Earthfist Barrage   (spend Earthfall, ST) -- stack Warden of the Temple

Les pseudo-noms "*_2" désignent la 2e pièce d'un combo (même touche réelle
que la version sans suffixe -- voir KEY_MAP dans main.py).

DÉTECTION DES BUFFS (spirited_vortex / spirited_strikes) : lue directement
depuis l'écran via state_update_fn (main.py), PAS estimée par un timer.
state.buffs["spirited_vortex"] / ["spirited_strikes"] reflètent l'état réel
détecté à CE tick -- present/absent, comme n'importe quelle condition
buff:name du reste du système.

STACKS WARDEN OF THE TEMPLE : PAS lues sur le badge x1/x2/x3 de l'icône --
testé en jeu, le badge est trop petit pour que le template matching
distingue x2 de x3 de façon fiable (le bot restait bloqué à forcer Vortex
dès 2 stacks). Le nombre de stacks est à la place compté ici, dans le code :
la mécanique est entièrement déterministe (seuls Lashing Stormkick et
Earthfist Barrage posent un stack, seul un Vortex casté À 3 stacks le
consomme -- rien d'autre ne peut le faire varier), donc pas besoin de lire
un pixel pour le savoir.

Un premier filet de sécurité "rattrape le compteur vers le haut si le badge
x3 est détecté visuellement" a été essayé, puis RETIRÉ : testé en jeu,
state.debuffs["warden_of_the_temple_3"] reste bloqué à True en continu (même
juste après un reset), donc ce filet reforçait le compteur à 3 en boucle et
reproduisait exactement le spam qu'on venait de corriger. La détection
visuelle de ce debuff n'est donc plus utilisée du tout ici.

Seul filet de sécurité anti-désynchro restant (utile si le bot est relancé
en plein combat avec des stacks déjà posées avant) : si le compteur reste
bloqué à 3 plus de WARDEN_STACK_TIMEOUT_S sans qu'un Vortex parte pour le
consommer, on considère que quelque chose a désynchronisé et on force un
recalibrage à 0. Reset manuel possible en jeu via la touche de debug F9
(cf. main.py).
"""
from __future__ import annotations

import time
from typing import Optional

from .engine import State

FINISHER_DELAY_S = 0.15   # laisse au jeu le temps d'enregistrer la 2e pièce
WARDEN_STACKS_TO_EMPOWER = 3
WARDEN_STACK_TIMEOUT_S = 10.0


def _set_warden_stacks(state: State, now: float, value: int) -> None:
    value = max(0, min(value, WARDEN_STACKS_TO_EMPOWER))
    was_empowered = state.flags.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER
    state.flags["warden_stacks"] = value
    is_empowered = value >= WARDEN_STACKS_TO_EMPOWER
    if is_empowered and not was_empowered:
        state.flags["warden_stacks_since"] = now
    elif not is_empowered:
        state.flags.pop("warden_stacks_since", None)


def _reconcile_warden_stacks(state: State, now: float) -> None:
    """
    Filet de sécurité anti-désynchro -- cf. docstring du module.

    Le rattrapage via le badge visuel (state.debuffs["warden_of_the_temple_3"])
    a été retiré : testé en jeu, cette détection reste bloquée à True en
    continu (même juste après un reset), donc elle re-forçait le compteur à
    3 en boucle et reproduisait le spam qu'on venait de corriger. Seul le
    timeout protège maintenant contre une désynchro (+ reset manuel F9).
    """
    since = state.flags.get("warden_stacks_since")
    if since is not None and now - since > WARDEN_STACK_TIMEOUT_S:
        _set_warden_stacks(state, now, 0)


def meiko_pre_tick(state: State) -> Optional[str]:
    """
    Complète un combo en cours. Si le dernier cast est la 1re pièce d'un
    combo, retourne le nom de la 2e pièce à presser CE tick (bypass le scan
    de priorité normal, exactement comme meiko_combo_pre_tick mais en clair).
    Si le dernier cast EST une 2e pièce, retourne le finisher (après un
    court délai tampon, cf. FINISHER_DELAY_S).
    Retourne None si rien à compléter -> le scan de priorité prend le relai
    (c'est lui qui choisit la 1re pièce DPS : Earth Fist en ST, Wind Kick en AoE).
    """
    last = state.last_cast
    now = time.monotonic()

    _reconcile_warden_stacks(state, now)

    # --- 2e pièce déjà pressée -> finisher, après un court délai tampon.
    # Off-GCD -> pas d'attente du GCD complet, juste le temps que le jeu
    # enregistre la 2e pièce avant d'envoyer le finisher derrière. C'est
    # aussi CE moment qu'on prend comme preuve que le cast est "réel" (pas
    # juste pressé) pour mettre à jour le compteur de stacks. ---
    if last in ("wind_kick_2", "earth_fist_2", "spirit_palm_2"):
        if now - state.last_cast_time < FINISHER_DELAY_S:
            return None
        combo = state.flags.pop("_pending_stack_combo", None)
        if combo == "grant":
            _set_warden_stacks(state, now, state.flags.get("warden_stacks", 0) + 1)
        elif combo == "reset":
            _set_warden_stacks(state, now, 0)
        return "fighting_techniques"

    # --- Tout ce qui suit presse une VRAIE touche soumise au GCD -- on
    # n'agit que si le GCD de la frappe précédente est terminé. ---
    if now < state.gcd_until:
        return None

    # --- Storm (Wind Kick 1re pièce) : build ou spend selon la charge Stormfall ---
    if last == "wind_kick":
        if state.resources.get("stormfall", 0.0) >= 0.999:
            state.flags["_pending_stack_combo"] = "grant"   # Lashing Stormkick -> +1 stack
            return "earth_fist_2"
        state.flags.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Storm

    # --- Earth (Earth Fist 1re pièce) : build ou spend selon la charge Earthfall ---
    if last == "earth_fist":
        if state.resources.get("earthfall", 0.0) >= 0.999:
            state.flags["_pending_stack_combo"] = "grant"   # Earthfist Barrage -> +1 stack
            return "wind_kick_2"
        state.flags.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Earth

    # --- Defensive (Spirit Palm 1re pièce) : LA décision qu'on ne pouvait
    # jamais garantir auparavant. Basée sur l'état RÉEL des buffs, pas une
    # estimation. ---
    if last == "spirit_palm":
        vortex_up = state.buffs.get("spirited_vortex", False)
        strikes_up = state.buffs.get("spirited_strikes", False)

        if state.flags.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER:
            # 3 stacks confirmés -> Spirited Vortex à coup sûr, peu importe
            # l'état de Strikes. C'est la règle que tu voulais imposer. Ce
            # cast-là (et LUI SEUL) consomme les 3 stacks.
            state.flags["_pending_stack_combo"] = "reset"
            return "earth_fist_2"

        if not vortex_up:
            # Vortex casté hors 3-stacks -> les stacks en cours ne sont PAS
            # consommées (seul un Vortex à 3 stacks les consomme).
            state.flags.pop("_pending_stack_combo", None)
            return "earth_fist_2"     # Vortex manquant -> priorité
        if not strikes_up:
            state.flags.pop("_pending_stack_combo", None)
            return "wind_kick_2"      # sinon Strikes manquant -> le prendre
        # Les deux sont déjà up (cas limite, ne devrait pas arriver souvent
        # puisqu'on n'ouvre ce combo QUE quand l'un des deux manque, cf. plus
        # bas) -> défaut sûr sur Strikes plutôt que de ne rien retourner.
        state.flags.pop("_pending_stack_combo", None)
        return "wind_kick_2"

    # --- Rien en cours : faut-il OUVRIR le combo défensif maintenant ? ---
    # On n'ouvre QUE si un vrai manque est détecté (buff absent) ou si les 3
    # stacks sont atteintes -- plus de minuteur estimé qui peut rester
    # bloqué près de 0 et forcer une boucle défensive infinie.
    vortex_up = state.buffs.get("spirited_vortex", False)
    strikes_up = state.buffs.get("spirited_strikes", False)
    needs_refresh = (
        state.flags.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER
        or not vortex_up
        or not strikes_up
    )
    if needs_refresh:
        return "spirit_palm"

    return None


def meiko_post_tick(state: State, pressed: str) -> None:
    """
    Plus de bookkeeping de timers à faire ici : state.buffs est déjà tenu à
    jour par state_update_fn (lecture d'écran) à chaque tick, avant même que
    meiko_pre_tick ne s'exécute, et le compteur de stacks Warden of the
    Temple est géré directement dans meiko_pre_tick (cf. plus haut). Gardé
    comme point d'extension si tu veux logger/débugger les transitions plus
    tard.
    """
    return


# =============================================================================
# TARIQ -- logique hors-combo (rotation en liste de priorité classique, pas
# de séquence à compléter comme Meiko). tariq_pre_tick ne presse donc jamais
# rien lui-même : il pose des flags de détection AVANT le scan de priorité,
# que les conditions du YAML lisent ensuite via flag:X (cf. engine.py).
#
# hammer_storm_proc_ready : fix du bug "double-cast sur une seule instance
# de proc" (rapport complet dans message.txt). La condition d'origine,
# `buff:hammer_storm_empowered`, est un test de NIVEAU (le buff lit-il
# "actif" CE tick ?) -- si la détection reste bloquée sur "actif" plusieurs
# ticks (jusqu'à plusieurs secondes, cf. "Case B" du rapport : ~90 lectures
# ACTIVE d'affilée après un stall), hammer_storm_proc repart tant que la
# lecture reste "actif", donc plusieurs fois pour UNE SEULE proc réelle.
# Aucun cooldown_ms fixe ne peut corriger ça : le re-proc légitime peut
# arriver en ~1 GCD ("Case A"), le blocage visuel peut durer plusieurs
# secondes -- pas de seuil qui bloque l'un sans retarder/rater l'autre.
#
# Fix : le proc n'est "prêt" que sur la transition absent -> présent (front
# montant), jamais tant qu'il reste juste "présent". Une fois consommé
# (tariq_post_tick, au vrai cast), il faut une NOUVELLE transition pour
# redevenir prêt -- peu importe combien d'autres abilities sont castées
# entre-temps (donc plus besoin du "not last:hammer_storm_proc" d'origine,
# qui se faisait justement contourner par un off-GCD intercalé).
#
# skull_crusher_proc a exactement la même forme dans Schism V2.yaml
# (condition buff:skull_crusher_empowered, cooldown_ms quasi nul, et même
# PAS de garde-fou "not last:" du tout) -- même fix appliqué par analogie.
# Pas encore confirmé par des logs comme hammer_storm_proc (cf.
# message.txt) : à surveiller en jeu.
# =============================================================================
TARIQ_PROC_BUFFS = {
    "hammer_storm_proc": "hammer_storm_empowered",
    "skull_crusher_proc": "skull_crusher_empowered",
}

# BUG TROUVÉ EN JEU (2026-07-12) : la détection des buffs (template match,
# un booléen brut par tick) peut clignoter sur UNE seule frame de bruit
# (compression, léger artefact) -- absent->présent->absent->présent en
# quelques ticks, sans qu'un vrai nouveau proc n'ait eu lieu. Le front
# montant s'armait alors à tort sur ce bruit, causant des hammer_storm_proc/
# skull_crusher_proc castés "sans proc" réel. MIN_ABSENT_TICKS exige que le
# buff ait été lu absent PLUSIEURS ticks consécutifs avant qu'une nouvelle
# lecture "présent" compte comme un vrai front montant -- filtre le bruit
# d'1 frame sans retarder perceptiblement un vrai proc (3 ticks ~100ms à
# 30Hz, négligeable face au GCD de 1s).
MIN_ABSENT_TICKS = 3

# Hammer Storm est un cast (canalisation) qui ne doit JAMAIS être coupé par
# une autre ability -- y compris les off_gcd (culling_strike, focused_wrath,
# thunder_call...), qui bypassent normalement le gate de GCD classique.
# state.flags["hammer_storm_casting"] est posé par make_tariq_state_update_fn
# (main.py) via bar_reader.read_fill_pct sur la cast bar (direction="ltr" --
# le rempli est ancré à gauche, le bord droit recule vers la gauche pendant
# la canalisation -- vérifié image par image sur un clip Game Bar, cf.
# main.py). Tant que c'est vrai, on repousse hard_lock_until -- pas besoin de connaître la durée
# exacte du cast, juste de renouveler le verrou tant que la détection
# confirme, tick par tick, que ça caste encore (même principe que le reste
# du projet : détecter l'état réel, jamais l'estimer). RotationEngine.tick()
# (engine.py) bloque TOUTE presse tant que now < state.hard_lock_until.
HAMMER_STORM_LOCK_REFRESH_S = 0.2   # marge au-delà du tick courant -- absorbe un raté de détection isolé sans laisser le verrou tomber en plein cast

# SIMPLIFICATION VOLONTAIRE (2026-07-12), À LA DEMANDE DE L'UTILISATEUR : tout le
# système de comptage de stacks Hammer Storm (combo "attendre 2 procs ->
# Focused Wrath -> Hammer Storm", réarmement sur augmentation du badge OCR,
# décrément d'une charge à la consommation) a été retiré. Root cause de la
# demande : l'utilisateur a confirmé que le jeu n'affiche JAMAIS "2" sur ce badge --
# quand Skull Crusher fait proc Hammer Storm une 2e fois, il faut un 2e cast
# de Skull Crusher pour re-proc, et le badge reste identique ("rien"/pas de
# chiffre) qu'il y ait 1 ou "2" procs en attente. Le compteur de stacks ne
# pouvait donc STRUCTURELLEMENT jamais distinguer "même proc résiduel" de
# "nouveau proc accumulé" -- toute la logique de réarmement sur augmentation
# était vouée à l'échec (elle ne s'est JAMAIS déclenchée en jeu réel). Le
# vrai besoin de l'utilisateur : tirer Hammer Storm sur N'IMPORTE QUEL proc détecté,
# immédiatement, sans attendre quoi que ce soit -- exactement ce que fait
# déjà le simple front montant (TARIQ_PROC_BUFFS ci-dessus) sur
# hammer_storm_proc_ready. Rien à ajouter par-dessus.
#
# Focused Wrath perd son déclenchement lié aux stacks -- son unique
# condition redevient "pas déjà active" (cf. config/tariq.yaml), elle part
# donc dès que son cooldown (86s) est terminé, comme thunder_call. Toujours
# utile même sans combo particulier : off_gcd, aucun coût, et le rabais de
# Fury ci-dessous en profite dès qu'elle est up, peu importe la source
# (bot ou Leap Smash castée par l'utilisateur).
#
# BUG TROUVÉ EN JEU (2026-07-12) : "hammerstorm ne fire pas quand il devrait"
# -- vérifié sur fellowship.wiki.fextralife.com/Focused+Wrath : "Your next
# two casts of Skull Crusher or Hammer Storm cost 50% less Fury". Tant que
# focused_wrath_empowered est actif, la condition YAML fury_above:50/23
# (coût plein) est trop stricte : elle bloque un proc dont le VRAI coût,
# réduit de 50%, est déjà couvert. Seuils réduits ci-dessous, vérifiés en
# général (pas liés aux stacks) puisque le buff peut venir du bot ou de Leap
# Smash castée manuellement par l'utilisateur.
HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED = 25.0
SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED = 12.0   # config/tariq.yaml : fury_above:23 (coût plein) / 2

# BUG TROUVÉ EN JEU (2026-07-13, rapporté par l'utilisateur) : hammer_storm_proc
# consommé (ready -> False dans tariq_post_tick) sur la seule foi d'avoir
# PRESSÉ la touche -- sans jamais vérifier que le jeu a réellement lancé le
# sort. Scénario observé : le thread de détection lit une Fury périmée
# (cf. les épisodes de gel de plusieurs secondes trouvés dans debug.log,
# 2026-07-12 -- tout le snapshot reste figé, fury y compris) au-dessus du
# seuil au moment où la condition YAML est évaluée, mais la VRAIE Fury en
# jeu est en réalité insuffisante entre-temps -- le jeu refuse silencieusement
# le cast (pas de canalisation, pas de dépense de Fury), alors que le bot,
# lui, a déjà marqué le proc comme consommé. Résultat : le buff
# hammer_storm_empowered reste actif en jeu (jamais réellement consommé),
# mais plus aucun nouveau front montant ne peut s'armer tant qu'il ne
# redescend pas (cf. TARIQ_PROC_BUFFS plus haut) -- le proc reste bloqué
# indéfiniment, Hammer Storm ne repart plus.
#
# Fix : même principe que le reste du projet (détecter l'état réel, jamais
# l'estimer). On a DÉJÀ un signal de vérité terrain pour "Hammer Storm a
# réellement commencé à canaliser" -- state.flags["hammer_storm_casting"]
# (cast bar, cf. HAMMER_STORM_LOCK_REFRESH_S ci-dessus). tariq_post_tick ne
# marque donc plus le proc "consommé" au moment de la presse -- il pose
# juste un marqueur "en attente de confirmation". tariq_pre_tick confirme
# (efface vraiment ready) SEULEMENT si hammer_storm_casting devient vrai
# dans la fenêtre qui suit ; sinon (le press n'a mené à aucun cast réel),
# ready reste tel quel (déjà True) -- le prochain tick retente naturellement
# dès que les conditions (fury réelle, GCD) sont de nouveau réunies, au lieu
# de rester bloqué en silence.
#
# skull_crusher_proc n'a PAS ce filet : aucun signal de détection dédié
# ("est-ce que Skull Crusher a réellement fait des dégâts/proc ?") n'existe
# actuellement pour lui (contrairement à Hammer Storm, il n'a pas de cast
# bar propre suivie) -- même vulnérabilité en théorie, non corrigée faute de
# vérité terrain disponible. Ne pas faire semblant de la corriger avec une
# heuristique bruitée (ex: delta de Fury) tant qu'aucune vraie détection
# n'existe -- cf. principe du projet, jamais d'estimation à la place d'une
# détection.
HAMMER_STORM_PROC_CONFIRM_WINDOW_S = 0.5   # marge après la presse pour laisser le jeu démarrer la canalisation avant de considérer que le press a échoué


def tariq_pre_tick(state: State) -> Optional[str]:
    now = time.monotonic()
    if state.flags.get("hammer_storm_casting", False):
        state.hard_lock_until = max(state.hard_lock_until, now + HAMMER_STORM_LOCK_REFRESH_S)
        if state.flags.get("_hammer_storm_proc_pending", False):
            # Canalisation confirmée pendant la fenêtre d'attente -> le press
            # a réellement abouti, le proc est VRAIMENT consommé maintenant.
            state.flags["hammer_storm_proc_ready"] = False
            state.flags["_hammer_storm_proc_pending"] = False

    pending_since = state.flags.get("_hammer_storm_proc_pending_since")
    if pending_since is not None and now - pending_since > HAMMER_STORM_PROC_CONFIRM_WINDOW_S:
        # Fenêtre expirée sans canalisation détectée -> le jeu a refusé le
        # cast (Fury réelle insuffisante, très probablement). ready n'a
        # JAMAIS été effacé (cf. tariq_post_tick) -- rien à restaurer, on
        # lève juste le marqueur d'attente pour permettre une nouvelle
        # tentative dès que les conditions sont réunies.
        state.flags["_hammer_storm_proc_pending"] = False
        state.flags.pop("_hammer_storm_proc_pending_since", None)

    for proc_name, buff_name in TARIQ_PROC_BUFFS.items():
        prev_key = f"_{buff_name}_prev"
        absent_ticks_key = f"_{buff_name}_absent_ticks"
        ready_key = f"{proc_name}_ready"

        was_active = state.flags.get(prev_key, False)
        is_active = state.buffs.get(buff_name, False)
        absent_ticks = state.flags.get(absent_ticks_key, MIN_ABSENT_TICKS)

        if is_active:
            if not was_active and absent_ticks >= MIN_ABSENT_TICKS:
                state.flags[ready_key] = True
            absent_ticks = 0
        else:
            absent_ticks += 1

        state.flags[absent_ticks_key] = absent_ticks
        state.flags[prev_key] = is_active

    # Focused Wrath réduit de 50% le coût en Fury des 2 prochains Hammer
    # Storm/Skull Crusher (cf. commentaire HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
    # plus haut) -- dès que le buff est actif (peu importe la source, bot ou
    # Leap Smash de l'utilisateur) et qu'un proc est prêt, on consomme dès que le
    # seuil RÉDUIT est couvert plutôt que d'attendre le seuil plein exigé par
    # la condition YAML normale.
    if state.buffs.get("focused_wrath_empowered", False):
        fury_pct = state.resources.get("fury", 0.0) * 100
        if (
            now >= state.gcd_until
            and not state.is_on_cooldown("hammer_storm_proc", now)
            and state.flags.get("hammer_storm_proc_ready", False)
            and fury_pct >= HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
        ):
            return "hammer_storm_proc"
        if (
            now >= state.gcd_until
            and not state.is_on_cooldown("skull_crusher_proc", now)
            and state.flags.get("skull_crusher_proc_ready", False)
            and fury_pct >= SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED
        ):
            return "skull_crusher_proc"

    return None


def tariq_post_tick(state: State, pressed: str) -> None:
    if pressed == "hammer_storm_proc":
        # Ne PAS effacer ready ici -- cf. HAMMER_STORM_PROC_CONFIRM_WINDOW_S :
        # seule une canalisation réellement détectée (tariq_pre_tick) confirme
        # la consommation. On pose juste le marqueur d'attente avec l'horodatage
        # de CETTE presse.
        state.flags["_hammer_storm_proc_pending"] = True
        state.flags["_hammer_storm_proc_pending_since"] = time.monotonic()
    elif pressed in TARIQ_PROC_BUFFS:
        state.flags[f"{pressed}_ready"] = False
