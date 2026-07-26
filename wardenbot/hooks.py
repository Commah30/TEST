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
Earthfist Barrage posent un stack, seul le combo défensif forcé À 3 stacks
le consomme -- rien d'autre ne peut le faire varier), donc pas besoin de
lire un pixel pour le savoir. Depuis le 2026-07-16, ce forçage alterne entre
Vortex et Strikes (cf. warden_empower_target plus bas) mais reste l'unique
consommateur des 3 stacks, quelle que soit la cible du moment.

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
EN: only the forced defensive combo AT 3 stacks consumes it -- nothing else
EN: can change it), so no need to read a pixel to know it. Since 2026-07-16,
EN: this forced cast alternates between Vortex and Strikes (cf.
EN: warden_empower_target below) but remains the sole consumer of the 3
EN: stacks, whichever target is current.
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

from .engine import CombatState, Intent
from .freshness import Freshness
from .state_update import (
    HELENA_BARS_FRESHNESS_POLICY, MEIKO_YOLO_FRESHNESS_POLICY, TARIQ_BARS_FRESHNESS_POLICY,
    TARIQ_YOLO_FRESHNESS_POLICY, VIGOUR_PARTY_FRESHNESS_POLICY,
)
from wardenbot.diagnostics import log_press_confirmation

FINISHER_DELAY_S = 0.15   # laisse au jeu le temps d'enregistrer la 2e pièce · EN: gives the game time to register the 2nd piece
WARDEN_STACKS_TO_EMPOWER = 3
WARDEN_STACK_TIMEOUT_S = 10.0

# BUG TROUVÉ EN JEU (2026-07-17, rapport de session l'utilisateur) : "Rising Storm ->
# Lashing Stormkick -> Rising Storm" (reset prématuré) au lieu de "Rising
# Storm -> Lashing Stormkick -> Lashing Stormkick -> Rising Storm" -- la
# condition d'origine ne dépensait Stormfall/Earthfall QUE quand les 2 orbes
# étaient allumés (>= 0.999, cf. `if last == "wind_kick"/"earth_fist":`
# ci-dessous) alors que Lashing Stormkick/Earthfist Barrage ne consomme
# qu'UN SEUL orbe par cast (confirmé par la séquence attendue par l'utilisateur :
# 2 Lashing Stormkick de suite avant de revenir à Rising Storm). Résultat :
# dès qu'un orbe restait (stormfall=0.5 après une 1re dépense), la condition
# `>= 0.999` redevenait fausse et le hook repartait sur "build" (Rising
# Storm) au lieu de dépenser le 2e orbe -- gaspillant une charge déjà
# disponible. Root cause identique à ce que l'audit d'architecture (Phase 0,
# 2026-07-14) documentait déjà en théorie ("angle mort de fraîcheur") mais
# ici c'est un bug de SEUIL, pas de latence : le code traitait Stormfall/
# Earthfall comme un booléen (plein/vide) alors que c'est une vraie
# ressource à 2 charges (cf. state_update.py, 0.0/0.5/1.0). Fix : dépenser
# dès qu'AU MOINS un orbe est disponible (> 0), pas seulement les 2.
# EN: BUG FOUND IN-GAME (2026-07-17, the user's session report): "Rising Storm
# EN: -> Lashing Stormkick -> Rising Storm" (premature reset) instead of
# EN: "Rising Storm -> Lashing Stormkick -> Lashing Stormkick -> Rising Storm"
# EN: -- the original condition only spent Stormfall/Earthfall when BOTH orbs
# EN: were lit (>= 0.999, cf. `if last == "wind_kick"/"earth_fist":` below)
# EN: while Lashing Stormkick/Earthfist Barrage only consumes ONE orb per cast
# EN: (confirmed by the sequence the user expects: 2 Lashing Stormkick in a row
# EN: before returning to Rising Storm). Result: as soon as one orb remained
# EN: (stormfall=0.5 after a first spend), the `>= 0.999` condition became
# EN: false again and the hook went back to "build" (Rising Storm) instead of
# EN: spending the 2nd orb -- wasting an already-available charge. Same root
# EN: cause the architecture audit (Phase 0, 2026-07-14) already documented in
# EN: theory ("freshness blind spot") but here it's a THRESHOLD bug, not
# EN: latency: the code treated Stormfall/Earthfall as a boolean (full/empty)
# EN: when it's a real 2-charge resource (cf. state_update.py, 0.0/0.5/1.0).
# EN: Fix: spend as soon as AT LEAST one orb is available (> 0), not only both.
STORM_EARTH_SPEND_THRESHOLD = 0.0

# DEMANDE DE L'UTILISATEUR (2026-07-16) : Spirited Strikes dure 30s -- au lieu
# d'attendre qu'il tombe à False (ce qui, combiné au bug de détection ci-
# dessous, causait un spam du combo défensif), on le refresh PROACTIVEMENT
# quand il reste ≤N secondes, en lisant le VRAI compte à rebours affiché sur
# l'icône (OCR, cf. wardenbot/state_update.py::MeikoDetectionSnapshot --
# jamais une estimation dérivée d'un timer local, cf. principe du projet).
# Spirited Vortex, lui, ne suit PAS cette règle : son SEUL déclencheur de
# refresh reste la règle des 3 stacks de Warden of the Temple (demande
# explicite de l'utilisateur) -- jamais basé sur sa propre durée restante.
#
# 5s -> 10s (2026-07-18, rapport l'utilisateur : "perd trop d'uptime sur Spirited
# Strikes") : 5s ne laissait quasiment aucune marge. Le refresh n'est
# proposé qu'au prochain passage par "rien en cours" (entre deux cycles du
# combo Storm/Earth en cours) -- dans le pire cas, ça peut attendre la fin
# du cycle en cours (~2.35s : GCD+GCD+FINISHER_DELAY_S) AVANT même de
# commencer le combo de refresh (encore ~2.35s). Pire cas total ~4.7s rien
# que pour le temps de jeu, avant même de compter la latence de détection
# (OCR throttlé ~400ms, cf. MEIKO_OCR_INTERVAL_S). Avec un seuil à 5s, la
# marge réelle avoisinait 0.3s -- assez pour que le buff tombe avant que le
# refresh n'aboutisse, perdant l'uptime que ce mécanisme est censé
# préserver. 10s laisse ~5.3s de marge, largement au-dessus du pire cas.
# EN: THE USER'S REQUEST (2026-07-16): Spirited Strikes lasts 30s -- instead of
# EN: waiting for it to drop to False (which, combined with the detection bug
# EN: below, caused the defensive combo to spam), it's refreshed PROACTIVELY
# EN: when ≤N seconds remain, by reading the REAL countdown displayed on the
# EN: icon (OCR, cf. wardenbot/state_update.py::MeikoDetectionSnapshot --
# EN: never an estimate derived from a local timer, cf. project principle).
# EN: Spirited Vortex does NOT follow this rule: its ONLY refresh trigger
# EN: stays the Warden of the Temple 3-stack rule (the user's explicit
# EN: request) -- never based on its own remaining duration.
# EN:
# EN: 5s -> 10s (2026-07-18, the user's report: "losing too much uptime on
# EN: Spirited Strikes"): 5s left almost no margin. The refresh is only
# EN: proposed on the next pass through "nothing in progress" (between two
# EN: cycles of the ongoing Storm/Earth combo) -- worst case, it can wait for
# EN: the current cycle to finish (~2.35s: GCD+GCD+FINISHER_DELAY_S) BEFORE
# EN: even starting the refresh combo (another ~2.35s). Total worst case
# EN: ~4.7s of pure game time, before even counting detection latency (OCR
# EN: throttled ~400ms, cf. MEIKO_OCR_INTERVAL_S). With a 5s threshold, the
# EN: real margin was around 0.3s -- enough for the buff to drop before the
# EN: refresh landed, losing the very uptime this mechanism is meant to
# EN: preserve. 10s leaves ~5.3s of margin, comfortably above the worst case.
STRIKES_PROACTIVE_REFRESH_WINDOW_S = 10.0

# BUG TROUVÉ EN JEU (2026-07-16, log Meiko analysé avec l'utilisateur) : "rien n'est
# lancé comme il devrait à part les premiers combo" -- deux symptômes, même
# root cause. (1) Vortex castée 2 fois de suite puis Strikes 3 fois de suite
# avant que la boucle Storm/Earth démarre enfin : capture_watchdog.log
# confirme deux cycles du thread de détection Meiko à 2.551s et 3.490s
# (seuil 0.2s, contention GPU -- même phénomène déjà documenté côté Tariq,
# cf. yolo_detect.py::_select_device) pile aux mêmes instants. state.buffs
# restait donc figé sur la valeur PRÉ-cast pendant tout le hoquet, et le hook
# recastait une ability qui venait littéralement d'être posée. (2) Une fois
# primé, le refresh proactif de Strikes tournait en boucle PENDANT PLUS DE
# 25 SECONDES d'affilée (spirited_strikes_remaining_s bloqué à exactement
# 5.0 tick après tick) -- le hook re-déclenchait un refresh avant même que
# la lecture OCR ait eu le temps de refléter le cast qu'on venait de faire.
#
# Fix : même principe que _hammer_storm_proc_pending côté Tariq (cf. plus
# bas dans ce fichier) -- après avoir décidé un cast, on marque une fenêtre
# de confirmation pendant laquelle on NE RE-DÉCLENCHE PAS le même motif,
# qu'on soit encore en train d'attendre que la détection rattrape un hoquet,
# ou que le cast précédent ait simplement échoué en jeu. 6s de marge :
# largement au-dessus d'un combo complet (~2.35s, GCD+GCD+FINISHER_DELAY)
# ET du pire hoquet observé (3.49s) -- si RIEN ne s'est confirmé après ça,
# on considère que le cast précédent a réellement échoué et on retente.
# EN: BUG FOUND IN-GAME (2026-07-16, Meiko log analyzed with the user): "nothing
# EN: fires as it should except the first combos" -- two symptoms, same root
# EN: cause. (1) Vortex cast twice in a row then Strikes three times in a row
# EN: before the Storm/Earth loop finally started: capture_watchdog.log
# EN: confirms two Meiko detection thread cycles at 2.551s and 3.490s
# EN: (threshold 0.2s, GPU contention -- same phenomenon already documented on
# EN: the Tariq side, cf. yolo_detect.py::_select_device) at those exact
# EN: moments. state.buffs thus stayed frozen on the PRE-cast value for the
# EN: whole hiccup, and the hook recast an ability that had literally just
# EN: been applied. (2) Once primed, the Strikes proactive refresh looped for
# EN: MORE THAN 25 SECONDS straight (spirited_strikes_remaining_s stuck at
# EN: exactly 5.0 tick after tick) -- the hook kept re-triggering a refresh
# EN: before the OCR reading even had time to reflect the cast we had just
# EN: made.
# EN:
# EN: Fix: same principle as _hammer_storm_proc_pending on the Tariq side (cf.
# EN: below in this file) -- after deciding a cast, we mark a confirmation
# EN: window during which we do NOT re-trigger the same reason, whether we're
# EN: still waiting for detection to catch up on a hiccup, or the previous
# EN: cast genuinely failed in-game. 6s margin: comfortably above a full combo
# EN: (~2.35s, GCD+GCD+FINISHER_DELAY) AND the worst hiccup observed (3.49s)
# EN: -- if NOTHING has been confirmed after that, we assume the previous cast
# EN: really failed and retry.
MEIKO_DEFENSE_CONFIRM_WINDOW_S = 6.0


def _set_warden_stacks(state: CombatState, now: float, value: int) -> None:
    value = max(0, min(value, WARDEN_STACKS_TO_EMPOWER))
    was_empowered = state.hooks.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER
    state.hooks["warden_stacks"] = value
    is_empowered = value >= WARDEN_STACKS_TO_EMPOWER
    if is_empowered and not was_empowered:
        state.hooks["warden_stacks_since"] = now
    elif not is_empowered:
        state.hooks.pop("warden_stacks_since", None)


def _reconcile_warden_stacks(state: CombatState, now: float) -> None:
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
    since = state.hooks.get("warden_stacks_since")
    if since is not None and now - since > WARDEN_STACK_TIMEOUT_S:
        _set_warden_stacks(state, now, 0)


def meiko_pre_tick(state: CombatState) -> list[Intent]:
    """
    Câblé comme pre_tick_hook (cf. RotationEngine, engine.py::Intent) -- doit
    retourner une liste d'Intent, pas un nom nu. Toute la logique de décision
    (combo en cours, quelle 2e pièce, refresh du combo défensif...) reste
    intacte dans _meiko_choose_combo_step ci-dessous, un simple
    Optional[str] en interne -- bien plus simple à lire/déboguer que si
    chacun de ses ~14 points de retour manipulait Intent directement. Poids
    fixe de 90 (cf. Intent dans engine.py) : reproduit fidèlement l'ancien
    comportement de "GOTO absolu" (ce nom gagnait TOUJOURS contre le scan de
    priorité YAML, poids 50) -- migration mécanique, zéro changement de
    comportement.

    EN: Wired as pre_tick_hook (cf. RotationEngine, engine.py::Intent) -- must
    EN: return a list of Intent, not a bare name. All the decision logic
    EN: (in-progress combo, which 2nd piece, defensive combo refresh...) stays
    EN: intact in _meiko_choose_combo_step below, a plain Optional[str]
    EN: internally -- far simpler to read/debug than if each of its ~14
    EN: return points manipulated Intent directly. Fixed weight of 90 (cf.
    EN: Intent in engine.py): faithfully reproduces the former "absolute GOTO"
    EN: behavior (this name ALWAYS won against the YAML priority scan, weight
    EN: 50) -- mechanical migration, zero behavior change.
    """
    ability = _meiko_choose_combo_step(state)
    return [Intent(ability=ability, weight=90, source="meiko_pre_tick")] if ability else []


def _meiko_choose_combo_step(state: CombatState) -> Optional[str]:
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
        combo = state.hooks.pop("_pending_stack_combo", None)
        if combo == "grant":
            _set_warden_stacks(state, now, state.hooks.get("warden_stacks", 0) + 1)
        elif combo == "reset":
            _set_warden_stacks(state, now, 0)
            # Bascule warden_empower_target UNIQUEMENT ici, à la confirmation
            # réelle du cast -- jamais au moment de la décision plus bas. Même
            # principe que grant/reset ci-dessus : si ce tick n'est qu'un
            # "aperçu" jamais réellement pressé (appel de test isolé, ou en
            # prod un hook affaibli par weight_overrides qui perd face au scan
            # -- cf. RotationEngine.tick), l'alternance ne doit PAS avancer,
            # sinon elle dérive sans qu'aucun cast n'ait eu lieu.
            # EN: Only flip warden_empower_target HERE, at the real confirmation
            # EN: of the cast -- never at decision time below. Same principle as
            # EN: grant/reset above: if this tick is only a "preview" that never
            # EN: actually got pressed (an isolated test call, or in prod a hook
            # EN: weakened by weight_overrides that loses to the scan -- cf.
            # EN: RotationEngine.tick), the alternation must NOT advance, or it
            # EN: drifts without any cast having happened.
            used_target = state.hooks.pop("_pending_warden_empower_flip", "vortex")
            state.hooks["warden_empower_target"] = "strikes" if used_target == "vortex" else "vortex"
        return "fighting_techniques"

    # --- Tout ce qui suit presse une VRAIE touche soumise au GCD -- on
    # n'agit que si le GCD de la frappe précédente est terminé. ---
    # EN: --- Everything below presses a REAL key subject to the GCD -- we only
    # EN: act if the previous strike's GCD is over. ---
    if now < state.gcd_until:
        return None

    # FRAÎCHEUR (audit architecture, 2026-07-17) : jusqu'ici, meiko_pre_tick
    # ne consultait JAMAIS state.detection_meta -- seul Vigour le faisait
    # (cf. vigour_pre_tick). Pourtant meiko_yolo EST rempli à chaque tick par
    # state_update_fn (cf. state_update.py) et sert de source à vortex_up/
    # strikes_up/stormfall/earthfall juste en dessous : sans ce gate, un
    # thread de détection bloqué (cf. CLAUDE.md, épisodes de snapshot figé
    # déjà rencontrés côté Tariq) ferait décider ce hook sur un état de buff
    # gelé indéfiniment, sans qu'aucun symptôme visible ne le trahisse. Placé
    # APRÈS la branche finisher ci-dessus (qui ne lit aucun buff, juste
    # FINISHER_DELAY_S -- la bloquer laisserait un combo à moitié pressé) et
    # APRÈS le gate GCD (cohérent avec l'ordre déjà établi). Retourne None
    # (pas de décision) -- le scan de priorité YAML reprend alors la main
    # avec un ouvreur sûr (earth_fist/wind_kick) plutôt que d'agir sur du
    # périmé.
    # EN: FRESHNESS (architecture audit, 2026-07-17): until now,
    # EN: meiko_pre_tick never consulted state.detection_meta at all -- only
    # EN: Vigour did (cf. vigour_pre_tick). Yet meiko_yolo IS filled every tick
    # EN: by state_update_fn (cf. state_update.py) and feeds vortex_up/
    # EN: strikes_up/stormfall/earthfall right below: without this gate, a
    # EN: stuck detection thread (cf. CLAUDE.md, frozen-snapshot episodes
    # EN: already hit on the Tariq side) would make this hook decide on a
    # EN: buff state frozen indefinitely, with no visible symptom giving it
    # EN: away. Placed AFTER the finisher branch above (which reads no buff,
    # EN: just FINISHER_DELAY_S -- blocking it would leave a combo half-
    # EN: pressed) and AFTER the GCD gate (consistent with the order already
    # EN: established). Returns None (no decision) -- the YAML priority scan
    # EN: then takes over with a safe opener (earth_fist/wind_kick) rather
    # EN: than acting on stale data.
    meiko_meta = state.detection_meta.get("meiko_yolo")
    if meiko_meta is None or MEIKO_YOLO_FRESHNESS_POLICY.classify(meiko_meta.age_ms(now=now)) == Freshness.EXPIRED:
        return None

    # --- Storm (Wind Kick 1re pièce) : build ou spend selon la charge Stormfall ---
    # EN: --- Storm (Wind Kick 1st piece): build or spend depending on the Stormfall charge ---
    if last == "wind_kick":
        if state.resources.get("stormfall", 0.0) > STORM_EARTH_SPEND_THRESHOLD:
            state.hooks["_pending_stack_combo"] = "grant"   # Lashing Stormkick -> +1 stack
            return "earth_fist_2"
        state.hooks.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Storm

    # --- Earth (Earth Fist 1re pièce) : build ou spend selon la charge Earthfall ---
    # EN: --- Earth (Earth Fist 1st piece): build or spend depending on the Earthfall charge ---
    if last == "earth_fist":
        if state.resources.get("earthfall", 0.0) > STORM_EARTH_SPEND_THRESHOLD:
            state.hooks["_pending_stack_combo"] = "grant"   # Earthfist Barrage -> +1 stack
            return "wind_kick_2"
        state.hooks.pop("_pending_stack_combo", None)
        return "spirit_palm_2"        # build -> Rising Earth

    # --- Defensive (Spirit Palm 1re pièce) : LA décision qu'on ne pouvait
    # jamais garantir auparavant. Basée sur l'état RÉEL des buffs, pas une
    # estimation. ---
    # EN: --- Defensive (Spirit Palm 1st piece): THE decision we could never
    # EN: guarantee before. Based on the REAL buff state, not an estimate. ---
    if last == "spirit_palm":
        vortex_up = state.buffs.get("spirited_vortex", False)
        strikes_up = state.buffs.get("spirited_strikes", False)

        if state.hooks.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER:
            # 3 stacks confirmés -> combo défensif forcé à coup sûr, peu
            # importe l'état des buffs. Ce cast-là (et LUI SEUL) consomme les
            # 3 stacks.
            #
            # CHANGEMENT DE RÈGLE (2026-07-16, demande explicite de l'utilisateur) :
            # forcer TOUJOURS Spirited Vortex à 3 stacks n'est plus la règle
            # voulue -- on ALTERNE désormais la cible du forçage : 3 stacks ->
            # Vortex, les 3 stacks suivants -> Spirited Strikes, les 3
            # suivants -> Vortex, etc. warden_empower_target (state.hooks)
            # retient la cible du PROCHAIN forçage et bascule à chaque fois
            # que les 3 stacks sont consommées ici. Démarre sur "vortex" (1re
            # activation du combat = comportement d'origine).
            # EN: RULE CHANGE (2026-07-16, the user's explicit request): ALWAYS
            # EN: forcing Spirited Vortex at 3 stacks is no longer the wanted
            # EN: rule -- we now ALTERNATE the forced target: 3 stacks ->
            # EN: Vortex, next 3 stacks -> Spirited Strikes, next 3 stacks ->
            # EN: Vortex, etc. warden_empower_target (state.hooks) remembers
            # EN: the target of the NEXT forced cast and flips every time the
            # EN: 3 stacks are consumed here. Starts on "vortex" (the fight's
            # EN: 1st activation = original behavior).
            # Lecture SEULE ici -- le flip réel n'a lieu qu'à la confirmation
            # du cast (cf. bloc "2e pièce déjà pressée" plus haut), jamais à
            # la décision. On note juste la cible choisie CE tick dans
            # _pending_warden_empower_flip pour que la confirmation sache
            # quoi inverser le moment venu.
            # EN: READ ONLY here -- the real flip only happens at cast
            # EN: confirmation (cf. the "2nd piece already pressed" block
            # EN: above), never at decision time. Just note THIS tick's chosen
            # EN: target in _pending_warden_empower_flip so confirmation knows
            # EN: what to invert once it lands.
            target = state.hooks.get("warden_empower_target", "vortex")
            state.hooks["_pending_warden_empower_flip"] = target

            # BUG TROUVÉ EN JEU (2026-07-16, rapporté par l'utilisateur) : "la
            # rotation spam Vortex/Strikes" -- après CE cast forcé, on
            # dé-prime volontairement (meiko_defense_primed=False) pour
            # laisser "rien en cours" re-vérifier UNE fois l'AUTRE buff (celui
            # qui n'a PAS reçu ce forçage-ci, souvent retombé pendant les 3
            # cycles Storm/Earth) avant de retourner à la boucle. Marche
            # symétriquement pour les deux cibles : la logique "rien en
            # cours" qui suit vérifie vortex_up ET strikes_up indépendamment.
            # cf. meiko_defense_primed plus bas pour le mécanisme complet.
            # EN: BUG FOUND IN-GAME (2026-07-16, reported by the user): "the
            # EN: rotation spams Vortex/Strikes" -- after THIS forced cast, we
            # EN: deliberately un-prime (meiko_defense_primed=False) so "rien
            # EN: en cours" re-checks the OTHER buff once (the one that did NOT
            # EN: receive this forced cast, often dropped during the 3
            # EN: Storm/Earth cycles) before returning to the loop. Works
            # EN: symmetrically for either target: the "rien en cours" logic
            # EN: that follows checks vortex_up AND strikes_up independently.
            # EN: cf. meiko_defense_primed below for the full mechanism.
            state.hooks["_pending_stack_combo"] = "reset"
            state.hooks["meiko_defense_primed"] = False
            return "earth_fist_2" if target == "vortex" else "wind_kick_2"

        if state.hooks.get("meiko_defense_primed", False):
            # DEMANDE DE L'UTILISATEUR (2026-07-16) : une fois primé, Vortex ne doit
            # PLUS JAMAIS être re-casté ici -- son seul déclencheur est la
            # règle des 3 stacks ci-dessus (déjà traitée, on ne peut plus y
            # être arrivé si on est ici). Le seul autre motif d'ouverture une
            # fois primé est le refresh (pro)actif de Strikes (cf. "rien en
            # cours" plus bas) -- on ignore délibérément vortex_up pour ne
            # pas dépendre d'un flicker de détection ponctuel.
            # EN: THE USER'S REQUEST (2026-07-16): once primed, Vortex must NEVER
            # EN: be re-cast here -- its only trigger is the 3-stack rule above
            # EN: (already handled, we can't have gotten here if that fired).
            # EN: The only other reason to open once primed is the
            # EN: (pro)active Strikes refresh (cf. "rien en cours" below) -- we
            # EN: deliberately ignore vortex_up so as not to depend on a
            # EN: one-off detection flicker.
            state.hooks.pop("_pending_stack_combo", None)
            return "wind_kick_2"

        # Phase d'ouverture initiale (pas encore primé) : Vortex en premier
        # si manquant, comme avant ce fix -- MAIS avec une fenêtre de
        # confirmation (cf. MEIKO_DEFENSE_CONFIRM_WINDOW_S) : si on vient
        # JUSTE de tenter ce cast, on ne le retente pas juste parce que la
        # détection n'a pas encore rattrapé un hoquet -- on considère qu'il a
        # peut-être déjà réussi, et on laisse la fenêtre s'écouler avant de
        # décider que ça a réellement échoué.
        # EN: Initial opening phase (not yet primed): Vortex first if missing,
        # EN: same as before this fix -- BUT with a confirmation window (cf.
        # EN: MEIKO_DEFENSE_CONFIRM_WINDOW_S): if we JUST attempted this cast,
        # EN: we don't retry it just because detection hasn't caught up on a
        # EN: hiccup yet -- we assume it may have already succeeded, and let
        # EN: the window elapse before deciding it genuinely failed.
        vortex_pending_since = state.hooks.get("_vortex_open_pending_since")
        vortex_recently_attempted = (
            vortex_pending_since is not None and now - vortex_pending_since < MEIKO_DEFENSE_CONFIRM_WINDOW_S
        )
        if not vortex_up and not vortex_recently_attempted:
            # Vortex casté hors 3-stacks -> les stacks en cours ne sont PAS
            # consommées (seul un Vortex à 3 stacks les consomme).
            # EN: Vortex cast outside 3-stacks -> the current stacks are NOT
            # EN: consumed (only a Vortex at 3 stacks consumes them).
            state.hooks["_vortex_open_pending_since"] = now
            state.hooks.pop("_pending_stack_combo", None)
            return "earth_fist_2"     # Vortex manquant -> priorité · EN: Vortex missing -> priority

        strikes_pending_since = state.hooks.get("_strikes_open_pending_since")
        strikes_recently_attempted = (
            strikes_pending_since is not None and now - strikes_pending_since < MEIKO_DEFENSE_CONFIRM_WINDOW_S
        )
        if not strikes_up and not strikes_recently_attempted:
            state.hooks["_strikes_open_pending_since"] = now
            state.hooks.pop("_pending_stack_combo", None)
            return "wind_kick_2"      # sinon Strikes manquant -> le prendre · EN: else Strikes missing -> take it
        # Les deux sont déjà up, ou en attente de confirmation (cas limite,
        # ne devrait pas arriver souvent puisqu'on n'ouvre ce combo QUE quand
        # l'un des deux manque, cf. plus bas) -> défaut sûr sur Strikes
        # plutôt que de ne rien retourner.
        # EN: Both are already up, or awaiting confirmation (edge case,
        # EN: shouldn't happen often since we only open this combo WHEN one of
        # EN: the two is missing, cf. below) -> safe default on Strikes rather
        # EN: than returning nothing.
        state.hooks.pop("_pending_stack_combo", None)
        return "wind_kick_2"

    # --- Rien en cours : faut-il OUVRIR le combo défensif maintenant ? ---
    # BUG TROUVÉ EN JEU (2026-07-16, rapporté par l'utilisateur) : "la rotation ne
    # fait que spam le combo Spirited Vortex/Strikes" -- avant ce fix,
    # needs_refresh redevenait vrai dès qu'UN SEUL des deux buffs retombait,
    # même en PLEIN MILIEU de la boucle Storm/Earth (Wind Kick/Earth Fist) --
    # si Vortex/Strikes durent moins longtemps qu'un cycle complet de 3
    # Lashing Stormkick, ce qui semble être le cas, le combo défensif
    # reprenait la main à CHAQUE fois, et la boucle Storm/Earth n'avait
    # jamais l'occasion de tourner plus d'un cycle -- exactement le spam
    # observé. Séquence attendue par l'utilisateur : ouvrir Vortex puis Strikes une
    # FOIS en début de combat, PUIS laisser la boucle Storm/Earth tourner
    # sans interruption jusqu'à 3 stacks (même si Vortex/Strikes retombent
    # entre-temps), forcer le combo à 3 stacks (Vortex ou Strikes en
    # alternance, cf. warden_empower_target plus haut), refresh l'autre buff
    # si besoin à CE moment précis, puis reprendre la boucle -- cycle qui se
    # répète.
    #
    # meiko_defense_primed (flag) : True une fois que les deux buffs ont été
    # confirmés up au moins une fois -- tant qu'il est True, une chute
    # ponctuelle de Vortex/Strikes en pleine boucle Storm/Earth n'interrompt
    # PLUS rien (on laisse `return None` -> le scan de priorité normal
    # reprend earth_fist, l'ouvreur ST). Remis à False à deux moments
    # seulement : (1) au tout début du combat (valeur par défaut), pour
    # forcer le double-refresh initial ; (2) juste après le cast forcé à 3
    # stacks (cf. plus haut -- Vortex OU Strikes selon l'alternance), pour
    # laisser UNE relecture de l'AUTRE buff avant de re-primer et replonger
    # dans la boucle. warden_stacks>=3 reste un déclencheur ABSOLU, primed ou
    # non -- jamais retardé.
    # EN: --- Nothing in progress: should we OPEN the defensive combo now? ---
    # EN: BUG FOUND IN-GAME (2026-07-16, reported by the user): "the rotation
    # EN: just spams the Spirited Vortex/Strikes combo" -- before this fix,
    # EN: needs_refresh went true again as soon as EITHER buff dropped, even
    # EN: RIGHT IN THE MIDDLE of the Storm/Earth loop (Wind Kick/Earth Fist) --
    # EN: if Vortex/Strikes last less than a full 3-Lashing-Stormkick cycle,
    # EN: which seems to be the case, the defensive combo took back control
    # EN: EVERY time, and the Storm/Earth loop never got to run more than one
    # EN: cycle -- exactly the observed spam. Sequence the user expects: open
    # EN: Vortex then Strikes ONCE at the start of the fight, THEN let the
    # EN: Storm/Earth loop run uninterrupted until 3 stacks (even if
    # EN: Vortex/Strikes drop in between), force the combo at 3 stacks
    # EN: (Vortex or Strikes alternating, cf. warden_empower_target above),
    # EN: refresh the other buff if needed at THAT exact moment, then resume
    # EN: the loop -- cycle repeats.
    # EN:
    # EN: meiko_defense_primed (flag): True once both buffs have been
    # EN: confirmed up at least once -- while True, a one-off drop of
    # EN: Vortex/Strikes mid-Storm/Earth-loop no longer interrupts anything
    # EN: (we `return None` -> the normal priority scan resumes earth_fist,
    # EN: the ST opener). Reset to False at only two moments: (1) the very
    # EN: start of the fight (default value), to force the initial double
    # EN: refresh; (2) right after the forced cast at 3 stacks (cf. above --
    # EN: Vortex OR Strikes depending on the alternation), to allow ONE
    # EN: re-check of the OTHER buff before re-priming and diving back into
    # EN: the loop. warden_stacks>=3 stays an ABSOLUTE trigger, primed or not
    # EN: -- never delayed.
    vortex_up = state.buffs.get("spirited_vortex", False)
    strikes_up = state.buffs.get("spirited_strikes", False)
    stacks_maxed = state.hooks.get("warden_stacks", 0) >= WARDEN_STACKS_TO_EMPOWER
    primed = state.hooks.get("meiko_defense_primed", False)

    # DEMANDE DE L'UTILISATEUR (2026-07-16) : une fois primé, Strikes se refresh
    # PROACTIVEMENT quand il reste ≤STRIKES_PROACTIVE_REFRESH_WINDOW_S
    # (lecture OCR réelle, cf. wardenbot/state_update.py) -- PAS quand il
    # tombe à False. `> 0` exclut une lecture 0.0/négative aberrante (buff
    # déjà retombé entre le dernier cycle OCR et maintenant -- dans ce cas
    # spirited_strikes lui-même serait déjà False, géré par la phase
    # d'ouverture initiale si jamais primed retombait aussi à False).
    # EN: THE USER'S REQUEST (2026-07-16): once primed, Strikes is refreshed
    # EN: PROACTIVELY when ≤STRIKES_PROACTIVE_REFRESH_WINDOW_S remain (real OCR
    # EN: reading, cf. wardenbot/state_update.py) -- NOT when it drops to
    # EN: False. `> 0` excludes a stray 0.0/negative read (buff already dropped
    # EN: between the last OCR cycle and now -- in that case spirited_strikes
    # EN: itself would already be False, handled by the initial opening phase
    # EN: if primed also happened to drop back to False).
    # Fenêtre de confirmation (cf. MEIKO_DEFENSE_CONFIRM_WINDOW_S plus haut) :
    # si on a déjà déclenché un refresh proactif récemment, on n'en
    # redéclenche pas un autre tant qu'il n'est ni confirmé (lecture fraîche
    # nettement au-dessus du seuil) ni la fenêtre expirée -- sinon un hoquet
    # de détection fait tourner ce refresh en boucle indéfiniment (bug
    # observé : plus de 25s d'affilée, cf. commentaire plus haut).
    # EN: Confirmation window (cf. MEIKO_DEFENSE_CONFIRM_WINDOW_S above): if a
    # EN: proactive refresh was already triggered recently, we don't trigger
    # EN: another one until it's either confirmed (a fresh reading clearly
    # EN: above the threshold) or the window has expired -- otherwise a
    # EN: detection hiccup makes this refresh loop indefinitely (bug observed:
    # EN: 25+ seconds straight, cf. comment above).
    strikes_remaining_s = state.resources.get("spirited_strikes_remaining_s")
    strikes_refresh_pending_since = state.hooks.get("_strikes_refresh_pending_since")
    strikes_refresh_recently_triggered = (
        strikes_refresh_pending_since is not None
        and now - strikes_refresh_pending_since < MEIKO_DEFENSE_CONFIRM_WINDOW_S
    )
    strikes_about_to_expire = (
        primed
        and not strikes_refresh_recently_triggered
        and strikes_remaining_s is not None
        and 0 < strikes_remaining_s <= STRIKES_PROACTIVE_REFRESH_WINDOW_S
    )
    if strikes_remaining_s is not None and strikes_remaining_s > STRIKES_PROACTIVE_REFRESH_WINDOW_S:
        # Lecture fraîche confirmant que le refresh a réellement abouti (ou
        # que rien n'était nécessaire) -- lève la fenêtre d'attente.
        # EN: Fresh reading confirming the refresh actually succeeded (or that
        # EN: nothing was needed) -- lifts the wait window.
        state.hooks.pop("_strikes_refresh_pending_since", None)

    # BUG TROUVÉ EN JEU (2026-07-17, rapport de session l'utilisateur) : "Spirited
    # Strikes lancé deux fois de suite" en ouverture -- needs_refresh
    # réagissait à vortex_up/strikes_up BRUTS, sans jamais consulter les
    # fenêtres de confirmation (_vortex_open_pending_since/
    # _strikes_open_pending_since) que la branche `if last == "spirit_palm":`
    # plus bas tient déjà à jour. Séquence exacte observée : Strikes castée ->
    # finisher -> "rien en cours" relu AVANT que la détection (YOLO, throttlé
    # ~150ms, cf. state_update.py) n'ait rattrapé le nouveau buff ->
    # needs_refresh redevenait vrai à tort (strikes_up encore lu False) ->
    # réouvre spirit_palm -> DANS la branche, `not strikes_up and not
    # strikes_recently_attempted` est cette fois FAUX (on est dans la
    # fenêtre) donc le check ciblé est sauté -> tombe sur le "défaut sûr"
    # en fin de branche, qui recastait Strikes AVEUGLÉMENT (cf. commentaire
    # "cas limite" plus bas) -- d'où le double-cast. Fix ICI, à la source :
    # needs_refresh ne doit considérer un buff comme "manquant" QUE s'il
    # n'est ni up NI en attente de confirmation récente -- exactement le
    # même garde-fou que la branche spirit_palm applique déjà en interne,
    # remonté au niveau de la décision "faut-il seulement rouvrir".
    # EN: BUG FOUND IN-GAME (2026-07-17, the user's session report): "Spirited
    # EN: Strikes cast twice in a row" on opening -- needs_refresh reacted to
    # EN: RAW vortex_up/strikes_up, never consulting the confirmation windows
    # EN: (_vortex_open_pending_since/_strikes_open_pending_since) that the
    # EN: `if last == "spirit_palm":` branch below already keeps up to date.
    # EN: Exact sequence observed: Strikes cast -> finisher -> "nothing in
    # EN: progress" re-read BEFORE detection (YOLO, throttled ~150ms, cf.
    # EN: state_update.py) had caught up on the new buff -> needs_refresh
    # EN: wrongly became true again (strikes_up still read False) -> reopens
    # EN: spirit_palm -> INSIDE the branch, `not strikes_up and not
    # EN: strikes_recently_attempted` is now FALSE (we're within the window)
    # EN: so the targeted check is skipped -> falls to the "safe default" at
    # EN: the end of the branch, which BLINDLY recast Strikes (cf. the "edge
    # EN: case" comment below) -- hence the double-cast. Fix HERE, at the
    # EN: source: needs_refresh must only consider a buff "missing" if it's
    # EN: NEITHER up NOR recently pending confirmation -- exactly the same
    # EN: guardrail the spirit_palm branch already applies internally, raised
    # EN: up to the "should we even reopen" decision.
    vortex_open_pending_since = state.hooks.get("_vortex_open_pending_since")
    vortex_open_recently_attempted = (
        vortex_open_pending_since is not None and now - vortex_open_pending_since < MEIKO_DEFENSE_CONFIRM_WINDOW_S
    )
    strikes_open_pending_since = state.hooks.get("_strikes_open_pending_since")
    strikes_open_recently_attempted = (
        strikes_open_pending_since is not None and now - strikes_open_pending_since < MEIKO_DEFENSE_CONFIRM_WINDOW_S
    )
    vortex_genuinely_missing = not vortex_up and not vortex_open_recently_attempted
    strikes_genuinely_missing = not strikes_up and not strikes_open_recently_attempted

    needs_refresh = stacks_maxed or strikes_about_to_expire or (
        not primed and (vortex_genuinely_missing or strikes_genuinely_missing)
    )
    if needs_refresh:
        if strikes_about_to_expire and not stacks_maxed:
            state.hooks["_strikes_refresh_pending_since"] = now
        return "spirit_palm"

    if not primed and vortex_up and strikes_up:
        state.hooks["meiko_defense_primed"] = True
        # Les deux confirmés -> les fenêtres d'attente de l'ouverture
        # initiale n'ont plus lieu d'être (cf. phase d'ouverture initiale
        # plus haut) -- les laisser traîner pourrait bloquer à tort un futur
        # VRAI refresh bien plus tard dans le combat.
        # EN: Both confirmed -> the initial-opening wait windows no longer
        # EN: apply (cf. initial opening phase above) -- leaving them around
        # EN: could wrongly block a genuinely new refresh much later in the
        # EN: fight.
        state.hooks.pop("_vortex_open_pending_since", None)
        state.hooks.pop("_strikes_open_pending_since", None)

    return None


def meiko_post_tick(state: CombatState, pressed: str) -> None:
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

# BUG TROUVÉ EN JEU (rapporté par l'utilisateur, 2026-07-19) : "hammer storm
# sans proc" -- analyse de debug.log (session 16:52-17:15) : sur 119 presses
# hammer_storm_proc réelles, 11 sont parties avec hammer_storm_empowered déjà
# lu absent depuis 33 à 1120 ticks (~0.5s à ~18.7s à 60Hz). Root cause :
# XXX_ready n'expirait JAMAIS une fois armé -- seul un vrai cast confirmé
# (tariq_post_tick) l'effaçait. Si resource_above:fury:55 (ou le seuil réduit sous
# Focused Wrath) mettait du temps à être atteint après l'armement, le proc
# restait consommable indéfiniment, bien après que son glow ait disparu à
# l'écran. TARIQ_PROC_EXPIRE_S = 30.0 : durée RÉELLE du proc confirmée par
# l'utilisateur (2026-07-19) -- peut cumuler jusqu'à 2 charges, mais le badge
# ne l'affiche jamais (cf. simplification du 2026-07-12 ci-dessous, "le jeu
# n'affiche JAMAIS '2' sur ce badge") -- MÊME principe ici : pas de tentative
# de compter les charges, juste ne plus laisser ready traîner indéfiniment
# au-delà de la durée réelle d'UNE charge. Appliquée aux DEUX procs de
# TARIQ_PROC_BUFFS (même boucle générique) -- confirmée par l'utilisateur
# UNIQUEMENT pour hammer_storm_proc, étendue à skull_crusher_proc par
# analogie (même mécanisme de proc, aucune preuve d'une durée différente,
# mais PAS indépendamment confirmée -- à surveiller comme les autres seuils
# de ce fichier avant validation en jeu).
# EN: BUG FOUND IN-GAME (reported by the user, 2026-07-19): "hammer storm
# EN: without a proc" -- analysis of debug.log (session 16:52-17:15): out of
# EN: 119 real hammer_storm_proc presses, 11 fired with hammer_storm_empowered
# EN: already read absent for 33 to 1120 ticks (~0.5s to ~18.7s at 60Hz). Root
# EN: cause: XXX_ready NEVER expired once armed -- only a real confirmed cast
# EN: (tariq_post_tick) cleared it. If resource_above:fury:55 (or the reduced threshold
# EN: under Focused Wrath) took a while to be reached after arming, the proc
# EN: stayed consumable indefinitely, long after its glow had disappeared on
# EN: screen. TARIQ_PROC_EXPIRE_S = 30.0: the proc's REAL duration confirmed
# EN: by the user (2026-07-19) -- can stack up to 2 charges, but the badge
# EN: never shows it (cf. the 2026-07-12 simplification below, "the game
# EN: NEVER shows '2' on that badge") -- SAME principle here: no attempt to
# EN: count charges, just stop letting ready linger indefinitely past a
# EN: single charge's real duration. Applied to BOTH procs in
# EN: TARIQ_PROC_BUFFS (same generic loop) -- confirmed by the user ONLY for
# EN: hammer_storm_proc, extended to skull_crusher_proc by analogy (same proc
# EN: mechanism, no evidence of a different duration, but NOT independently
# EN: confirmed -- to watch like this file's other thresholds before in-game
# EN: validation).
TARIQ_PROC_EXPIRE_S = 30.0

# Hammer Storm est un cast (canalisation) qui ne doit JAMAIS être coupé par
# une autre ability -- y compris les off_gcd (culling_strike, focused_wrath,
# thunder_call...), qui bypassent normalement le gate de GCD classique.
# state.hooks["hammer_storm_casting"] est posé par make_tariq_state_update_fn
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
# EN: gate. state.hooks["hammer_storm_casting"] is set by
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
# focused_wrath_empowered est actif, la condition YAML resource_above:fury:55/23
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
# EN: focused_wrath_empowered is active, the YAML condition resource_above:fury:55/23
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
SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED = 12.0   # config/tariq.yaml : resource_above:fury:23 (coût plein) / 2 -- pas de signal de détection dédié pour confirmer un cast Skull Crusher, donc pas de preuve pour ajuster ce seuil comme les deux ci-dessus (cf. skull_crusher_proc, non touché) · EN: config/tariq.yaml: resource_above:fury:23 (full cost) / 2 -- no dedicated detection signal to confirm a Skull Crusher cast, so no proof to tune this threshold like the two above (cf. skull_crusher_proc, untouched)

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
# réellement commencé à canaliser" -- state.hooks["hammer_storm_casting"]
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
# EN: Storm actually started channeling" -- state.hooks["hammer_storm_casting"]
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
# aujourd'hui). C'est un objectif temporaire (state.hooks["tariq_objective"])
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
# EN: today). It's a temporary objective (state.hooks["tariq_objective"]) that
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


def _update_tariq_planner(state: CombatState, now: float) -> None:
    objective = state.planner.get("tariq_objective", "normal")

    fw_ready_in = state.cooldown_until.get("focused_wrath", 0.0) - now
    tc_ready_in = state.cooldown_until.get("thunder_call", 0.0) - now
    fw_ready = fw_ready_in <= 0
    tc_ready = tc_ready_in <= 0

    if objective == "prepare_burst":
        since = state.planner.get("tariq_objective_since", now)
        timed_out = now - since > BURST_PREP_WINDOW_S + BURST_TIMEOUT_MARGIN_S
        if (fw_ready and tc_ready) or timed_out:
            objective = "normal"
    elif (fw_ready and 0 < tc_ready_in <= BURST_PREP_WINDOW_S) or (tc_ready and 0 < fw_ready_in <= BURST_PREP_WINDOW_S):
        objective = "prepare_burst"
        state.planner["tariq_objective_since"] = now

    state.planner["tariq_objective"] = objective
    state.planner["burst_hold"] = objective == "prepare_burst"
    state.planner["fury_conserve"] = objective == "prepare_burst"


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


def _register_press_confirmation(state: CombatState, pressed: str, now: float) -> None:
    if pressed not in GCD_VEIL_ABILITIES and pressed not in OFF_GCD_VEIL_ABILITY_BUFF_KEYS:
        return   # ability sans voile suivi (ex: culling_strike/thunder_call/focused_wrath sont dans OFF_GCD_..., donc bien couverts -- seules face_breaker/wild_swing/etc. via GCD_VEIL_ABILITIES et les 3 off_gcd sont trackées) · EN: ability with no tracked veil (culling_strike/thunder_call/focused_wrath are in OFF_GCD_..., so well covered -- only face_breaker/wild_swing/etc. via GCD_VEIL_ABILITIES and the 3 off_gcd are tracked)
    pending = state.hooks.setdefault("_press_confirmations_pending", [])
    pending.append({
        "ability": pressed,
        "requested_at_monotonic": now,
        "requested_at_wall": time.time(),
    })


def _resolve_press_confirmations(state: CombatState, now: float) -> None:
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
    last_seen = state.hooks.get("_press_confirm_last_tick_at")
    state.hooks["_press_confirm_last_tick_at"] = now
    if last_seen is not None and now - last_seen > 0.5:
        state.hooks["_press_confirmations_pending"] = []
        return

    pending = state.hooks.get("_press_confirmations_pending")
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
    state.hooks["_press_confirmations_pending"] = still_pending


def tariq_pre_tick(state: CombatState) -> list[Intent]:
    """
    Câblé comme pre_tick_hook (cf. RotationEngine, engine.py::Intent) -- doit
    retourner une liste d'Intent, pas un nom nu. Toute la logique (planner,
    confirmations de presse, verrou Hammer Storm, front montant des procs,
    discount Focused Wrath...) reste intacte dans _tariq_pre_tick_impl
    ci-dessous, appelée UNE SEULE fois (ses effets de bord -- planner,
    resolve_press_confirmations, hard_lock_until -- ne doivent jamais
    tourner deux fois par tick). Poids fixe de 90 (cf. Intent, engine.py) :
    reproduit fidèlement l'ancien comportement de "GOTO absolu" -- migration
    mécanique, zéro changement de comportement.

    EN: Wired as pre_tick_hook (cf. RotationEngine, engine.py::Intent) -- must
    EN: return a list of Intent, not a bare name. All the logic (planner,
    EN: press confirmations, Hammer Storm lock, proc rising edge, Focused
    EN: Wrath discount...) stays intact in _tariq_pre_tick_impl below, called
    EN: EXACTLY ONCE (its side effects -- planner, resolve_press_confirmations,
    EN: hard_lock_until -- must never run twice per tick). Fixed weight of 90
    EN: (cf. Intent, engine.py): faithfully reproduces the former "absolute
    EN: GOTO" behavior -- mechanical migration, zero behavior change.
    """
    ability = _tariq_pre_tick_impl(state)
    return [Intent(ability=ability, weight=90, source="tariq_pre_tick")] if ability else []


def _tariq_pre_tick_impl(state: CombatState) -> Optional[str]:
    now = time.monotonic()
    _update_tariq_planner(state, now)
    _resolve_press_confirmations(state, now)
    if state.detection.get("hammer_storm_casting", False):
        state.hard_lock_until = max(state.hard_lock_until, now + HAMMER_STORM_LOCK_REFRESH_S)
        if state.hooks.get("_hammer_storm_proc_pending", False):
            # Canalisation confirmée pendant la fenêtre d'attente -> le press
            # a réellement abouti, le proc est VRAIMENT consommé maintenant.
            # EN: Channel confirmed during the wait window -> the press really
            # EN: succeeded, the proc is TRULY consumed now.
            state.hooks["hammer_storm_proc_ready"] = False
            state.hooks["_hammer_storm_proc_pending"] = False

    # PLUS DE CLEAR SUR TIMEOUT ICI (retiré le 2026-07-20, BUG TROUVÉ EN JEU,
    # rapporté par l'utilisateur -- corrélé précisément avec debug.log) :
    # l'ancien code effaçait _hammer_storm_proc_pending dès que
    # HAMMER_STORM_PROC_CONFIRM_WINDOW_S (0.5s) s'écoulait SANS canalisation
    # détectée -- en supposant "le jeu a refusé le cast". Mais si la
    # canalisation démarre VRAIMENT, juste détectée un peu APRÈS ces 0.5s
    # (ex. pendant un des stalls multi-secondes du worker de détection déjà
    # documentés, cf. capture_watchdog.log/_run_detection_worker), la
    # confirmation tardive arrivait alors que _pending était déjà remis à
    # False -- la condition `if state.hooks.get("_hammer_storm_proc_pending",
    # False):` du bloc juste au-dessus ne s'exécutait donc JAMAIS pour ce
    # press, et `ready` restait bloqué à True INDÉFINIMENT (jusqu'à
    # TARIQ_PROC_EXPIRE_S, 30s). Effet observé et corrélé (2026-07-20) :
    # hammer_storm_proc repart plusieurs fois sur le MÊME proc déjà dépensé,
    # à chaque fois SANS buff empowered réellement actif -- exactement le
    # symptôme rapporté.
    #
    # On NE PEUT PAS distinguer, au moment où la fenêtre "expire", "le jeu a
    # vraiment refusé" (cf. test_hammer_storm_proc_press_without_real_cast_stays_ready_for_retry,
    # 2026-07-13 -- ready DOIT rester True pour retenter) de "la canalisation
    # a juste été détectée en retard" (ce nouveau bug -- ready DOIT être
    # effacé dès que la confirmation arrive, même tard). Les deux cas sont
    # indiscernables à cet instant précis -- la seule réponse correcte est de
    # ne JAMAIS cesser d'écouter une confirmation tardive : le bloc
    # `if state.detection.get("hammer_storm_casting", False):` plus haut
    # reste actif tant que _pending n'a pas été levé par une VRAIE
    # confirmation, quelle que soit la durée d'attente. Le filet de sécurité
    # contre un press réellement raté pour toujours reste TARIQ_PROC_EXPIRE_S
    # (30s) -- s'il ne se passe RIEN pendant 30s, ready expire de toute façon
    # (cf. plus bas), donc pas de risque de rester bloqué en attente sans fin.
    # EN: NO MORE TIMEOUT CLEAR HERE (removed 2026-07-20, BUG FOUND IN-GAME,
    # EN: reported by the user -- precisely correlated with debug.log): the
    # EN: old code cleared _hammer_storm_proc_pending as soon as
    # EN: HAMMER_STORM_PROC_CONFIRM_WINDOW_S (0.5s) elapsed WITHOUT a detected
    # EN: channel -- assuming "the game refused the cast". But if the channel
    # EN: REALLY starts, just detected slightly AFTER those 0.5s (e.g. during
    # EN: one of the detection worker's already-documented multi-second
    # EN: stalls, cf. capture_watchdog.log/_run_detection_worker), the late
    # EN: confirmation then arrived while _pending had already been reset to
    # EN: False -- the `if state.hooks.get("_hammer_storm_proc_pending",
    # EN: False):` condition in the block just above therefore NEVER ran for
    # EN: that press, and `ready` stayed stuck True INDEFINITELY (until
    # EN: TARIQ_PROC_EXPIRE_S, 30s). Observed and correlated effect
    # EN: (2026-07-20): hammer_storm_proc fires several times on the SAME
    # EN: already-spent proc, each time WITHOUT a really active empowered
    # EN: buff -- exactly the reported symptom.
    # EN:
    # EN: We CANNOT tell apart, at the moment the window "expires", "the game
    # EN: really refused" (cf. test_hammer_storm_proc_press_without_real_cast_stays_ready_for_retry,
    # EN: 2026-07-13 -- ready MUST stay True to retry) from "the channel was
    # EN: just detected late" (this new bug -- ready MUST be cleared as soon
    # EN: as the confirmation arrives, however late). The two cases are
    # EN: indistinguishable at that exact moment -- the only correct answer is
    # EN: to NEVER stop listening for a late confirmation: the
    # EN: `if state.detection.get("hammer_storm_casting", False):` block above
    # EN: stays active as long as _pending hasn't been lifted by a REAL
    # EN: confirmation, no matter how long the wait. The safety net against a
    # EN: press that truly failed forever remains TARIQ_PROC_EXPIRE_S (30s) --
    # EN: if NOTHING happens for 30s, ready expires anyway (cf. below), so
    # EN: there's no risk of staying stuck waiting forever.

    for proc_name, buff_name in TARIQ_PROC_BUFFS.items():
        prev_key = f"_{buff_name}_prev"
        absent_ticks_key = f"_{buff_name}_absent_ticks"
        ready_key = f"{proc_name}_ready"
        armed_at_key = f"_{ready_key}_armed_at"

        was_active = state.hooks.get(prev_key, False)
        is_active = state.buffs.get(buff_name, False)
        absent_ticks = state.hooks.get(absent_ticks_key, MIN_ABSENT_TICKS)

        if is_active:
            if not was_active and absent_ticks >= MIN_ABSENT_TICKS:
                state.hooks[ready_key] = True
                state.hooks[armed_at_key] = now
            absent_ticks = 0
        else:
            absent_ticks += 1

        # EXPIRATION (2026-07-19, cf. TARIQ_PROC_EXPIRE_S plus haut) -- tourne
        # à CHAQUE tick, même sous hard_lock/détection périmée (même raison
        # que le suivi absent_ticks juste au-dessus : un proc réellement
        # expiré doit arrêter d'être consommable, peu importe ce que fait le
        # reste de la fonction ce tick-ci).
        # EN: EXPIRATION (2026-07-19, cf. TARIQ_PROC_EXPIRE_S above) -- runs
        # EN: EVERY tick, even under hard_lock/stale detection (same reason as
        # EN: the absent_ticks tracking just above: a truly expired proc must
        # EN: stop being consumable, regardless of what else this tick does).
        if state.hooks.get(ready_key, False):
            armed_at = state.hooks.get(armed_at_key)
            if armed_at is not None and now - armed_at > TARIQ_PROC_EXPIRE_S:
                state.hooks[ready_key] = False
                state.hooks.pop(armed_at_key, None)
                if proc_name == "hammer_storm_proc":
                    # Hygiène (2026-07-20) : depuis le retrait du clear sur
                    # timeout ci-dessus, _pending peut désormais rester vrai
                    # en attendant une confirmation qui ne viendra jamais (cast
                    # réellement refusé, jamais de canalisation) -- si ready
                    # expire quand même, ça veut dire qu'aucune confirmation
                    # n'est jamais arrivée en 30s : plus la peine d'écouter,
                    # on lève aussi le marqueur d'attente pour ne pas le
                    # laisser traîner indéfiniment.
                    # EN: Hygiene (2026-07-20): since the timeout clear above
                    # EN: was removed, _pending can now stay true while waiting
                    # EN: for a confirmation that will never come (cast really
                    # EN: refused, no channel ever). If ready expires anyway,
                    # EN: it means no confirmation ever arrived in 30s: no
                    # EN: point listening any further, also lift the pending
                    # EN: marker rather than leaving it dangling forever.
                    state.hooks["_hammer_storm_proc_pending"] = False
                    state.hooks.pop("_hammer_storm_proc_pending_since", None)

        state.hooks[absent_ticks_key] = absent_ticks
        state.hooks[prev_key] = is_active

    # FRAÎCHEUR (audit architecture, 2026-07-17) : jusqu'ici, cette décision
    # (comme le reste de _tariq_pre_tick_impl) ne consultait JAMAIS
    # state.detection_meta -- seul Vigour le faisait. Placé ICI (juste avant
    # la décision de presse RÉELLE) et PAS en tête de fonction : le planner
    # (_update_tariq_planner), les confirmations de presse
    # (_resolve_press_confirmations), le hard_lock Hammer Storm et le suivi
    # front-montant des procs (boucle TARIQ_PROC_BUFFS juste au-dessus)
    # doivent continuer à tourner à CHAQUE tick quoi qu'il arrive (absent_ticks
    # notamment -- le geler pendant une période périmée fausserait la
    # détection de front montant une fois la détection revenue). Seule la
    # décision finale (presser un proc sur un fury_pct/ready potentiellement
    # gelés) est bloquée par une détection périmée -- tariq_bars alimente
    # fury_pct, tariq_yolo alimente hammer_storm_proc_ready/
    # skull_crusher_proc_ready (via les buffs, cf. la boucle ci-dessus) : les
    # deux doivent être fraîches.
    # EN: FRESHNESS (architecture audit, 2026-07-17): until now, this decision
    # EN: (like the rest of _tariq_pre_tick_impl) never consulted
    # EN: state.detection_meta at all -- only Vigour did. Placed HERE (right
    # EN: before the REAL press decision) and NOT at the top of the function:
    # EN: the planner (_update_tariq_planner), press confirmations
    # EN: (_resolve_press_confirmations), the Hammer Storm hard_lock, and the
    # EN: proc rising-edge tracking (TARIQ_PROC_BUFFS loop just above) must
    # EN: keep running EVERY tick no matter what (absent_ticks especially --
    # EN: freezing it during a stale period would throw off rising-edge
    # EN: detection once detection comes back). Only the FINAL decision
    # EN: (pressing a proc on a potentially frozen fury_pct/ready) is blocked
    # EN: by stale detection -- tariq_bars feeds fury_pct, tariq_yolo feeds
    # EN: hammer_storm_proc_ready/skull_crusher_proc_ready (via the buffs, cf.
    # EN: the loop above): both need to be fresh.
    tariq_yolo_meta = state.detection_meta.get("tariq_yolo")
    tariq_bars_meta = state.detection_meta.get("tariq_bars")
    tariq_detection_expired = (
        tariq_yolo_meta is None
        or TARIQ_YOLO_FRESHNESS_POLICY.classify(tariq_yolo_meta.age_ms(now=now)) == Freshness.EXPIRED
        or tariq_bars_meta is None
        or TARIQ_BARS_FRESHNESS_POLICY.classify(tariq_bars_meta.age_ms(now=now)) == Freshness.EXPIRED
    )

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
    if not tariq_detection_expired and state.buffs.get("focused_wrath_empowered", False):
        fury_pct = state.resources.get("fury", 0.0) * 100
        if (
            now >= state.gcd_until
            and not state.is_on_cooldown("hammer_storm_proc", now)
            and state.hooks.get("hammer_storm_proc_ready", False)
            and fury_pct >= HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
        ):
            return "hammer_storm_proc"
        if (
            now >= state.gcd_until
            and not state.is_on_cooldown("skull_crusher_proc", now)
            and state.hooks.get("skull_crusher_proc_ready", False)
            and fury_pct >= SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED
        ):
            return "skull_crusher_proc"

    return None


def tariq_post_tick(state: CombatState, pressed: str) -> None:
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
        state.hooks["_hammer_storm_proc_pending"] = True
        state.hooks["_hammer_storm_proc_pending_since"] = time.monotonic()
    elif pressed in TARIQ_PROC_BUFFS:
        state.hooks[f"{pressed}_ready"] = False


# =============================================================================
# VIGOUR (Healer) -- Phase 1 de l'industrialisation Vigour (2026-07-15, cf.
# discussion architecture). Matrice de poids simple, un seul soin proposé par
# tick (comme meiko_pre_tick/tariq_pre_tick, un seul cast possible par GCD de
# toute façon) : dégâts de groupe > allié critique > cleanse > entretien
# (HoT). AoE heal ne renseigne jamais Intent.target -- soin de zone, pas de
# ciblage à faire (cf. RotationEngine.tick(), le press de ciblage ne se
# déclenche que si target est renseigné).
#
# PREMIER VRAI BRANCHEMENT DE freshness.py DANS UNE DÉCISION (cf. l'audit :
# jusqu'ici mesuré/classifiable mais jamais consulté, cf. engine.py::
# CombatState.detection_meta). Une lecture EXPIRED -> on ne propose RIEN
# plutôt que d'agir sur une valeur qu'on ne peut plus garantir fraîche ; le
# scan de priorité YAML (poids 50) reste libre de prendre le relai si
# quelqu'un lui en donne l'occasion (aujourd'hui : rien, cf. config/vigour.yaml,
# Phase 1 -- toutes les abilities sont skip_detection, décidées ici).
# EN: VIGOUR (Healer) -- Phase 1 of the Vigour industrialization (2026-07-15,
# EN: cf. architecture discussion). Simple weight matrix, one heal proposed
# EN: per tick (like meiko_pre_tick/tariq_pre_tick, only one cast possible per
# EN: GCD anyway): group damage > critical ally > cleanse > upkeep (HoT). AoE
# EN: heal never sets Intent.target -- area heal, no targeting needed (cf.
# EN: RotationEngine.tick(), the target-select press only fires if target is
# EN: set).
# EN:
# EN: FIRST REAL WIRING OF freshness.py INTO A DECISION (cf. the audit: until
# EN: now only measured/classifiable but never consulted, cf. engine.py::
# EN: CombatState.detection_meta). An EXPIRED reading -> propose NOTHING
# EN: rather than act on a value we can no longer guarantee is fresh; the YAML
# EN: priority scan (weight 50) stays free to take over if anything gives it
# EN: the chance to (today: nothing, cf. config/vigour.yaml, Phase 1 -- every
# EN: ability is skip_detection, decided here).
VIGOUR_CRITICAL_HP_THRESHOLD = 0.30
VIGOUR_HURT_HP_THRESHOLD = 0.70
VIGOUR_GROUP_DAMAGE_MIN_HURT_ALLIES = 3
VIGOUR_FULL_HP_THRESHOLD = 0.97   # "personne n'a pris le moindre degat" -- pas 1.0 strict : tolere le bruit de mesure du bar_reader (+/-3pt deja documente) sans faire flapper build<->dps pour rien

# ROTATION RÉELLE (2026-07-16, build Legendary Cloak -- cf. method.gg,
# section talents + playstyle-and-rotation post-4e-ligne, complétée par les
# cooldowns/coûts fournis directement par l'utilisateur). RÉVISÉE le
# 2026-07-16 (2e passe, 1er retour de test en jeu) : la 1re version faisait
# de l'entretien de Rune Of Renewal un timer par allié (~25s, "100% uptime"
# pris au pied de la lettre) et gardait Dawnbreaker Orb/Luminous Barrier sur
# un seuil "quelqu'un profiterait d'un complément" (<90%, quasi toujours
# vrai en combat) -- deux sources de dépense d'orbe DÉCONNECTÉES du vrai
# besoin de soin, exactement le symptôme signalé par l'utilisateur ("on ne
# peut pas se permettre de spam des runes pour rien avec renewal ni avec les
# boucliers"). Remplacée par une règle strictement pilotée par la vie réelle
# du groupe (build seulement si personne n'a besoin de rien, dépense
# seulement si quelqu'un en a réellement besoin) :
#
# Ordre de priorité, du plus urgent au moins urgent :
#   1. Critique (<30%)          -> Greater Heal (soin direct)
#   2. Urgence de groupe (3+)   -> Circle of Light (AoE, cf. HYPOTHÈSE
#      CIBLAGE ci-dessous)
#   3. Cleanse                  -> Remove Magic
#   4. Quelqu'un à 70% ou moins -> soigne le plus bas en vie : Dawnbreaker
#      Orb (2 orbes, le plus gros complément) si affordable et pas en
#      cooldown, sinon Rune Of Renewal (1 orbe), sinon Dawnflare (aucun orbe
#      dispo -- on en génère un avant de pouvoir soigner)
#   5. Personne à 70% ou moins, mais pas TOUT LE MONDE à (quasi) 100% ->
#      Soulbrand (dégât pur -- une simple égratignure ne justifie pas de
#      dépenser un orbe de soin)
#   6. Tout le monde à (quasi) 100% -> Dawnflare (build -- rien à soigner,
#      autant générer des orbes et taper au passage)
#
# Luminous Barrier N'EST PLUS auto-déclenché : sous ce nouveau modèle, son
# seul déclencheur défendable (quelqu'un <= 70%) est déjà entièrement pris
# en charge par (4), en mieux -- Dawnbreaker Orb soigne davantage et Rune Of
# Renewal coûte le même orbe tout en ajoutant l'écho +35%. Garder une
# branche Luminous Barrier au même seuil serait du code mort (jamais
# atteint, cf. CLAUDE.md -- pas de demi-implémentation). Reste déclarée dans
# config/vigour.yaml et câblée côté touche (profiles/Default/config.json)
# pour un usage manuel, ou pour une future passe si un déclencheur PRÉDICTIF
# (dégâts entrants, pas seulement la vie actuelle) devient possible.
#
# Rune Of Renewal n'a plus de suivi temporel : VIGOUR_RUNE_OF_RENEWAL_
# DURATION_S/_REFRESH_WINDOW_S, _propose_rune_of_renewal et le bookkeeping
# de vigour_post_tick ont été retirés plutôt que réajustés -- c'était
# précisément ce timer (rafraîchir même sans besoin réel) qui causait le
# spam signalé. vigour_post_tick est redevenu un no-op (cf. plus bas).
#
# Dawnbreaker Orb vérifie désormais son propre cooldown (state.is_on_
# cooldown) AVANT d'être proposé : contrairement au scan de priorité YAML
# (_pick_ability), un hook_intent n'est PAS filtré par cooldown_until côté
# moteur (cf. RotationEngine.tick()) -- lui presser cette touche pendant son
# cooldown de 9.97s brûlerait un GCD entier pour rien pile au moment où
# quelqu'un a besoin d'un soin. Comme (4) s'appuie beaucoup plus souvent sur
# Dawnbreaker Orb qu'avant (1er choix pour QUICONQUE est <= 70%, plus une
# niche isolée), cette vérification devient nécessaire ici -- Circle of
# Light/Remove Magic/Luminous Barrier ont la même lacune (jamais vérifiée),
# préexistante et non traitée ici (hors périmètre de ce correctif, cf.
# discussion 2026-07-16).
#
# HYPOTHÈSE CIBLAGE Circle of Light (PAS VÉRIFIÉE EN JEU) : ce sort cible
# normalement le SOL sous les pieds d'un joueur, pas une unité -- ce moteur
# ne pilote que le clavier (pydirectinput), aucun contrôle du curseur/de la
# caméra pour un ciblage au sol. On utilise le MÊME mécanisme de ciblage
# d'allié que les autres sorts (Intent.target -> RotationEngine.tick() presse
# la touche de ciblage avant le sort, cf. engine.py) en visant le tank comme
# point d'ancrage (le groupe se regroupe généralement sur lui). Ça ne
# fonctionnera QUE si Fellowship a une option "cast au sol sous la cible"
# (fréquente dans ce genre de jeu, mais PAS confirmée ici) -- à valider en
# jeu avant de faire confiance à ce sort spécifiquement.
# EN: REAL ROTATION (2026-07-16, Legendary Cloak build -- cf. method.gg,
# EN: talents + post-4th-row playstyle-and-rotation section, completed with
# EN: cooldowns/costs given directly by the user). REVISED on 2026-07-16
# EN: (2nd pass, 1st in-game test feedback): the 1st version turned Rune Of
# EN: Renewal upkeep into a per-ally timer (~25s, "100% uptime" taken
# EN: literally) and kept Dawnbreaker Orb/Luminous Barrier on a "someone
# EN: would benefit from a top-up" threshold (<90%, almost always true in
# EN: combat) -- two orb-spend sources DISCONNECTED from real healing need,
# EN: exactly the symptom the user reported in-game ("we can't afford to
# EN: spam runes for nothing with Renewal nor with the shields"). Replaced
# EN: by a rule strictly driven by the group's real HP (build only if
# EN: nobody needs anything, spend only if someone genuinely does):
# EN:
# EN: Priority order, most to least urgent:
# EN:   1. Critical (<30%)          -> Greater Heal (direct heal)
# EN:   2. Group emergency (3+)     -> Circle of Light (AoE, cf. TARGETING
# EN:      HYPOTHESIS below)
# EN:   3. Cleanse                  -> Remove Magic
# EN:   4. Someone at 70% or below  -> heals the lowest-HP one: Dawnbreaker
# EN:      Orb (2 orbs, the bigger top-up) if affordable and off cooldown,
# EN:      else Rune Of Renewal (1 orb), else Dawnflare (no orb available --
# EN:      generate one before we can heal)
# EN:   5. Nobody at 70% or below, but NOT EVERYONE at (near) 100% ->
# EN:      Soulbrand (pure damage -- a minor scratch doesn't justify
# EN:      spending a healing orb)
# EN:   6. Everyone at (near) 100%  -> Dawnflare (build -- nothing to heal,
# EN:      might as well generate orbs and hit something along the way)
# EN:
# EN: Luminous Barrier is NO LONGER auto-triggered: under this new model,
# EN: its only defensible trigger (someone <= 70%) is already fully covered
# EN: by (4), and better -- Dawnbreaker Orb heals for more and Rune Of
# EN: Renewal costs the same orb while also adding the +35% echo. Keeping a
# EN: Luminous Barrier branch at the same threshold would be dead code
# EN: (never reached, cf. CLAUDE.md -- no half-finished implementations).
# EN: It stays declared in config/vigour.yaml and wired to a key
# EN: (profiles/Default/config.json) for manual use, or for a future pass if
# EN: a PREDICTIVE trigger (incoming damage, not just current HP) becomes
# EN: possible.
# EN:
# EN: Rune Of Renewal no longer has timer tracking: VIGOUR_RUNE_OF_RENEWAL_
# EN: DURATION_S/_REFRESH_WINDOW_S, _propose_rune_of_renewal and vigour_post_
# EN: tick's bookkeeping were removed rather than re-tuned -- that exact
# EN: timer (refreshing even without real need) was causing the reported
# EN: spam. vigour_post_tick is back to being a no-op (cf. below).
# EN:
# EN: Dawnbreaker Orb now checks its own cooldown (state.is_on_cooldown)
# EN: BEFORE being proposed: unlike the YAML priority scan (_pick_ability), a
# EN: hook_intent is NOT filtered by cooldown_until on the engine side (cf.
# EN: RotationEngine.tick()) -- pressing this key during its 9.97s cooldown
# EN: would burn an entire GCD for nothing right when someone needs healing.
# EN: Since (4) now relies on Dawnbreaker Orb far more often than before
# EN: (1st choice for ANYONE <= 70%, not an isolated niche anymore), this
# EN: check becomes necessary here -- Circle of Light/Remove Magic/Luminous
# EN: Barrier share the same gap (never checked), pre-existing and not
# EN: addressed here (out of scope for this fix, cf. 2026-07-16 discussion).
# EN:
# EN: Circle of Light TARGETING HYPOTHESIS (NOT VERIFIED IN-GAME): this spell
# EN: normally targets the GROUND at a player's feet, not a unit -- this
# EN: engine only drives the keyboard (pydirectinput), no cursor/camera
# EN: control for ground-targeting. Uses the SAME ally-targeting mechanism as
# EN: other spells (Intent.target -> RotationEngine.tick() presses the
# EN: target-select key before the spell, cf. engine.py), aiming at the tank
# EN: as an anchor point (the group usually stacks on him). This will ONLY
# EN: work if Fellowship has a "cast on the ground at your target" option
# EN: (common in this genre, but NOT confirmed here) -- to validate in-game
# EN: before trusting this spell specifically.
# EN:
VIGOUR_TANK_SLOT = 1
VIGOUR_RUNE_OF_RENEWAL_ORB_COST = 1
VIGOUR_DAWNBREAKER_ORB_COST = 2
VIGOUR_SOULBRAND_ORB_COST = 1


def vigour_pre_tick(state: CombatState) -> list[Intent]:
    """Câblé comme pre_tick_hook (cf. RotationEngine, engine.py::Intent) --
    même contrat que meiko_pre_tick/tariq_pre_tick : retourne une liste
    d'Intent, poids 90, ou [] si rien à proposer (le scan de priorité YAML
    prend alors le relai)."""
    now = time.monotonic()
    if now < state.gcd_until:
        return []   # même gate GCD explicite que meiko_pre_tick -- cf. CLAUDE.md bug #1 (spam avant la fin du GCD)

    meta = state.detection_meta.get("vigour_party_bars")
    if meta is None or VIGOUR_PARTY_FRESHNESS_POLICY.classify(meta.age_ms(now=now)) == Freshness.EXPIRED:
        return []   # donnée trop vieille ou jamais lue -- ne rien affirmer sur la vie du groupe

    party = state.party
    alive = {i: p for i, p in party.items() if p.get("present")}
    orbs = state.resources.get("orbs", 0)

    # 1. Critique (<30%) -- Greater Heal
    critical = {
        i: p for i, p in alive.items()
        if p.get("hp_pct") is not None and p["hp_pct"] < VIGOUR_CRITICAL_HP_THRESHOLD
    }
    if critical:
        worst = min(critical, key=lambda i: critical[i]["hp_pct"])
        return [Intent(ability="greater_heal", target=f"player_{worst}", weight=90, source="vigour_pre_tick")]

    # 2. Urgence de groupe (3+ sous 70%) -- Circle of Light, ciblé sur le tank
    group_hurt = sum(
        1 for p in alive.values()
        if p.get("hp_pct") is not None and p["hp_pct"] < VIGOUR_HURT_HP_THRESHOLD
    )
    if group_hurt >= VIGOUR_GROUP_DAMAGE_MIN_HURT_ALLIES and VIGOUR_TANK_SLOT in alive:
        return [Intent(ability="circle_of_light", target=f"player_{VIGOUR_TANK_SLOT}", weight=90, source="vigour_pre_tick")]

    # 3. Cleanse -- Remove Magic (renommé depuis "Dispell", cf. config/vigour.yaml)
    cleanse_target = next((i for i, p in alive.items() if p.get("cleanse_needed")), None)
    if cleanse_target is not None:
        return [Intent(ability="remove_magic", target=f"player_{cleanse_target}", weight=90, source="vigour_pre_tick")]

    # 4. Quelqu'un à 70% ou moins -- soigne le plus bas en vie. Dawnbreaker
    # Orb (2 orbes, complément le plus gros) en 1er choix si affordable ET
    # pas en cooldown (cf. commentaire de tête -- hook_intent non filtré par
    # cooldown_until côté moteur, contrairement au scan YAML), sinon Rune Of
    # Renewal (1 orbe), sinon Dawnflare pour d'abord générer l'orbe qui
    # manque. Remplace l'ancienne priorité "tank spécifiquement" + l'ancien
    # entretien par timer (2026-07-16, retirés -- cf. commentaire de tête).
    hurt = {
        i: p for i, p in alive.items()
        if p.get("hp_pct") is not None and p["hp_pct"] <= VIGOUR_HURT_HP_THRESHOLD
    }
    if hurt:
        worst = min(hurt, key=lambda i: hurt[i]["hp_pct"])
        if orbs >= VIGOUR_DAWNBREAKER_ORB_COST and not state.is_on_cooldown("dawnbreaker_orb", now):
            return [Intent(ability="dawnbreaker_orb", target=f"player_{worst}", weight=90, source="vigour_pre_tick")]
        if orbs >= VIGOUR_RUNE_OF_RENEWAL_ORB_COST:
            return [Intent(ability="rune_of_renewal", target=f"player_{worst}", weight=90, source="vigour_pre_tick")]
        return [Intent(ability="dawnflare", weight=90, source="vigour_pre_tick")]

    # 5/6. Personne à 70% ou moins -- construit (Dawnflare) si tout le monde
    # est déjà (quasi) au maximum, dépense du dégât pur (Soulbrand) sinon :
    # une simple égratignure ne justifie pas de dépenser un orbe de soin
    # (2026-07-16, retour de test en jeu de l'utilisateur -- "si toutes les
    # vies a 100% il faut build avec dawnflare ... sinon on dps"). Soulbrand
    # retombe sur Dawnflare si 0 orbe (rien à dépenser -- on en génère un).
    all_topped = bool(alive) and all(
        p.get("hp_pct") is not None and p["hp_pct"] >= VIGOUR_FULL_HP_THRESHOLD
        for p in alive.values()
    )
    if not all_topped and orbs >= VIGOUR_SOULBRAND_ORB_COST:
        return [Intent(ability="soulbrand", weight=90, source="vigour_pre_tick")]
    return [Intent(ability="dawnflare", weight=90, source="vigour_pre_tick")]


def vigour_post_tick(state: CombatState, pressed: str) -> None:
    """Plus de bookkeeping de timer à faire ici (2026-07-16, cf. commentaire
    de tête sur vigour_pre_tick -- l'entretien par timer de Rune Of Renewal a
    été retiré, pas réajusté, car c'était lui qui causait le spam signalé par
    l'utilisateur). Tout ce dont vigour_pre_tick a besoin (HP/cleanse/orbes)
    vient de la détection écran à chaque tick, pas d'une estimation. Gardé
    comme point d'extension, même convention que meiko_post_tick plus haut.

    EN: No more timer bookkeeping to do here (2026-07-16, cf. vigour_pre_
    EN: tick's header comment -- Rune Of Renewal's timer-based upkeep was
    EN: removed, not re-tuned, since it was the exact thing causing the spam
    EN: the user reported). Everything vigour_pre_tick needs (HP/cleanse/
    EN: orbs) comes from screen detection every tick, not an estimate. Kept
    EN: as an extension point, same convention as meiko_post_tick above.
    """
    return


# =============================================================================
# VIGOUR -- ROTATION EARLY GAME (avant le palier 4 des talents), 2026-07-16.
# Fournie par l'utilisateur comme une liste de priorités DISTINCTE de la
# rotation Legendary Cloak ci-dessus (post-4e-ligne, cf. vigour_pre_tick) --
# PAS un remplacement : les deux coexistent. C'est précisément pourquoi
# Radiant Blast était déjà déclarée dans config/vigour.yaml mais jamais
# câblée (cf. le commentaire d'origine : "listée dans le guide UNIQUEMENT
# pour la rotation PRÉ-4e-ligne") -- cette fonction la câble enfin.
#
# Sélection : STATIQUE, dans game_config.py::CHARACTERS["vigour"] --
# événement one-shot dans la vie d'un personnage (on ne redescend jamais
# sous le palier 4 une fois qu'on l'a atteint), donc un simple choix de
# fonction au démarrage suffit, pas besoin d'un flag ou d'une touche de
# bascule en jeu. game_config.py pointe actuellement sur
# vigour_pre_tick_early_game/vigour_post_tick_early_game (2026-07-16, "en
# jeu maintenant" implicite au moment où l'utilisateur a fourni cette liste)
# -- à repasser sur vigour_pre_tick/vigour_post_tick une fois le palier 4 des
# talents (build Legendary Cloak) atteint.
#
# Ordre de priorité fourni par l'utilisateur (2026-07-16) :
#   1. Uptime du buff (priorité absolue) -- Rune Of Renewal sur le TANK dès
#      qu'absent ou sur le point de s'estomper (alimente la génération
#      passive d'orbe)
#   2. Urgence tank -- Greater Heal si tank < 70%, étendu au reste du groupe
#      seulement si la situation dégénère
#   3. Génération de runes -- Dawnbreaker Orb puis Radiant Blast, dès que
#      hors cooldown
#   4. Dépense des runes (règle du cap) -- jauge >= 5 : Luminous Barrier
#      (allié qui encaisse des dégâts) ou Soulbrand (ennemi)
#   5. Soin de groupe (AoE) -- Circle of Light si plusieurs alliés perdent
#      de la vie simultanément
#   6. Filler -- Dawnflare en boucle
#
# 2 AJOUTS NON DEMANDÉS EXPLICITEMENT, documentés pour pouvoir être corrigés :
#   - Item 0 (Critique <30%, N'IMPORTE QUEL allié) placé AU-DESSUS de (1) :
#     l'utilisateur ne l'a pas redemandé ici, mais un allié en train de
#     mourir doit passer avant l'entretien d'un buff, cf. le même seuil déjà
#     utilisé par vigour_pre_tick (VIGOUR_CRITICAL_HP_THRESHOLD). Couvre
#     directement la clause "étendu au reste du groupe si la situation
#     dégénère" de (2) -- pas besoin d'un chiffre inventé séparé.
#   - Cleanse (Remove Magic) inséré juste après, par continuité avec
#     vigour_pre_tick -- absent de la liste fournie, mais un debuff qui
#     traîne ne se résout pas tout seul.
#
# Radiant Blast (2026-07-16, confirmé par l'utilisateur) : coûte du MANA,
# PAS d'orbe -- risque de vidage de mana identique à Greater Heal si spammé
# sans limite (cf. "erreurs communes" de l'utilisateur). Gatée par
# VIGOUR_EARLY_GAME_RADIANT_BLAST_MANA_FLOOR, un seuil DE NOTRE PROPRE CHOIX
# (pas fourni par l'utilisateur) -- à ajuster si trop/pas assez conservateur
# en jeu.
#
# Rune Of Renewal : timer réintroduit, mais scopé UNIQUEMENT AU TANK (une
# seule cible, un seul horodatage) -- PAS une resucée du timer "toutes les
# 25s sur les 4 alliés" retiré plus haut (cause du spam signalé) : ici c'est
# le guide LUI-MÊME qui demande explicitement cette uptime précise, sur un
# rôle précis, pour une raison économique précise (génération d'orbe passive)
# -- portée bien plus étroite que l'ancien design généralisé à tout le monde.
#
# Circle of Light : LIMITE CONNUE, PAS résolue ici (cf. note de l'utilisateur
# sur le cône d'effet) -- ce moteur n'a AUCUNE détection de position/distance
# entre alliés, seulement des %HP. Impossible de vérifier qu'un allié blessé
# est physiquement dans le petit cône avant de lancer le sort ; on continue
# de viser le tank comme point d'ancrage (même hypothèse de ciblage que
# vigour_pre_tick) sans garantie qu'il soit dans la zone. Gap connu et
# volontairement non traité ici, pas un bug à corriger par réflexe.
#
# EN: VIGOUR -- EARLY GAME ROTATION (before talent tier 4), 2026-07-16.
# EN: Provided by the user as a DISTINCT priority list from the Legendary
# EN: Cloak rotation above (post-4th-row, cf. vigour_pre_tick) -- NOT a
# EN: replacement: the two coexist. This is exactly why Radiant Blast was
# EN: already declared in config/vigour.yaml but never wired (cf. the
# EN: original comment: "listed in the guide ONLY for the PRE-4th-row
# EN: rotation") -- this function finally wires it.
# EN:
# EN: Selection: STATIC, in game_config.py::CHARACTERS["vigour"] -- a
# EN: one-shot event in a character's lifetime (you never drop back below
# EN: tier 4 once reached), so a simple function choice at startup is
# EN: enough, no need for a flag or an in-game toggle key. game_config.py
# EN: currently points at vigour_pre_tick_early_game/vigour_post_tick_
# EN: early_game (2026-07-16, "in-game right now" implicit at the moment the
# EN: user provided this list) -- switch back to vigour_pre_tick/vigour_
# EN: post_tick once talent tier 4 (Legendary Cloak build) is reached.
# EN:
# EN: Priority order as provided by the user (2026-07-16):
# EN:   1. Buff uptime (absolute priority) -- Rune Of Renewal on the TANK as
# EN:      soon as absent or about to fade (fuels passive orb generation)
# EN:   2. Tank emergency -- Greater Heal if tank < 70%, extended to the rest
# EN:      of the group only if the situation degenerates
# EN:   3. Rune generation -- Dawnbreaker Orb then Radiant Blast, as soon as
# EN:      off cooldown
# EN:   4. Rune spend (cap rule) -- gauge >= 5: Luminous Barrier (an ally
# EN:      taking damage) or Soulbrand (an enemy)
# EN:   5. Group heal (AoE) -- Circle of Light if multiple allies lose HP
# EN:      simultaneously
# EN:   6. Filler -- Dawnflare on loop
# EN:
# EN: 2 ADDITIONS NOT EXPLICITLY REQUESTED, documented so they can be
# EN: corrected:
# EN:   - Item 0 (Critical <30%, ANY ally) placed ABOVE (1): the user didn't
# EN:     re-ask for this here, but a dying ally must come before buff
# EN:     upkeep, cf. the same threshold vigour_pre_tick already uses
# EN:     (VIGOUR_CRITICAL_HP_THRESHOLD). Directly covers (2)'s "extended to
# EN:     the rest of the group if the situation degenerates" clause -- no
# EN:     need for a separately invented number.
# EN:   - Cleanse (Remove Magic) inserted right after, by continuity with
# EN:     vigour_pre_tick -- absent from the provided list, but a lingering
# EN:     debuff doesn't resolve itself.
# EN:
# EN: Radiant Blast (2026-07-16, confirmed by the user): costs MANA, NOT an
# EN: orb -- same mana-drain risk as Greater Heal if spammed without limit
# EN: (cf. the user's "common mistakes"). Gated by VIGOUR_EARLY_GAME_
# EN: RADIANT_BLAST_MANA_FLOOR, a threshold OF OUR OWN CHOOSING (not provided
# EN: by the user) -- adjust if too conservative or not conservative enough
# EN: in-game.
# EN:
# EN: Rune Of Renewal: timer reintroduced, but scoped ONLY TO THE TANK (a
# EN: single target, a single timestamp) -- NOT a rehash of the "every 25s
# EN: on all 4 allies" timer removed above (the cause of the reported spam):
# EN: here it's the GUIDE ITSELF explicitly asking for this specific uptime,
# EN: on a specific role, for a specific economic reason (passive orb
# EN: generation) -- much narrower scope than the old design generalized to
# EN: everyone.
# EN:
# EN: Circle of Light: KNOWN LIMITATION, NOT resolved here (cf. the user's
# EN: note on the effect's cone) -- this engine has NO position/distance
# EN: detection between allies, only %HP. Impossible to verify a hurt ally is
# EN: physically inside the small cone before casting; still aims at the
# EN: tank as an anchor point (same targeting hypothesis as vigour_pre_tick)
# EN: with no guarantee they're in the area. Known gap, deliberately not
# EN: addressed here, not a bug to fix by reflex.
VIGOUR_EARLY_GAME_TANK_HP_THRESHOLD = 0.70
VIGOUR_EARLY_GAME_RUNE_OF_RENEWAL_REFRESH_WINDOW_S = 25.0   # même marge proactive que l'ancien design (30s de durée réelle, cf. tooltip) -- pas la cause du spam, seule sa portée (tout le monde) l'était · EN: same proactive margin as the old design (30s real duration, cf. tooltip) -- not the cause of the spam, only its scope (everyone) was
VIGOUR_EARLY_GAME_RUNE_CAP_THRESHOLD = 5
VIGOUR_EARLY_GAME_RADIANT_BLAST_MANA_FLOOR = 0.30   # notre propre choix, pas fourni par l'utilisateur -- réserve du mana pour Greater Heal · EN: our own choice, not provided by the user -- reserves mana for Greater Heal


def vigour_pre_tick_early_game(state: CombatState) -> list[Intent]:
    """Câblé comme pre_tick_hook (cf. RotationEngine, engine.py::Intent) --
    même contrat que vigour_pre_tick : retourne une liste d'Intent, poids 90,
    ou [] si rien à proposer. Cf. commentaire de tête ci-dessus pour l'ordre
    de priorité complet et son origine."""
    now = time.monotonic()
    if now < state.gcd_until:
        return []   # même gate GCD explicite que vigour_pre_tick -- cf. CLAUDE.md bug #1

    meta = state.detection_meta.get("vigour_party_bars")
    if meta is None or VIGOUR_PARTY_FRESHNESS_POLICY.classify(meta.age_ms(now=now)) == Freshness.EXPIRED:
        return []   # donnée trop vieille ou jamais lue -- ne rien affirmer sur la vie du groupe

    party = state.party
    alive = {i: p for i, p in party.items() if p.get("present")}
    orbs = state.resources.get("orbs", 0)
    mana = state.resources.get("mana", 1.0)
    tank = alive.get(VIGOUR_TANK_SLOT)

    # 0. Critique (<30%, n'importe quel allié) -- AJOUT (pas dans la liste
    # fournie), cf. commentaire de tête. Couvre la clause "étendu si la
    # situation dégénère" de l'item 2 sans inventer un chiffre séparé.
    critical = {
        i: p for i, p in alive.items()
        if p.get("hp_pct") is not None and p["hp_pct"] < VIGOUR_CRITICAL_HP_THRESHOLD
    }
    if critical:
        worst = min(critical, key=lambda i: critical[i]["hp_pct"])
        return [Intent(ability="greater_heal", target=f"player_{worst}", weight=90, source="vigour_pre_tick_early_game")]

    # 1. Cleanse -- AJOUT (pas dans la liste fournie), cf. commentaire de tête.
    cleanse_target = next((i for i, p in alive.items() if p.get("cleanse_needed")), None)
    if cleanse_target is not None:
        return [Intent(ability="remove_magic", target=f"player_{cleanse_target}", weight=90, source="vigour_pre_tick_early_game")]

    # 2. Uptime du buff (priorité absolue, guide) -- Rune Of Renewal sur le
    # tank dès qu'absent ou sur le point de s'estomper (timer scopé au tank
    # seul, cf. commentaire de tête).
    if tank is not None and orbs >= VIGOUR_RUNE_OF_RENEWAL_ORB_COST:
        applied_at = state.hooks.get("early_game_rune_of_renewal_tank_applied_at", 0.0)
        if now - applied_at >= VIGOUR_EARLY_GAME_RUNE_OF_RENEWAL_REFRESH_WINDOW_S:
            state.hooks["_early_game_rune_of_renewal_pending"] = True
            return [Intent(ability="rune_of_renewal", target=f"player_{VIGOUR_TANK_SLOT}", weight=90, source="vigour_pre_tick_early_game")]

    # 3. Urgence tank (guide) -- Greater Heal si tank < 70% (routine, cible
    # spécifiquement le tank -- l'extension au reste du groupe est déjà
    # couverte par l'item 0 ci-dessus).
    if tank is not None and tank.get("hp_pct") is not None and tank["hp_pct"] < VIGOUR_EARLY_GAME_TANK_HP_THRESHOLD:
        return [Intent(ability="greater_heal", target=f"player_{VIGOUR_TANK_SLOT}", weight=90, source="vigour_pre_tick_early_game")]

    # 4. Génération de runes (guide) -- Dawnbreaker Orb (2 orbes) en 1er
    # choix dès que hors cooldown, cible le plus bas en vie même si personne
    # n'est bas (le but ici est l'orbe généré, pas le soin -- overheal
    # accepté, contrairement à Greater Heal qui reste strictement gaté par
    # le HP pour éviter le vidage de mana). Sinon Radiant Blast (mana, PAS
    # d'orbe -- cf. commentaire de tête), gaté par un plancher de mana.
    if orbs >= VIGOUR_DAWNBREAKER_ORB_COST and alive and not state.is_on_cooldown("dawnbreaker_orb", now):
        worst = min(alive, key=lambda i: alive[i]["hp_pct"] if alive[i].get("hp_pct") is not None else 1.0)
        return [Intent(ability="dawnbreaker_orb", target=f"player_{worst}", weight=90, source="vigour_pre_tick_early_game")]
    if mana >= VIGOUR_EARLY_GAME_RADIANT_BLAST_MANA_FLOOR and not state.is_on_cooldown("radiant_blast", now):
        return [Intent(ability="radiant_blast", weight=90, source="vigour_pre_tick_early_game")]

    # 5. Dépense des runes (règle du cap, guide) -- >= 5 orbes : dépenser
    # MAINTENANT plutôt que d'attendre le plafond de 6. Luminous Barrier sur
    # un allié qui encaisse des dégâts (pas au maximum), sinon Soulbrand
    # (cible ennemie, toujours disponible).
    if orbs >= VIGOUR_EARLY_GAME_RUNE_CAP_THRESHOLD:
        hurt = {
            i: p for i, p in alive.items()
            if p.get("hp_pct") is not None and p["hp_pct"] < VIGOUR_FULL_HP_THRESHOLD
        }
        if hurt and not state.is_on_cooldown("luminous_barrier", now):
            worst = min(hurt, key=lambda i: hurt[i]["hp_pct"])
            return [Intent(ability="luminous_barrier", target=f"player_{worst}", weight=90, source="vigour_pre_tick_early_game")]
        return [Intent(ability="soulbrand", weight=90, source="vigour_pre_tick_early_game")]

    # 6. Soin de groupe AoE (guide) -- Circle of Light si plusieurs alliés
    # perdent de la vie simultanément. Cf. commentaire de tête pour la
    # limite connue (pas de détection de position/cône).
    group_hurt = sum(
        1 for p in alive.values()
        if p.get("hp_pct") is not None and p["hp_pct"] < VIGOUR_HURT_HP_THRESHOLD
    )
    if group_hurt >= VIGOUR_GROUP_DAMAGE_MIN_HURT_ALLIES and tank is not None and not state.is_on_cooldown("circle_of_light", now):
        return [Intent(ability="circle_of_light", target=f"player_{VIGOUR_TANK_SLOT}", weight=90, source="vigour_pre_tick_early_game")]

    # 7. Filler (guide) -- Dawnflare en boucle, jamais de temps mort.
    return [Intent(ability="dawnflare", weight=90, source="vigour_pre_tick_early_game")]


def vigour_post_tick_early_game(state: CombatState, pressed: str) -> None:
    """Confirme l'horodatage de Rune Of Renewal sur le tank UNIQUEMENT si ce
    sort a RÉELLEMENT été le sort pressé ce tick -- même principe que
    l'ancien design retiré (cf. commentaire de tête de vigour_pre_tick),
    mais scopé à UNE seule cible (le tank), pas les 4 alliés.

    EN: Confirms Rune Of Renewal's timestamp on the tank ONLY if this spell
    EN: was ACTUALLY the one pressed this tick -- same principle as the old
    EN: removed design (cf. vigour_pre_tick's header comment), but scoped to
    EN: a SINGLE target (the tank), not all 4 allies.
    """
    if pressed == "rune_of_renewal" and state.hooks.pop("_early_game_rune_of_renewal_pending", False):
        state.hooks["early_game_rune_of_renewal_tank_applied_at"] = time.monotonic()


# =============================================================================
# HELENA (Guardian tank) -- 2026-07-18/19. config/helena.yaml pour le détail
# des captures/mappings encore manquants avant que ça tourne parfaitement en
# jeu. pre_tick_hook AJOUTÉ le 2026-07-19 (n'existait pas au premier portage) :
# uniquement pour le front montant de shield_throw_proc (bookkeeping pur, ne
# presse jamais rien lui-même -- cf. helena_pre_tick, retourne toujours []).
# Le reste de la rotation Season 3 continue de s'exprimer entièrement via le
# scan de priorité YAML (priorities/condition).
#
# EN: HELENA (Guardian tank) -- 2026-07-18/19. cf. config/helena.yaml for the
# EN: detail of captures/mappings still missing before this runs perfectly
# EN: in-game. pre_tick_hook ADDED 2026-07-19 (didn't exist at the first port):
# EN: only for shield_throw_proc's rising edge (pure bookkeeping, never
# EN: presses anything itself -- cf. helena_pre_tick, always returns []). The
# EN: rest of the Season 3 rotation still expresses entirely through the YAML
# EN: priority scan (priorities/condition).
# =============================================================================

# Front montant de shield_throw_proc -- MÊME schéma que TARIQ_PROC_BUFFS/
# MIN_ABSENT_TICKS plus haut dans ce fichier (bug "double-cast" du 2026-07-12,
# cf. commentaire complet à côté de TARIQ_PROC_BUFFS) : state.buffs["shield_throw_proc"]
# est un test de NIVEAU (le buff lit-il "actif" CE tick ?) posé par
# make_helena_state_update_fn (state_update.py) via template match sur
# helena_buff_bar. Sans front montant, un buff qui reste lu "actif" plusieurs
# ticks d'affilée referait presser shield_throw_proc plusieurs fois pour UN
# SEUL proc réel. HELENA_MIN_ABSENT_TICKS filtre le même bruit de compression
# 1-frame que côté Tariq (valeur identique, même caméra/pipeline de capture).
# EN: shield_throw_proc rising edge -- SAME scheme as TARIQ_PROC_BUFFS/
# EN: MIN_ABSENT_TICKS above in this file (the "double-cast" bug from
# EN: 2026-07-12, cf. the full comment next to TARIQ_PROC_BUFFS):
# EN: state.buffs["shield_throw_proc"] is a LEVEL test (does the buff read
# EN: "active" THIS tick?) set by make_helena_state_update_fn (state_update.py)
# EN: via template match on helena_buff_bar. Without a rising edge, a buff
# EN: that keeps reading "active" for several ticks in a row would make
# EN: shield_throw_proc fire several times for ONE real proc.
# EN: HELENA_MIN_ABSENT_TICKS filters the same 1-frame compression noise as
# EN: on the Tariq side (identical value, same capture camera/pipeline).
HELENA_MIN_ABSENT_TICKS = 3


def helena_pre_tick(state: CombatState) -> "list[Intent]":
    """Bookkeeping PUR -- ne presse jamais rien (retourne toujours []).
    Arme state.hooks["shield_throw_proc_ready"] sur le front montant absent ->
    présent de state.buffs["shield_throw_proc"], lu par la condition YAML
    `hooks:shield_throw_proc_ready` (cf. config/helena.yaml::shield_throw_proc).
    La consommation (remise à False) se fait dans helena_post_tick, au vrai cast.

    EN: PURE bookkeeping -- never presses anything (always returns []). Arms
    EN: state.hooks["shield_throw_proc_ready"] on the absent -> present rising
    EN: edge of state.buffs["shield_throw_proc"], read by the YAML condition
    EN: `hooks:shield_throw_proc_ready` (cf. config/helena.yaml::shield_throw_proc).
    EN: Consumption (reset to False) happens in helena_post_tick, on the real cast.

    FRAÎCHEUR (2026-07-19, ajoutée EN MÊME TEMPS que le passage en thread de
    make_helena_state_update_fn -- cf. son commentaire de tête, state_update.py) :
    armer ready sur une lecture PÉRIMÉE forcerait une presse sur une donnée
    qu'on ne peut plus garantir à jour -- même principe que l'audit
    2026-07-17 côté Meiko/Tariq. Le suivi absent_ticks/prev, lui, continue de
    tourner MÊME périmé (cf. boucle ci-dessous) : le geler pendant une
    période périmée fausserait la détection de front montant une fois la
    détection revenue (même raison que TARIQ_PROC_BUFFS, cf. plus haut dans
    ce fichier).

    EN: FRESHNESS (2026-07-19, added AT THE SAME TIME as
    EN: make_helena_state_update_fn's move to a thread -- cf. its header
    EN: comment, state_update.py): arming ready on a STALE reading would force
    EN: a press on data we can no longer guarantee is up to date -- same
    EN: principle as the 2026-07-17 audit on the Meiko/Tariq side. The
    EN: absent_ticks/prev tracking itself keeps running EVEN while stale (cf.
    EN: loop below): freezing it during a stale period would throw off rising-
    EN: edge detection once detection comes back (same reason as
    EN: TARIQ_PROC_BUFFS, cf. above in this file).
    """
    was_active = state.hooks.get("_shield_throw_proc_prev", False)
    is_active = state.buffs.get("shield_throw_proc", False)
    absent_ticks = state.hooks.get("_shield_throw_proc_absent_ticks", HELENA_MIN_ABSENT_TICKS)

    if is_active:
        if not was_active and absent_ticks >= HELENA_MIN_ABSENT_TICKS:
            helena_bars_meta = state.detection_meta.get("helena_bars")
            is_fresh = (
                helena_bars_meta is not None
                and HELENA_BARS_FRESHNESS_POLICY.classify(helena_bars_meta.age_ms()) != Freshness.EXPIRED
            )
            if is_fresh:
                state.hooks["shield_throw_proc_ready"] = True
        absent_ticks = 0
    else:
        absent_ticks += 1

    state.hooks["_shield_throw_proc_absent_ticks"] = absent_ticks
    state.hooks["_shield_throw_proc_prev"] = is_active
    return []


# Table CDR Veteran of War -- {ability pressée -> {ability réduite: ms}}.
# UNIQUEMENT les relations où LES DEUX abilities sont auto-castées par
# config/helena.yaml (donc où `pressed` peut RÉELLEMENT valoir la clé de
# cette table). Shield Throw (+ son proc) est AUTO-CASTÉE depuis le
# 2026-07-19 (demande explicite de l'utilisateur) -- sa contribution de CDR
# (-3s Shockwave) est donc maintenant observable et modélisée ici. Charge
# reste MANUELLE (non demandée) -- sa contribution (Shield Slam/Shield Throw/
# Shields Up/Shockwave -6s chacun) reste invisible ici. Gap documenté, pas
# silencieux (même principe que earthfall/health_pct côté Meiko).
#
# Hold the Line n'est PAS dans cette table : son effet sur Shield Slam est un
# RESET COMPLET ("Resets Shield Slam CD"), pas une réduction proportionnelle
# en ms -- modélisé à part dans helena_post_tick (cf. plus bas), cette table
# ne sait exprimer QUE des soustractions bornées.
# EN: Veteran of War CDR table -- {pressed ability -> {reduced ability: ms}}.
# EN: ONLY the relations where BOTH abilities are auto-cast by
# EN: config/helena.yaml (so where `pressed` can ACTUALLY be a key of this
# EN: table). Shield Throw (+ its proc) has been AUTO-CAST since 2026-07-19
# EN: (the user's explicit request) -- its CDR contribution (-3s Shockwave) is
# EN: therefore now observable and modeled here. Charge remains MANUAL (not
# EN: requested) -- its contribution (Shield Slam/Shield Throw/Shields Up/
# EN: Shockwave -6s each) stays invisible here. A documented gap, not a silent
# EN: one (same principle as earthfall/health_pct on the Meiko side).
# EN:
# EN: Hold the Line is NOT in this table: its effect on Shield Slam is a FULL
# EN: RESET ("Resets Shield Slam CD"), not a proportional ms reduction --
# EN: modeled separately in helena_post_tick (below), this table can only
# EN: express bounded subtractions.
HELENA_CDR_MS: "dict[str, dict[str, int]]" = {
    "measured_strike": {"shield_slam": 1500, "shield_throw": 1500},
    "power_strike": {"shield_slam": 1500, "shield_throw": 1500},
    "shield_slam": {"shockwave": 3000},
    "shield_throw": {"shockwave": 3000},
    "shield_throw_proc": {"shockwave": 3000},  # toujours un cast de Shield Throw, meme CDR · EN: still a Shield Throw cast, same CDR
    "shockwave": {"shields_up": 6000},
}


def helena_post_tick(state: CombatState, pressed: str) -> None:
    """Applique les effets Veteran of War pour `pressed` : d'abord le cas
    spécial de Hold the Line (RESET complet de Shield Slam, pas une
    réduction -- cf. HELENA_CDR_MS docstring), puis la table générique de
    réductions proportionnelles. Ne touche QUE les abilities déjà présentes
    dans state.cooldown_until (cf. CombatState.record_cast : cooldown_ms<=0
    n'y écrit jamais rien) -- pas de création d'un cooldown à partir de rien,
    seulement un raccourcissement/reset d'un cooldown réel.

    EN: Applies the Veteran of War effects for `pressed`: first Hold the
    EN: Line's special case (FULL reset of Shield Slam, not a reduction -- cf.
    EN: HELENA_CDR_MS docstring), then the generic proportional-reduction
    EN: table. Only touches abilities already present in state.cooldown_until
    EN: (cf. CombatState.record_cast: cooldown_ms<=0 never writes anything
    EN: there) -- no cooldown created out of nothing, only a real cooldown
    EN: shortened/reset.
    """
    if pressed == "hold_the_line":
        state.cooldown_until.pop("shield_slam", None)
    elif pressed == "shield_throw_proc":
        # Consommation du front montant arme par helena_pre_tick -- meme
        # principe que TARIQ_PROC_BUFFS (hooks.py, tariq_post_tick) : la
        # VRAIE presse confirmee (pas juste "propose") efface le flag, une
        # NOUVELLE transition absent->present sera necessaire pour le
        # rearmer (cf. helena_pre_tick).
        # EN: Consumes the rising edge armed by helena_pre_tick -- same
        # EN: principle as TARIQ_PROC_BUFFS (hooks.py, tariq_post_tick): the
        # EN: REAL confirmed press (not just "proposed") clears the flag, a
        # EN: NEW absent->present transition will be needed to rearm it
        # EN: (cf. helena_pre_tick).
        state.hooks["shield_throw_proc_ready"] = False

    reductions = HELENA_CDR_MS.get(pressed)
    if not reductions:
        return
    for target, reduction_ms in reductions.items():
        current = state.cooldown_until.get(target)
        if current is None:
            continue
        state.cooldown_until[target] = current - reduction_ms / 1000.0
