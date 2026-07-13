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

EN: hooks.py — per-character rotation logic, in pure and 100% transparent
EN: Python. Meiko section (combo) at the top, Tariq section (priority-list
EN: rotation) at the bottom.
EN:
EN: MEIKO -- This is WHERE the problem we could not guarantee before plays
EN: out: the "which 2nd piece to press after Spirit Palm" decision is a plain
EN: if/else you read and edit yourself. No opaque hook, no race against a
EN: hidden timer -- the execution order is the file's order.
EN:
EN: Combo table (cf. your meiko.yaml):
EN:   Spirit Palm + Wind Kick  + FT = Double Palm Strike (defensive, DR/Parry)
EN:   Spirit Palm + Earth Fist + FT = Spirited Vortex     (defensive, Dodge/DoT/slow)
EN:   Wind Kick   + Spirit Palm+ FT = Rising Storm        (builds Stormfall)
EN:   Wind Kick   + Earth Fist + FT = Lashing Stormkick   (spends Stormfall, AoE) -- stacks Warden of the Temple
EN:   Earth Fist  + Spirit Palm+ FT = Rising Earth        (builds Earthfall)
EN:   Earth Fist  + Wind Kick   + FT = Earthfist Barrage   (spends Earthfall, ST) -- stacks Warden of the Temple
EN:
EN: The "*_2" pseudo-names denote the 2nd piece of a combo (same real key as
EN: the suffix-less version -- see KEY_MAP in main.py).
EN:
EN: BUFF DETECTION (spirited_vortex / spirited_strikes): read directly from
EN: the screen via state_update_fn (main.py), NOT estimated by a timer.
EN: state.buffs["spirited_vortex"] / ["spirited_strikes"] reflect the real
EN: state detected THIS tick -- present/absent, like any buff:name condition
EN: in the rest of the system.
EN:
EN: WARDEN OF THE TEMPLE STACKS: NOT read from the icon's x1/x2/x3 badge --
EN: tested in-game, the badge is too small for template matching to reliably
EN: tell x2 from x3 (the bot stayed stuck forcing Vortex from 2 stacks).
EN: Instead the stack count is tracked here, in code: the mechanic is fully
EN: deterministic (only Lashing Stormkick and Earthfist Barrage add a stack,
EN: only a Vortex cast AT 3 stacks consumes it -- nothing else can change it),
EN: so no need to read a pixel to know it.
EN:
EN: A first safety net "bump the counter up if the x3 badge is detected
EN: visually" was tried, then REMOVED: tested in-game,
EN: state.debuffs["warden_of_the_temple_3"] stays stuck at True continuously
EN: (even right after a reset), so this net kept re-forcing the counter to 3
EN: and reproduced exactly the spam we had just fixed. This debuff's visual
EN: detection is therefore no longer used here at all.
EN:
EN: The only remaining anti-desync safety net (useful if the bot is restarted
EN: mid-fight with stacks already applied before): if the counter stays stuck
EN: at 3 for more than WARDEN_STACK_TIMEOUT_S without a Vortex firing to
EN: consume it, we assume something desynced and force a recalibration to 0.
EN: Manual reset possible in-game via the F9 debug key (cf. main.py).
"""
from __future__ import annotations

import time
from typing import Optional

from .engine import State
from wardenbot.diagnostics import log_press_confirmation

FINISHER_DELAY_S = 0.15   # laisse au jeu le temps d'enregistrer la 2e pièce · EN: gives the game time to register the 2nd piece
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

    EN: Anti-desync safety net -- cf. the module docstring.
    EN:
    EN: The catch-up via the visual badge
    EN: (state.debuffs["warden_of_the_temple_3"]) was removed: tested in-game,
    EN: this detection stays stuck at True continuously (even right after a
    EN: reset), so it kept re-forcing the counter to 3 and reproduced the spam
    EN: we had just fixed. Only the timeout now protects against a desync
    EN: (+ manual F9 reset).
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

    EN: Completes an in-progress combo. If the last cast is a combo's 1st
    EN: piece, returns the name of the 2nd piece to press THIS tick (bypasses
    EN: the normal priority scan, exactly like meiko_combo_pre_tick but in
    EN: plain code). If the last cast IS a 2nd piece, returns the finisher
    EN: (after a short buffer delay, cf. FINISHER_DELAY_S).
    EN: Returns None if nothing to complete -> the priority scan takes over (it
    EN: picks the 1st DPS piece: Earth Fist in ST, Wind Kick in AoE).
    """
    last = state.last_cast
    now = time.monotonic()

    _reconcile_warden_stacks(state, now)

    # --- 2e pièce déjà pressée -> finisher, après un court délai tampon.
    # Off-GCD -> pas d'attente du GCD complet, juste le temps que le jeu
    # enregistre la 2e pièce avant d'envoyer le finisher derrière. C'est
    # aussi CE moment qu'on prend comme preuve que le cast est "réel" (pas
    # juste pressé) pour mettre à jour le compteur de stacks. ---
    # EN: --- 2nd piece already pressed -> finisher, after a short buffer delay.
    # EN: Off-GCD -> no wait for the full GCD, just time for the game to
    # EN: register the 2nd piece before sending the finisher behind it. This is
    # EN: ALSO the moment we take as proof the cast is "real" (not just pressed)
    # EN: to update the stack counter. ---
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
    # EN: --- Everything below presses a REAL key subject to the GCD -- we only
    # EN: act if the previous strike's GCD is over. ---
    if now < state.gcd_until:
        return None

    # --- Storm (Wind Kick 1re pièce) : build ou spend selon la charge Stormfall ---
    # EN: --- Storm (Wind Kick 1st piece): build or spend depending on the Stormfall charge ---
    if last == "wind_kick":
        if state.resources.get("stormfall", 0.0) >= 0.999:
            state.flags["_pending_stack_combo"] = "grant"   # Lashing Stormkick -> +1 stack
            return "earth_fist_2"
        state.flags.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Storm

    # --- Earth (Earth Fist 1re pièce) : build ou spend selon la charge Earthfall ---
    # EN: --- Earth (Earth Fist 1st piece): build or spend depending on the Earthfall charge ---
    if last == "earth_fist":
        if state.resources.get("earthfall", 0.0) >= 0.999:
            state.flags["_pending_stack_combo"] = "grant"   # Earthfist Barrage -> +1 stack
            return "wind_kick_2"
        state.flags.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Earth

    # --- Defensive (Spirit Palm 1re pièce) : LA décision qu'on ne pouvait
    # jamais garantir auparavant. Basée sur l'état RÉEL des buffs, pas une
    # estimation. ---
    # EN: --- Defensive (Spirit Palm 1st piece): THE decision we could never
    # EN: guarantee before. Based on the REAL buff state, not an estimate. ---
    if last == "spirit_palm":
        vortex_up = state.buffs.get("spirited_vortex", False)
        strikes_up = state.buffs.get("spirited_strikes", False)

        if state.flags.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER:
            # 3 stacks confirmés -> Spirited Vortex à coup sûr, peu importe
            # l'état de Strikes. C'est la règle que tu voulais imposer. Ce
            # cast-là (et LUI SEUL) consomme les 3 stacks.
            # EN: 3 stacks confirmed -> Spirited Vortex for sure, regardless of
            # EN: the Strikes state. This is the rule you wanted to enforce.
            # EN: That cast (and IT ALONE) consumes the 3 stacks.
            state.flags["_pending_stack_combo"] = "reset"
            return "earth_fist_2"

        if not vortex_up:
            # Vortex casté hors 3-stacks -> les stacks en cours ne sont PAS
            # consommées (seul un Vortex à 3 stacks les consomme).
            # EN: Vortex cast outside 3-stacks -> the current stacks are NOT
            # EN: consumed (only a Vortex at 3 stacks consumes them).
            state.flags.pop("_pending_stack_combo", None)
            return "earth_fist_2"     # Vortex manquant -> priorité · EN: Vortex missing -> priority
        if not strikes_up:
            state.flags.pop("_pending_stack_combo", None)
            return "wind_kick_2"      # sinon Strikes manquant -> le prendre · EN: else Strikes missing -> take it
        # Les deux sont déjà up (cas limite, ne devrait pas arriver souvent
        # puisqu'on n'ouvre ce combo QUE quand l'un des deux manque, cf. plus
        # bas) -> défaut sûr sur Strikes plutôt que de ne rien retourner.
        # EN: Both are already up (edge case, shouldn't happen often since we
        # EN: only open this combo WHEN one of the two is missing, cf. below)
        # EN: -> safe default on Strikes rather than returning nothing.
        state.flags.pop("_pending_stack_combo", None)
        return "wind_kick_2"

    # --- Rien en cours : faut-il OUVRIR le combo défensif maintenant ? ---
    # On n'ouvre QUE si un vrai manque est détecté (buff absent) ou si les 3
    # stacks sont atteintes -- plus de minuteur estimé qui peut rester
    # bloqué près de 0 et forcer une boucle défensive infinie.
    # EN: --- Nothing in progress: should we OPEN the defensive combo now? ---
    # EN: We only open if a real gap is detected (buff absent) or if the 3
    # EN: stacks are reached -- no more estimated timer that can stay stuck near
    # EN: 0 and force an infinite defensive loop.
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

    EN: No more timer bookkeeping to do here: state.buffs is already kept up to
    EN: date by state_update_fn (screen read) every tick, before meiko_pre_tick
    EN: even runs, and the Warden of the Temple stack counter is managed
    EN: directly in meiko_pre_tick (cf. above). Kept as an extension point if
    EN: you want to log/debug the transitions later.
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
# EN: TARIQ -- non-combo logic (classic priority-list rotation, no sequence to
# EN: complete like Meiko). tariq_pre_tick therefore never presses anything
# EN: itself: it sets detection flags BEFORE the priority scan, which the YAML
# EN: conditions then read via flag:X (cf. engine.py).
# EN:
# EN: hammer_storm_proc_ready: fix for the "double-cast on a single proc
# EN: instance" bug (full report in message.txt). The original condition,
# EN: `buff:hammer_storm_empowered`, is a LEVEL test (does the buff read
# EN: "active" THIS tick?) -- if detection stays stuck on "active" for several
# EN: ticks (up to several seconds, cf. the report's "Case B": ~90 ACTIVE
# EN: reads in a row after a stall), hammer_storm_proc keeps firing while the
# EN: read stays "active", thus several times for ONE SINGLE real proc. No
# EN: fixed cooldown_ms can fix this: the legit re-proc can come in ~1 GCD
# EN: ("Case A"), the visual freeze can last several seconds -- no threshold
# EN: blocks one without delaying/missing the other.
# EN:
# EN: Fix: the proc is only "ready" on the absent -> present transition (rising
# EN: edge), never while it just stays "present". Once consumed (tariq_post_tick,
# EN: at the real cast), a NEW transition is needed to become ready again --
# EN: regardless of how many other abilities are cast in between (so the
# EN: original "not last:hammer_storm_proc" is no longer needed; it was exactly
# EN: what an intervening off-GCD bypassed).
# EN:
# EN: skull_crusher_proc has exactly the same shape in Schism V2.yaml
# EN: (condition buff:skull_crusher_empowered, near-zero cooldown_ms, and NOT
# EN: even a "not last:" guardrail at all) -- same fix applied by analogy. Not
# EN: yet confirmed by logs like hammer_storm_proc (cf. message.txt): to watch
# EN: in-game.
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
# EN: BUG FOUND IN-GAME (2026-07-12): buff detection (template match, a raw
# EN: boolean per tick) can flicker on ONE single noise frame (compression,
# EN: slight artifact) -- absent->present->absent->present within a few ticks,
# EN: without a real new proc having occurred. The rising edge then wrongly
# EN: armed on that noise, causing hammer_storm_proc/skull_crusher_proc cast
# EN: "without a real proc". MIN_ABSENT_TICKS requires the buff to have been
# EN: read absent for SEVERAL consecutive ticks before a new "present" read
# EN: counts as a real rising edge -- filters 1-frame noise without noticeably
# EN: delaying a real proc (3 ticks ~100ms at 30Hz, negligible against the 1s
# EN: GCD).
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
# EN: Hammer Storm is a cast (channel) that must NEVER be interrupted by
# EN: another ability -- including the off_gcd ones (culling_strike,
# EN: focused_wrath, thunder_call...), which normally bypass the classic GCD
# EN: gate. state.flags["hammer_storm_casting"] is set by
# EN: make_tariq_state_update_fn (main.py) via bar_reader.read_fill_pct on the
# EN: cast bar (direction="ltr" -- the fill is anchored left, the right edge
# EN: recedes leftward during the channel -- verified frame by frame on a Game
# EN: Bar clip, cf. main.py). While that's true, we push hard_lock_until back
# EN: -- no need to know the exact cast duration, just to renew the lock while
# EN: detection confirms, tick by tick, that it's still casting (same principle
# EN: as the rest of the project: detect the real state, never estimate it).
# EN: RotationEngine.tick() (engine.py) blocks EVERY press while now <
# EN: state.hard_lock_until.
HAMMER_STORM_LOCK_REFRESH_S = 0.2   # marge au-delà du tick courant -- absorbe un raté de détection isolé sans laisser le verrou tomber en plein cast · EN: margin beyond the current tick -- absorbs an isolated detection miss without letting the lock drop mid-cast

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
# focused_wrath_empowered est actif, la condition YAML fury_above:55/23
# (coût plein) est trop stricte : elle bloque un proc dont le VRAI coût,
# réduit de 50%, est déjà couvert. Seuils réduits ci-dessous, vérifiés en
# général (pas liés aux stacks) puisque le buff peut venir du bot ou de Leap
# Smash castée manuellement par l'utilisateur.
#
# MARGE AJOUTÉE (2026-07-13, cf. config/tariq.yaml::hammer_storm_proc) :
# même bruit de mesure identifié sur le seuil plein (échecs de cast
# confirmés entre 50.4% et 53.2% malgré une condition à 50) s'applique
# potentiellement ici -- marge proportionnelle (+5, comme 50->55) appliquée
# par précaution, MAIS pas confirmée par des logs sur CE seuil précis (le
# seul échec observé côté discount, à 34.8%, est déjà largement au-dessus de
# l'ancien seuil de 25 -- autre cause possible, pas expliquée par le bruit
# de mesure autour du seuil). À surveiller.
# EN: DELIBERATE SIMPLIFICATION (2026-07-12), AT THE USER'S REQUEST: the whole
# EN: Hammer Storm stack-counting system (the "wait for 2 procs -> Focused
# EN: Wrath -> Hammer Storm" combo, rearm on OCR badge increase, decrement one
# EN: charge on consumption) was removed. Root cause of the request: the user
# EN: confirmed the game NEVER shows "2" on that badge -- when Skull Crusher
# EN: procs Hammer Storm a 2nd time, a 2nd Skull Crusher cast is needed to
# EN: re-proc, and the badge stays identical ("nothing"/no number) whether
# EN: there are 1 or "2" pending procs. The stack counter therefore
# EN: STRUCTURALLY could never tell "same residual proc" from "new accumulated
# EN: proc" -- all the rearm-on-increase logic was doomed (it NEVER triggered
# EN: in a real game). the user's real need: fire Hammer Storm on ANY detected
# EN: proc, immediately, without waiting for anything -- exactly what the plain
# EN: rising edge (TARIQ_PROC_BUFFS above) on hammer_storm_proc_ready already
# EN: does. Nothing to add on top.
# EN:
# EN: Focused Wrath loses its stack-tied trigger -- its only condition reverts
# EN: to "not already active" (cf. config/tariq.yaml), so it fires as soon as
# EN: its cooldown (86s) is over, like thunder_call. Still useful even without
# EN: a particular combo: off_gcd, no cost, and the Fury discount below
# EN: benefits from it as soon as it's up, regardless of source (bot or Leap
# EN: Smash cast by the user).
# EN:
# EN: BUG FOUND IN-GAME (2026-07-12): "hammerstorm doesn't fire when it should"
# EN: -- verified on fellowship.wiki.fextralife.com/Focused+Wrath: "Your next
# EN: two casts of Skull Crusher or Hammer Storm cost 50% less Fury". While
# EN: focused_wrath_empowered is active, the YAML condition fury_above:55/23
# EN: (full cost) is too strict: it blocks a proc whose REAL cost, reduced by
# EN: 50%, is already covered. Reduced thresholds below, checked generally (not
# EN: stack-tied) since the buff can come from the bot or from Leap Smash cast
# EN: manually by the user.
# EN:
# EN: MARGIN ADDED (2026-07-13, cf. config/tariq.yaml::hammer_storm_proc): the
# EN: same measurement noise identified on the full threshold (cast failures
# EN: confirmed between 50.4% and 53.2% despite a condition at 50) potentially
# EN: applies here -- proportional margin (+5, like 50->55) applied as a
# EN: precaution, BUT not confirmed by logs on THIS precise threshold (the only
# EN: failure observed on the discount side, at 34.8%, is already well above
# EN: the old threshold of 25 -- another possible cause, not explained by the
# EN: measurement noise around the threshold). To watch.
HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED = 30.0
SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED = 12.0   # config/tariq.yaml : fury_above:23 (coût plein) / 2 -- pas de signal de détection dédié pour confirmer un cast Skull Crusher, donc pas de preuve pour ajuster ce seuil comme les deux ci-dessus (cf. skull_crusher_proc, non touché) · EN: config/tariq.yaml: fury_above:23 (full cost) / 2 -- no dedicated detection signal to confirm a Skull Crusher cast, so no proof to tune this threshold like the two above (cf. skull_crusher_proc, untouched)

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
# EN: BUG FOUND IN-GAME (2026-07-13, reported by the user): hammer_storm_proc
# EN: consumed (ready -> False in tariq_post_tick) purely on the faith of
# EN: having PRESSED the key -- without ever verifying the game actually
# EN: launched the spell. Observed scenario: the detection thread reads a stale
# EN: Fury (cf. the multi-second freeze episodes found in debug.log,
# EN: 2026-07-12 -- the whole snapshot stays frozen, fury included) above the
# EN: threshold at the moment the YAML condition is evaluated, but the REAL
# EN: in-game Fury is actually insufficient by then -- the game silently
# EN: refuses the cast (no channel, no Fury spent), while the bot has already
# EN: marked the proc as consumed. Result: the hammer_storm_empowered buff
# EN: stays active in-game (never actually consumed), but no new rising edge
# EN: can arm while it doesn't go back down (cf. TARIQ_PROC_BUFFS above) -- the
# EN: proc stays stuck indefinitely, Hammer Storm no longer fires.
# EN:
# EN: Fix: same principle as the rest of the project (detect the real state,
# EN: never estimate it). We ALREADY have a ground-truth signal for "Hammer
# EN: Storm actually started channeling" -- state.flags["hammer_storm_casting"]
# EN: (cast bar, cf. HAMMER_STORM_LOCK_REFRESH_S above). tariq_post_tick
# EN: therefore no longer marks the proc "consumed" at press time -- it just
# EN: sets a "pending confirmation" marker. tariq_pre_tick confirms (really
# EN: clears ready) ONLY if hammer_storm_casting becomes true within the
# EN: following window; otherwise (the press led to no real cast), ready stays
# EN: as-is (already True) -- the next tick naturally retries as soon as the
# EN: conditions (real fury, GCD) are met again, instead of staying silently
# EN: stuck.
# EN:
# EN: skull_crusher_proc does NOT have this net: no dedicated detection signal
# EN: ("did Skull Crusher actually deal damage/proc?") currently exists for it
# EN: (unlike Hammer Storm, it has no cast bar of its own being tracked) --
# EN: same vulnerability in theory, not fixed for lack of available ground
# EN: truth. Do not pretend to fix it with a noisy heuristic (e.g. Fury delta)
# EN: while no real detection exists -- cf. the project principle, never an
# EN: estimate in place of a detection.
HAMMER_STORM_PROC_CONFIRM_WINDOW_S = 0.5   # marge après la presse pour laisser le jeu démarrer la canalisation avant de considérer que le press a échoué · EN: margin after the press to let the game start the channel before considering the press failed

# =============================================================================
# PLANNER (2026-07-13, discuté avec l'utilisateur) -- au-dessus des priorités fixes
# de config/tariq.yaml, une SECONDE question : pas "que puis-je presser
# maintenant ?" mais "qu'est-ce que je suis en train de préparer ?".
#
# CE N'EST PAS une Behavior Tree ni un scoring général (cf. audit
# 2026-07-13 : rejetés faute de preuve qu'un vrai problème de PRIORITÉ existe
# aujourd'hui). C'est un objectif temporaire (state.flags["tariq_objective"])
# qui active/désactive quelques flags -- lus par les conditions YAML
# existantes exactement comme n'importe quel autre flag du projet
# (warden_stacks, hammer_storm_proc_ready...). Les priorités elles-mêmes ne
# changent JAMAIS : un flag change juste quelles conditions passent.
#
# Preuve concrète du problème que ça résout (debug.log, audit 2026-07-13) :
# focused_wrath et thunder_call partent aujourd'hui chacun dès que LEUR
# PROPRE cooldown le permet, sans coordination -- écarts réels observés de
# 22s et 27s entre les deux au lieu d'un burst empilé. Le champ
# `burst_sequence` du YAML exprimait déjà cette intention mais n'était lu
# nulle part (grep confirmé, zéro référence dans wardenbot/*.py ni main.py).
# Ceci est la première vraie implémentation de cette intention.
#
# Règle "prepare_burst" : si l'un des deux (focused_wrath/thunder_call) est
# prêt (hors cooldown) et que l'autre le sera dans moins de
# BURST_PREP_WINDOW_S, on RETIENT le premier (flag burst_hold, lu par la
# condition YAML des deux) au lieu de le tirer seul -- jusqu'à ce que les
# DEUX soient prêts (burst déclenché : le scan normal les presse l'un après
# l'autre, à un tick d'écart tout au plus puisque tous deux off_gcd) ou que
# la fenêtre soit dépassée (sécurité anti-blocage : timeout, cf. plus bas --
# ne reste jamais bloqué si un cooldown dérive plus que prévu).
#
# fury_conserve : pendant cette attente, on retient AUSSI la dépense
# discrétionnaire de Fury (skull_crusher normal, le filler qui dépense sans y
# être forcé) pour que la Fury soit maximale au moment où le burst
# démarre -- Focused Wrath réduit de 50% le coût des 2 prochains Hammer
# Storm/Skull Crusher (cf. HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED plus
# haut), donc plus de Fury en stock au moment du burst = plus de valeur tirée
# de cette fenêtre. Les GÉNÉRATEURS (face_breaker/chain_lightning/wild_swing)
# ne sont PAS retenus : ils doivent au contraire continuer à builder de la
# Fury pendant l'attente, sinon le burst démarrerait avec MOINS de ressource
# que possible -- l'inverse de l'objectif. Les PROCS (hammer_storm_proc/
# skull_crusher_proc) ne sont pas non plus concernés : règle historique du
# projet "aucun proc ne doit rater", jamais retenus par le planner.
# EN: PLANNER (2026-07-13, discussed with the user) -- above the fixed priorities
# EN: of config/tariq.yaml, a SECOND question: not "what can I press now?" but
# EN: "what am I preparing?".
# EN:
# EN: THIS IS NOT a Behavior Tree nor a general scoring (cf. the 2026-07-13
# EN: audit: rejected for lack of proof that a real PRIORITY problem exists
# EN: today). It's a temporary objective (state.flags["tariq_objective"]) that
# EN: enables/disables a few flags -- read by the existing YAML conditions
# EN: exactly like any other project flag (warden_stacks,
# EN: hammer_storm_proc_ready...). The priorities themselves NEVER change: a
# EN: flag just changes which conditions pass.
# EN:
# EN: Concrete proof of the problem it solves (debug.log, 2026-07-13 audit):
# EN: focused_wrath and thunder_call each fire today as soon as THEIR OWN
# EN: cooldown allows, with no coordination -- real gaps of 22s and 27s
# EN: observed between the two instead of a stacked burst. The YAML's
# EN: `burst_sequence` field already expressed this intent but was read nowhere
# EN: (grep confirmed, zero reference in wardenbot/*.py nor main.py). This is
# EN: the first real implementation of that intent.
# EN:
# EN: "prepare_burst" rule: if one of the two (focused_wrath/thunder_call) is
# EN: ready (off cooldown) and the other will be within less than
# EN: BURST_PREP_WINDOW_S, we HOLD the first (burst_hold flag, read by both
# EN: their YAML conditions) instead of firing it alone -- until BOTH are ready
# EN: (burst triggered: the normal scan presses them one after another, at most
# EN: one tick apart since both are off_gcd) or the window is exceeded
# EN: (anti-block safety: timeout, cf. below -- never stays stuck if a cooldown
# EN: drifts more than expected).
# EN:
# EN: fury_conserve: during this wait, we ALSO hold discretionary Fury spending
# EN: (normal skull_crusher, the filler that spends without being forced to) so
# EN: Fury is maximal when the burst starts -- Focused Wrath reduces by 50% the
# EN: cost of the next 2 Hammer Storm/Skull Crusher (cf.
# EN: HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED above), so more Fury in stock
# EN: at burst time = more value extracted from that window. The GENERATORS
# EN: (face_breaker/chain_lightning/wild_swing) are NOT held: on the contrary
# EN: they must keep building Fury during the wait, otherwise the burst would
# EN: start with LESS resource than possible -- the opposite of the goal. The
# EN: PROCS (hammer_storm_proc/skull_crusher_proc) are not affected either: the
# EN: project's historical "no proc must be missed" rule, never held by the
# EN: planner.
# =============================================================================
BURST_PREP_WINDOW_S = 8.0   # au-delà, retenir le premier reviendrait à geler la rotation trop longtemps pour un gain incertain -- fenêtre courte, seulement les tout derniers instants avant l'alignement · EN: beyond this, holding the first would freeze the rotation too long for an uncertain gain -- short window, only the very last moments before alignment
BURST_TIMEOUT_MARGIN_S = 2.0   # marge de sécurité au-delà de BURST_PREP_WINDOW_S avant d'abandonner l'attente -- ne bloque jamais indéfiniment si un cooldown dérive plus que prévu (ex. hard_lock Hammer Storm qui retarde tout pendant l'attente) · EN: safety margin beyond BURST_PREP_WINDOW_S before abandoning the wait -- never blocks indefinitely if a cooldown drifts more than expected (e.g. Hammer Storm hard_lock delaying everything during the wait)


def _update_tariq_planner(state: State, now: float) -> None:
    objective = state.flags.get("tariq_objective", "normal")

    fw_ready_in = state.cooldown_until.get("focused_wrath", 0.0) - now
    tc_ready_in = state.cooldown_until.get("thunder_call", 0.0) - now
    fw_ready = fw_ready_in <= 0
    tc_ready = tc_ready_in <= 0

    if objective == "prepare_burst":
        since = state.flags.get("tariq_objective_since", now)
        timed_out = now - since > BURST_PREP_WINDOW_S + BURST_TIMEOUT_MARGIN_S
        if (fw_ready and tc_ready) or timed_out:
            objective = "normal"
    elif (fw_ready and 0 < tc_ready_in <= BURST_PREP_WINDOW_S) or (tc_ready and 0 < fw_ready_in <= BURST_PREP_WINDOW_S):
        objective = "prepare_burst"
        state.flags["tariq_objective_since"] = now

    state.flags["tariq_objective"] = objective
    state.flags["burst_hold"] = objective == "prepare_burst"
    state.flags["fury_conserve"] = objective == "prepare_burst"


# =============================================================================
# CONFIRMATION DE PRESSE PAR VOILE GCD (2026-07-14, discuté avec l'utilisateur) --
# jusqu'ici, hammer_storm_proc était la SEULE ability avec un vrai accusé de
# réception côté jeu (la cast bar). Toutes les autres (heavy_strike,
# wild_swing, face_breaker, skull_crusher, chain_lightning, skull_crusher_proc,
# et les 3 off_gcd) étaient pressées "à l'aveugle" -- le bot suppose que ça a
# marché, sans aucun moyen de vérifier. Root cause du signalement de l'utilisateur
# ("je le vois visuellement, dans le code tu ne le vois pas") : cet angle
# mort touche TOUTES les abilities, pas seulement Hammer Storm.
#
# Le "voile noir décroissant" (cf. wardenbot/state_update.py,
# GCD_ICON_REGIONS/OFF_GCD_ICON_REGIONS) donne enfin ce signal, pour tout le
# kit : state.buffs["gcd_veil_active"] (OR partagé sur les 6 abilities à GCD
# commun) et state.buffs["veil_culling_strike"/"veil_thunder_call"/
# "veil_focused_wrath"] (propres à chacune des 3 off_gcd, leur voile suit
# LEUR PROPRE cooldown, pas le GCD partagé).
#
# Volontairement PUREMENT DIAGNOSTIC pour l'instant (cf. l'utilisateur : "je ne
# corrigerais pas encore, juste l'instrumentation") -- ça ne bloque, ne
# retente, ni ne modifie AUCUNE decision existante (contrairement au
# mécanisme _hammer_storm_proc_pending, qui lui continue à gater
# hammer_storm_proc_ready sur la cast bar, inchangé). Ça se contente de
# logger SUCCESS/PHANTOM_PRESS par presse dans press_confirm.log (cf.
# diagnostics.py::log_press_confirmation) -- à croiser avec input_timing.log
# (déjà là, prouve que la touche part proprement côté InputWorker) pour
# trancher entre les 4 cas de la discussion :
#   pas de voile + input jamais exécuté -> InputWorker
#   pas de voile + input exécuté        -> le jeu refuse/ignore
#   voile présent + sort non lancé      -> logique jeu/GCD/prérequis (ex: cas hammer_storm_proc déjà tracé)
#   voile présent + sort lancé          -> tout va bien
# EN: PRESS CONFIRMATION VIA GCD VEIL (2026-07-14, discussed with the user) --
# EN: until now, hammer_storm_proc was the ONLY ability with a real game-side
# EN: acknowledgement (the cast bar). All the others (heavy_strike, wild_swing,
# EN: face_breaker, skull_crusher, chain_lightning, skull_crusher_proc, and the
# EN: 3 off_gcd) were pressed "blindly" -- the bot assumes it worked, with no
# EN: way to check. Root cause of the user's report ("I see it visually, in the
# EN: code you don't see it"): this blind spot affects ALL abilities, not just
# EN: Hammer Storm.
# EN:
# EN: The "decreasing black veil" (cf. wardenbot/state_update.py,
# EN: GCD_ICON_REGIONS/OFF_GCD_ICON_REGIONS) finally gives this signal, for the
# EN: whole kit: state.buffs["gcd_veil_active"] (shared OR over the 6 abilities
# EN: on the common GCD) and state.buffs["veil_culling_strike"/
# EN: "veil_thunder_call"/"veil_focused_wrath"] (specific to each of the 3
# EN: off_gcd, their veil follows THEIR OWN cooldown, not the shared GCD).
# EN:
# EN: Deliberately PURELY DIAGNOSTIC for now (cf. the user: "I wouldn't fix it
# EN: yet, just the instrumentation") -- it neither blocks, retries, nor
# EN: modifies ANY existing decision (unlike the _hammer_storm_proc_pending
# EN: mechanism, which keeps gating hammer_storm_proc_ready on the cast bar,
# EN: unchanged). It just logs SUCCESS/PHANTOM_PRESS per press in
# EN: press_confirm.log (cf. diagnostics.py::log_press_confirmation) -- to
# EN: cross-reference with input_timing.log (already there, proves the key
# EN: leaves cleanly on the InputWorker side) to decide between the discussion's
# EN: 4 cases:
# EN:   no veil + input never executed -> InputWorker
# EN:   no veil + input executed       -> the game refuses/ignores
# EN:   veil present + spell not cast   -> game/GCD/prereq logic (e.g. the hammer_storm_proc case already traced)
# EN:   veil present + spell cast       -> all good
# =============================================================================
PRESS_CONFIRM_WINDOW_S = 1.5   # marge large au-dessus du cycle voile observé (~1-1.15s pour le GCD partagé) · EN: wide margin above the observed veil cycle (~1-1.15s for the shared GCD)

GCD_VEIL_ABILITIES = frozenset({
    "heavy_strike", "wild_swing", "face_breaker", "skull_crusher",
    "skull_crusher_proc", "hammer_storm_proc", "chain_lightning",
})
OFF_GCD_VEIL_ABILITY_BUFF_KEYS = {
    "culling_strike": "veil_culling_strike",
    "thunder_call": "veil_thunder_call",
    "focused_wrath": "veil_focused_wrath",
}


def _register_press_confirmation(state: State, pressed: str, now: float) -> None:
    if pressed not in GCD_VEIL_ABILITIES and pressed not in OFF_GCD_VEIL_ABILITY_BUFF_KEYS:
        return   # ability sans voile suivi (ex: culling_strike/thunder_call/focused_wrath sont dans OFF_GCD_..., donc bien couverts -- seules face_breaker/wild_swing/etc. via GCD_VEIL_ABILITIES et les 3 off_gcd sont trackées) · EN: ability with no tracked veil (culling_strike/thunder_call/focused_wrath are in OFF_GCD_..., so well covered -- only face_breaker/wild_swing/etc. via GCD_VEIL_ABILITIES and the 3 off_gcd are tracked)
    pending = state.flags.setdefault("_press_confirmations_pending", [])
    pending.append({
        "ability": pressed,
        "requested_at_monotonic": now,
        "requested_at_wall": time.time(),
    })


def _resolve_press_confirmations(state: State, now: float) -> None:
    # BUG TROUVÉ (2026-07-14, 1re session avec cette instrumentation) : 13 des
    # 38 PHANTOM_PRESS journalisés coïncidaient EXACTEMENT avec un cycle
    # pause/reprise (touche C) -- RotationEngine.tick() (donc tariq_pre_tick,
    # donc CETTE fonction) ne tourne pas du tout pendant une pause, mais
    # time.monotonic() continue d'avancer en temps réel : au réveil, l'écart
    # dépasse toujours largement PRESS_CONFIRM_WINDOW_S, donc tout ce qui
    # était en attente se résolvait instantanément en PHANTOM_PRESS -- alors
    # que le voile a pu apparaître ET disparaître normalement en jeu pendant
    # qu'on ne regardait pas. Fix : si l'écart depuis le dernier appel de
    # cette fonction dépasse largement le rythme normal (~60Hz), la boucle
    # vient de reprendre après un blocage (pause ou autre) -- on ABANDONNE
    # les confirmations en attente SANS les journaliser comme un échec
    # (ininterprétables), plutôt que de produire un faux PHANTOM_PRESS.
    # EN: BUG FOUND (2026-07-14, 1st session with this instrumentation): 13 of
    # EN: the 38 logged PHANTOM_PRESS coincided EXACTLY with a pause/resume
    # EN: cycle (C key) -- RotationEngine.tick() (thus tariq_pre_tick, thus THIS
    # EN: function) doesn't run at all during a pause, but time.monotonic()
    # EN: keeps advancing in real time: on wake-up, the gap always far exceeds
    # EN: PRESS_CONFIRM_WINDOW_S, so everything pending resolved instantly to
    # EN: PHANTOM_PRESS -- whereas the veil may have appeared AND disappeared
    # EN: normally in-game while we weren't looking. Fix: if the gap since this
    # EN: function's last call far exceeds the normal rate (~60Hz), the loop has
    # EN: just resumed after a block (pause or other) -- we DROP the pending
    # EN: confirmations WITHOUT logging them as a failure (uninterpretable),
    # EN: rather than producing a false PHANTOM_PRESS.
    last_seen = state.flags.get("_press_confirm_last_tick_at")
    state.flags["_press_confirm_last_tick_at"] = now
    if last_seen is not None and now - last_seen > 0.5:
        state.flags["_press_confirmations_pending"] = []
        return

    pending = state.flags.get("_press_confirmations_pending")
    if not pending:
        return
    still_pending = []
    for entry in pending:
        ability = entry["ability"]
        if ability in GCD_VEIL_ABILITIES:
            veil_seen = state.buffs.get("gcd_veil_active", False)
        else:
            veil_seen = state.buffs.get(OFF_GCD_VEIL_ABILITY_BUFF_KEYS[ability], False)

        if veil_seen:
            log_press_confirmation(ability, entry["requested_at_wall"], time.time(), "SUCCESS")
        elif now - entry["requested_at_monotonic"] > PRESS_CONFIRM_WINDOW_S:
            log_press_confirmation(ability, entry["requested_at_wall"], None, "PHANTOM_PRESS")
        else:
            still_pending.append(entry)
    state.flags["_press_confirmations_pending"] = still_pending


def tariq_pre_tick(state: State) -> Optional[str]:
    now = time.monotonic()
    _update_tariq_planner(state, now)
    _resolve_press_confirmations(state, now)
    if state.flags.get("hammer_storm_casting", False):
        state.hard_lock_until = max(state.hard_lock_until, now + HAMMER_STORM_LOCK_REFRESH_S)
        if state.flags.get("_hammer_storm_proc_pending", False):
            # Canalisation confirmée pendant la fenêtre d'attente -> le press
            # a réellement abouti, le proc est VRAIMENT consommé maintenant.
            # EN: Channel confirmed during the wait window -> the press really
            # EN: succeeded, the proc is TRULY consumed now.
            state.flags["hammer_storm_proc_ready"] = False
            state.flags["_hammer_storm_proc_pending"] = False

    pending_since = state.flags.get("_hammer_storm_proc_pending_since")
    if pending_since is not None and now - pending_since > HAMMER_STORM_PROC_CONFIRM_WINDOW_S:
        # Fenêtre expirée sans canalisation détectée -> le jeu a refusé le
        # cast (Fury réelle insuffisante, très probablement). ready n'a
        # JAMAIS été effacé (cf. tariq_post_tick) -- rien à restaurer, on
        # lève juste le marqueur d'attente pour permettre une nouvelle
        # tentative dès que les conditions sont réunies.
        # EN: Window expired with no channel detected -> the game refused the
        # EN: cast (insufficient real Fury, very likely). ready was NEVER
        # EN: cleared (cf. tariq_post_tick) -- nothing to restore, we just lift
        # EN: the pending marker to allow a new attempt as soon as the
        # EN: conditions are met.
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
    # EN: Focused Wrath reduces by 50% the Fury cost of the next 2 Hammer
    # EN: Storm/Skull Crusher (cf. the HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
    # EN: comment above) -- as soon as the buff is active (regardless of source,
    # EN: bot or the user's Leap Smash) and a proc is ready, we consume as soon as
    # EN: the REDUCED threshold is covered rather than waiting for the full
    # EN: threshold required by the normal YAML condition.
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
    _register_press_confirmation(state, pressed, time.monotonic())
    if pressed == "hammer_storm_proc":
        # Ne PAS effacer ready ici -- cf. HAMMER_STORM_PROC_CONFIRM_WINDOW_S :
        # seule une canalisation réellement détectée (tariq_pre_tick) confirme
        # la consommation. On pose juste le marqueur d'attente avec l'horodatage
        # de CETTE presse.
        # EN: Do NOT clear ready here -- cf. HAMMER_STORM_PROC_CONFIRM_WINDOW_S:
        # EN: only a channel actually detected (tariq_pre_tick) confirms the
        # EN: consumption. We just set the pending marker with THIS press's
        # EN: timestamp.
        state.flags["_hammer_storm_proc_pending"] = True
        state.flags["_hammer_storm_proc_pending_since"] = time.monotonic()
    elif pressed in TARIQ_PROC_BUFFS:
        state.flags[f"{pressed}_ready"] = False
