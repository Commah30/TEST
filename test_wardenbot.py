"""
test_wardenbot.py — prouve, sans écran ni jeu, que :
  1. la décision "3 stacks de Warden of the Temple -> Spirited Vortex" est
     déterministe, même quand Strikes serait "prioritaire" par ailleurs ;
  2. l'ouverture du combo défensif suit l'état RÉEL des buffs détectés
     (state.buffs), pas une estimation temporelle -- c'est le fix du bug
     "spam du combo défensif" rencontré en jeu.

Lance avec : python test_wardenbot.py
Aucune dépendance externe (pas besoin de mss/opencv/pydirectinput pour ce
test -- seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from wardenbot.engine import (
    MAX_WEIGHT_MODIFIER,
    Ability,
    CombatState,
    Intent,
    RotationEngine,
    Sequence,
    SequenceStep,
    load_abilities,
    load_weight_overrides,
    parse_sequences,
)
# _meiko_choose_combo_step (import direct, nom "privé") : la logique pure de
# décision testée ci-dessous par la quasi-totalité de ce fichier -- retourne
# un Optional[str], PAS un Intent. meiko_pre_tick (le vrai pre_tick_hook câblé
# sur RotationEngine, cf. game_config.py) n'est qu'un fin wrapper autour
# d'elle qui enveloppe le résultat dans un Intent de poids 90 (cf. hooks.py)
# -- testé séparément, une seule fois, tout en bas de ce fichier.
# EN: _meiko_choose_combo_step (direct import, "private" name): the pure
# EN: decision logic tested below by nearly this whole file -- returns an
# EN: Optional[str], NOT an Intent. meiko_pre_tick (the real pre_tick_hook
# EN: wired on RotationEngine, cf. game_config.py) is just a thin wrapper
# EN: around it that wraps the result in a weight-90 Intent (cf. hooks.py) --
# EN: tested separately, once, at the bottom of this file.
from wardenbot.hooks import _meiko_choose_combo_step, meiko_post_tick, meiko_pre_tick


def drive_combo(state: CombatState, opener: str) -> list[str]:
    """Presse `opener`, puis laisse meiko_pre_tick compléter le combo
    jusqu'au finisher inclus. Retourne la séquence complète."""
    sequence = [opener]
    state.record_cast(opener, now=0.0, cooldown_ms=0)
    meiko_post_tick(state, opener)

    for _ in range(5):  # garde-fou anti-boucle infinie
        nxt = _meiko_choose_combo_step(state)
        if nxt is None:
            break
        sequence.append(nxt)
        state.record_cast(nxt, now=0.0, cooldown_ms=0)
        meiko_post_tick(state, nxt)
        if nxt == "fighting_techniques":
            break
    return sequence


def test_default_opens_when_buffs_missing():
    state = CombatState()
    # Combat start : state.buffs est vide -> les deux buffs sont considérés
    # absents -> pre_tick doit décider d'ouvrir lui-même.
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm", f"attendu spirit_palm en ouverture, reçu {opener!r}"
    seq = drive_combo(state, "spirit_palm")
    print("Ouverture combat :", seq)
    assert seq[-2] in ("earth_fist_2", "wind_kick_2")


def test_default_picks_whichever_buff_is_missing():
    state = CombatState()
    # Vortex déjà up (détecté à l'écran), Strikes absent -> sans debuff,
    # Strikes doit être choisi puisque c'est LUI qui manque.
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"

    choice = _meiko_choose_combo_step(state)
    assert choice == "wind_kick_2", f"attendu wind_kick_2 (Strikes manquant), reçu {choice!r}"
    print("Vortex up / Strikes absent -> choix:", choice)


def test_three_stacks_forces_vortex_even_if_strikes_is_missing():
    state = CombatState()
    # Vortex up, Strikes absent -> sans les 3 stacks, Strikes gagnerait
    # (comme le test précédent). À 3 stacks (compteur), Vortex doit gagner
    # QUAND MÊME. Les stacks sont comptées en code, pas lues sur le badge
    # (cf. hooks.py) -- on les pose ici directement dans state.hooks.
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"

    without_stacks = _meiko_choose_combo_step(state)
    assert without_stacks == "wind_kick_2"
    print("Sans 3 stacks, Strikes manquant gagne -> choix:", without_stacks)

    state.hooks["warden_stacks"] = 3
    with_stacks = _meiko_choose_combo_step(state)
    assert with_stacks == "earth_fist_2", (
        f"avec 3 stacks, attendu earth_fist_2 (Vortex) malgré Strikes manquant, reçu {with_stacks!r}"
    )
    print("Avec 3 stacks, Vortex forcé malgré Strikes manquant -> choix:", with_stacks)


def test_full_sequence_matches_user_example():
    """Reproduit ta séquence de combat start. state.buffs est mis à jour
    manuellement à chaque étape pour simuler ce que state_update_fn ferait
    en lisant vraiment l'écran (dans le vrai bot, c'est automatique)."""
    state = CombatState()
    state.resources["stormfall"] = 0.0

    full_log: list[str] = []

    # 1) Ouverture défensive (aucun buff up) -> Vortex manquant en premier -> Vortex
    full_log += drive_combo(state, _meiko_choose_combo_step(state))
    state.buffs["spirited_vortex"] = True   # simule : Vortex vient d'être posé

    # 2) Strikes manque toujours -> réouverture -> Strikes
    full_log += drive_combo(state, "spirit_palm")
    state.buffs["spirited_strikes"] = True  # simule : Strikes vient d'être posé

    # 3) Boucle Storm : charge vide -> build (Rising Storm)
    full_log += drive_combo(state, "wind_kick")

    # 4) Charge pleine -> spend (Lashing Stormkick), 3 fois -> 3 stacks
    #    (comptées en code à chaque finisher réellement casté, pas lues sur
    #    le badge -- cf. hooks.py)
    state.resources["stormfall"] = 1.0
    full_log += drive_combo(state, "wind_kick")
    full_log += drive_combo(state, "wind_kick")
    full_log += drive_combo(state, "wind_kick")
    assert state.hooks.get("warden_stacks") == 3, state.hooks
    state.resources["stormfall"] = 0.0

    # 5) Les deux buffs sont encore up ici (30s pas écoulées) -- seules les
    #    3 stacks doivent forcer la réouverture, et forcer Vortex.
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm"
    empowered_seq = drive_combo(state, opener)
    full_log += empowered_seq
    assert empowered_seq == ["spirit_palm", "earth_fist_2", "fighting_techniques"], empowered_seq

    print("\nSéquence complète simulée :")
    print(" -> ".join(full_log))
    print("\nEmpowered Vortex bien déclenché sur earth_fist_2 dès l'apparition du debuff.")


def test_stack_counter_tracks_real_spend_combos_not_visual_badge():
    """Fix du bug "bloque à x2" rencontré en jeu : le badge x2/x3 sur
    l'icône est trop petit pour que le template matching le distingue de
    façon fiable, donc les stacks sont comptées ici à partir des VRAIS
    Lashing Stormkick castés, pas d'une lecture pixel."""
    state = CombatState()
    state.resources["stormfall"] = 1.0

    # 2 Lashing Stormkick (spend) -> 2 stacks, pas encore assez pour forcer Vortex
    drive_combo(state, "wind_kick")
    drive_combo(state, "wind_kick")
    assert state.hooks.get("warden_stacks") == 2, state.hooks

    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"
    choice = _meiko_choose_combo_step(state)
    assert choice == "wind_kick_2", f"à 2 stacks, Strikes doit encore gagner (pas Vortex forcé), reçu {choice!r}"
    print("2 Lashing Stormkick -> compteur=2, Strikes gagne encore -> choix:", choice)

    # 3e Lashing Stormkick -> 3 stacks -> force Vortex même si Strikes manque
    drive_combo(state, "wind_kick")
    assert state.hooks.get("warden_stacks") == 3, state.hooks

    state.last_cast = "spirit_palm"
    choice = _meiko_choose_combo_step(state)
    assert choice == "earth_fist_2", f"à 3 stacks (compteur), Vortex doit être forcé, reçu {choice!r}"
    print("3e Lashing Stormkick -> compteur=3, Vortex forcé -> choix:", choice)

    # Le Vortex empowered consomme les 3 stacks -> retombe à 0
    seq = drive_combo(state, "spirit_palm")
    assert seq == ["spirit_palm", "earth_fist_2", "fighting_techniques"], seq
    assert state.hooks.get("warden_stacks") == 0, state.hooks
    print("Vortex empowered casté -> compteur remis à 0 :", seq)


def test_visual_debuff_detection_is_ignored_for_stack_counting():
    """Testé en jeu : state.debuffs["warden_of_the_temple_3"] reste souvent
    bloqué à True en continu (détection peu fiable, cf. hooks.py). Un
    premier filet de sécurité qui s'y fiait pour "rattraper" le compteur a
    été retiré car il reproduisait le spam qu'on corrigeait. Ce test prouve
    que ce debuff visuel n'a plus aucune influence sur la décision."""
    state = CombatState()
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"
    state.debuffs["warden_of_the_temple_3"] = True   # bloqué à True, comme en jeu

    choice = _meiko_choose_combo_step(state)
    assert choice == "wind_kick_2", (
        f"le badge visuel seul ne doit plus forcer Vortex (compteur à 0) -- reçu {choice!r}"
    )
    assert state.hooks.get("warden_stacks", 0) == 0
    print("Debuff visuel bloqué à True mais compteur à 0 -> Strikes gagne normalement -> choix:", choice)


def test_stack_timeout_forces_recalibration():
    """Seul filet de sécurité anti-désynchro restant : si le compteur reste
    bloqué à 3 sans qu'un Vortex empowered ne parte pour le consommer
    pendant WARDEN_STACK_TIMEOUT_S, on force un recalibrage à 0."""
    from wardenbot.hooks import WARDEN_STACK_TIMEOUT_S, _set_warden_stacks
    import time

    state = CombatState()
    now = time.monotonic()
    _set_warden_stacks(state, now - WARDEN_STACK_TIMEOUT_S - 1.0, 3)  # 3 stacks "posées" il y a longtemps
    assert state.hooks.get("warden_stacks") == 3

    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = True   # les deux up -> sans les 3 stacks, rien à rouvrir
    choice = _meiko_choose_combo_step(state)
    assert choice is None, f"après timeout, le compteur doit être recalibré à 0 -> rien à forcer, reçu {choice!r}"
    assert state.hooks.get("warden_stacks") == 0
    print("Compteur à 3 depuis trop longtemps -> recalibré à 0 automatiquement.")


def test_storm_loop_not_interrupted_by_mid_loop_buff_drop():
    """BUG TROUVÉ EN JEU (2026-07-16, rapporté par l'utilisateur) : "la rotation ne
    fait que spam le combo Spirited Vortex/Strikes" -- la boucle Storm/Earth
    n'avait jamais l'occasion de tourner plus d'un cycle. Root cause :
    needs_refresh redevenait vrai dès qu'UN SEUL des deux buffs retombait,
    même en PLEIN MILIEU de la boucle Storm/Earth -- si Vortex/Strikes durent
    moins longtemps qu'un cycle complet de 3 Lashing Stormkick, le combo
    défensif reprenait la main à chaque fois. Séquence attendue (donnée par
    l'utilisateur) : ouvrir Vortex puis Strikes UNE fois en début de combat, PUIS
    laisser la boucle Storm/Earth tourner sans interruption jusqu'à 3 stacks
    (même si Vortex/Strikes retombent entre-temps), forcer Vortex à 3 stacks,
    refresh Strikes si besoin à CE moment précis, puis reprendre la boucle."""
    state = CombatState()
    state.resources["stormfall"] = 0.0

    # Ouverture initiale : les deux buffs manquent -> double refresh (Vortex
    # d'abord, puisque manquant en premier -- même logique qu'avant ce fix).
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm"
    drive_combo(state, opener)
    state.buffs["spirited_vortex"] = True   # simule : Vortex vient d'être posé
    assert state.hooks.get("meiko_defense_primed", False) is False

    opener2 = _meiko_choose_combo_step(state)
    assert opener2 == "spirit_palm", f"Strikes manque encore -> doit rouvrir, reçu {opener2!r}"
    drive_combo(state, opener2)
    state.buffs["spirited_strikes"] = True  # simule : Strikes vient d'être posé

    # "Rien en cours" : les deux sont up -> on se prime, pas de refresh.
    choice = _meiko_choose_combo_step(state)
    assert choice is None, f"les deux buffs sont up -> pas de refresh, reçu {choice!r}"
    assert state.hooks.get("meiko_defense_primed") is True
    print("Double-open initial (Vortex puis Strikes) -> primed=True, pas de refresh une fois les deux up.")

    # Boucle Storm : Strikes retombe EN PLEIN MILIEU (durée plus courte
    # qu'un cycle complet) -- ne doit RIEN interrompre une fois primed.
    state.buffs["spirited_strikes"] = False
    choice = _meiko_choose_combo_step(state)
    assert choice is None, f"primed=True -> Strikes retombé en pleine boucle ne doit PAS rouvrir le défensif, reçu {choice!r}"
    print("Strikes retombé en plein milieu de la boucle -> primed=True ignore la chute, pas de spam défensif.")

    state.resources["stormfall"] = 1.0
    drive_combo(state, "wind_kick")  # 1er Lashing Stormkick -> 1 stack
    drive_combo(state, "wind_kick")  # 2e -> 2 stacks
    drive_combo(state, "wind_kick")  # 3e -> 3 stacks
    assert state.hooks.get("warden_stacks") == 3, state.hooks
    print("3 cycles Storm/Earth complétés sans interruption malgré Strikes down -> 3 stacks atteints.")

    # 3 stacks -> Vortex forcé (déclencheur absolu, primed ou non), peu
    # importe l'état de Strikes (toujours down).
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm"
    forced_seq = drive_combo(state, opener)
    assert forced_seq == ["spirit_palm", "earth_fist_2", "fighting_techniques"], forced_seq
    assert state.hooks.get("warden_stacks") == 0
    assert state.hooks.get("meiko_defense_primed") is False, "dé-primé après le Vortex forcé, pour re-checker Strikes"
    print("3 stacks -> Vortex forcé malgré Strikes down, compteur remis à 0, dé-primé pour re-checker Strikes.")

    # Strikes toujours down après le Vortex forcé -> UNE relecture, refresh
    # Strikes avant de reprendre la boucle.
    state.buffs["spirited_vortex"] = True  # vient d'être re-casté par le Vortex forcé
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm", f"Strikes toujours down après le Vortex forcé -> doit rouvrir pour la refresh, reçu {opener!r}"
    refresh_seq = drive_combo(state, opener)
    assert refresh_seq == ["spirit_palm", "wind_kick_2", "fighting_techniques"], refresh_seq
    state.buffs["spirited_strikes"] = True

    # Les deux sont up -> re-prime, retour à la boucle (pas de refresh de trop).
    choice = _meiko_choose_combo_step(state)
    assert choice is None
    assert state.hooks.get("meiko_defense_primed") is True
    print("Strikes re-rafraîchi après le Vortex forcé -> re-primed, retour direct à la boucle Storm/Earth.")


def test_strikes_proactive_refresh_before_expiry():
    """DEMANDE DE L'UTILISATEUR (2026-07-16) : Spirited Strikes dure 30s -- une fois
    primé, il doit être refresh PROACTIVEMENT quand il reste
    ≤STRIKES_PROACTIVE_REFRESH_WINDOW_S (lecture OCR réelle du compte à
    rebours, cf. state.resources["spirited_strikes_remaining_s"]), PAS
    attendre qu'il tombe à False. Vortex, lui, ne doit JAMAIS être re-casté
    hors de la règle des 3 stacks, même si son propre buff venait à
    flickerer -- vérifié ici aussi."""
    from wardenbot.hooks import STRIKES_PROACTIVE_REFRESH_WINDOW_S

    state = CombatState()
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = True
    state.hooks["meiko_defense_primed"] = True   # déjà primé -- storm loop en cours

    # Loin de l'expiration -> pas de refresh, la boucle continue.
    state.resources["spirited_strikes_remaining_s"] = STRIKES_PROACTIVE_REFRESH_WINDOW_S + 10.0
    choice = _meiko_choose_combo_step(state)
    assert choice is None, f"Strikes loin de l'expiration -> pas de refresh, reçu {choice!r}"

    # Sous le seuil -> refresh proactif de Strikes (PAS Vortex).
    state.resources["spirited_strikes_remaining_s"] = STRIKES_PROACTIVE_REFRESH_WINDOW_S - 1.0
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm", f"Strikes proche de l'expiration -> doit rouvrir, reçu {opener!r}"
    seq = drive_combo(state, opener)
    assert seq == ["spirit_palm", "wind_kick_2", "fighting_techniques"], seq
    print("Strikes proche de l'expiration (OCR) -> refresh proactif déclenché, wind_kick_2 (Strikes) choisi.")

    # Vortex qui flickererait à False une fois primé ne doit RIEN déclencher
    # (seule la règle des 3 stacks compte désormais) -- même avec Strikes
    # confortablement loin de l'expiration.
    state.buffs["spirited_vortex"] = False
    state.resources["spirited_strikes_remaining_s"] = STRIKES_PROACTIVE_REFRESH_WINDOW_S + 10.0
    choice = _meiko_choose_combo_step(state)
    assert choice is None, f"Vortex qui flickere une fois primé ne doit rien déclencher, reçu {choice!r}"
    print("Vortex à False (flicker simulé) une fois primé -> ignoré, seule la règle des 3 stacks compte.")


def test_strikes_refresh_confirm_window_prevents_infinite_retrigger_loop():
    """BUG TROUVÉ EN JEU (2026-07-16, log Meiko analysé avec l'utilisateur) : le
    refresh proactif de Strikes tournait en boucle PENDANT PLUS DE 25
    SECONDES d'affilée -- spirited_strikes_remaining_s restait bloqué à la
    MÊME valeur basse (hoquet de détection, cf. capture_watchdog.log :
    cycles à 2.551s/3.490s, seuil 0.2s) et le hook re-déclenchait un refresh
    à chaque tick sans jamais laisser le temps à l'OCR de confirmer que le
    cast précédent avait fonctionné. Ce test verrouille le fix : une fois un
    refresh déclenché, une lecture bloquée à la même valeur basse ne doit
    PAS en redéclencher un autre avant MEIKO_DEFENSE_CONFIRM_WINDOW_S."""
    from wardenbot.hooks import MEIKO_DEFENSE_CONFIRM_WINDOW_S

    state = CombatState()
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = True
    state.hooks["meiko_defense_primed"] = True

    # Premier refresh proactif -- déclenché normalement.
    state.resources["spirited_strikes_remaining_s"] = 4.0
    opener = _meiko_choose_combo_step(state)
    assert opener == "spirit_palm", f"1er refresh doit se déclencher, reçu {opener!r}"
    drive_combo(state, opener)
    assert state.hooks.get("_strikes_refresh_pending_since") is not None

    # Détection en plein hoquet -- la lecture reste bloquée à la MÊME valeur
    # basse pendant plusieurs ticks -> ne doit RIEN redéclencher tant que la
    # fenêtre de confirmation n'est pas écoulée.
    for _ in range(5):
        choice = _meiko_choose_combo_step(state)
        assert choice is None, f"lecture bloquée pendant le hoquet -> ne doit pas rouvrir, reçu {choice!r}"
    print("Lecture OCR bloquée à la même valeur basse après un refresh -> pas de re-déclenchement en boucle.")

    # La fenêtre de confirmation expire sans qu'une lecture fraîche ne soit
    # jamais arrivée (cas "le cast précédent a réellement échoué") -> on
    # retente.
    state.hooks["_strikes_refresh_pending_since"] -= MEIKO_DEFENSE_CONFIRM_WINDOW_S + 1.0
    choice = _meiko_choose_combo_step(state)
    assert choice == "spirit_palm", f"fenêtre expirée sans confirmation -> doit retenter, reçu {choice!r}"
    print("Fenêtre de confirmation expirée sans lecture fraîche -> nouvelle tentative autorisée.")

    # Cas normal : une lecture fraîche (buff bien refresh) lève la fenêtre
    # immédiatement, pas besoin d'attendre son expiration.
    state.hooks["_strikes_refresh_pending_since"] = time.monotonic()
    state.resources["spirited_strikes_remaining_s"] = 29.0
    choice = _meiko_choose_combo_step(state)
    assert choice is None
    assert state.hooks.get("_strikes_refresh_pending_since") is None, "une lecture fraîche doit lever la fenêtre immédiatement"
    print("Lecture fraîche (buff confirmé refresh) -> fenêtre levée immédiatement, pas d'attente inutile.")


def test_detection_exception_on_one_ability_does_not_freeze_the_scan():
    """BUG TROUVÉ EN JEU (2026-07-14, analyse vidéo + logs Tariq) : une
    exception dans is_ready_fn (capture d'écran qui hoquette -- BitBlt) pour
    UNE ability à détection (ex: face_breaker, la seule sans skip_detection)
    tuait tout le tick, empêchant les abilities de priorité INFÉRIEURE
    (wild_swing) de jamais partir. Vérifie que le scan saute l'ability qui
    lève et continue -- la rotation ne gèle plus sur un hoquet de capture."""
    detector = Ability(name="detector", priority_st=1, skip_detection=False)   # déclenche is_ready_fn
    filler = Ability(name="filler", priority_st=2, skip_detection=True)         # priorité inférieure, aucune détection

    def is_ready_fn_that_throws(name, threshold=None):
        raise RuntimeError("BitBlt (capture simulée qui hoquette)")

    pressed_names: list[str] = []
    engine = RotationEngine(
        abilities=[detector, filler],
        state=CombatState(mode="st"),
        press_fn=lambda name: pressed_names.append(name),
        is_ready_fn=is_ready_fn_that_throws,
    )
    pressed = engine.tick()
    assert pressed == "filler", (
        f"le filler (priorité inférieure) doit partir malgré l'exception de détection sur 'detector', reçu {pressed!r}"
    )
    assert pressed_names == ["filler"]
    print("Exception de détection sur une ability -> le scan continue, le filler part quand même.")


def test_meiko_pre_tick_wraps_result_as_weighted_intent():
    """meiko_pre_tick est le VRAI pre_tick_hook câblé sur RotationEngine (cf.
    game_config.py) -- contrairement à _meiko_choose_combo_step (testé
    directement partout ailleurs dans ce fichier), il doit respecter le
    contrat Intent (engine.py) : liste vide si rien à proposer, sinon une
    liste d'Intent de poids 90 (cf. hooks.py -- reproduit l'ancien GOTO
    absolu, toujours > 50, le poids de base du scan de priorité YAML)."""
    state = CombatState()   # rien casté -> ouverture, Vortex/Strikes absents -> ouvre
    intents = meiko_pre_tick(state)
    assert len(intents) == 1, f"attendu exactement 1 Intent, reçu {intents!r}"
    assert intents[0].ability == "spirit_palm"
    assert intents[0].weight == 90
    assert intents[0].source == "meiko_pre_tick"
    print("meiko_pre_tick enveloppe bien sa décision dans un Intent de poids 90.")

    # Rien à compléter (dernier cast = finisher, pas de combo en cours, tous
    # les buffs déjà up) -> liste vide, pas [Intent(ability=None, ...)].
    state.last_cast = "fighting_techniques"
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = True
    state.hooks["meiko_defense_primed"] = True
    assert meiko_pre_tick(state) == [], "rien à compléter -> liste vide, pas un Intent avec ability=None"
    print("meiko_pre_tick retourne une liste vide quand rien à proposer (pas un Intent creux).")


def test_engine_tick_prefers_hook_intent_over_scan_intent_by_weight():
    """Le coeur de la migration Intent (2026-07-14) : RotationEngine.tick()
    doit choisir l'Intent au poids le plus élevé plutôt que de laisser le
    hook gagner par court-circuit codé en dur. Une ability de priorité YAML
    maximale (priority_st=0) doit quand même perdre face à un hook qui
    propose autre chose, puisque son poids (90) dépasse le poids de base du
    scan (50)."""
    yaml_winner = Ability(name="yaml_winner", priority_st=0, skip_detection=True, cooldown_ms=0)
    hook_winner = Ability(name="hook_winner", priority_st=99, skip_detection=True, cooldown_ms=0)

    def fake_pre_tick_hook(state):
        return [Intent(ability="hook_winner", weight=90, source="test_hook")]

    pressed_names: list[str] = []
    engine = RotationEngine(
        abilities=[yaml_winner, hook_winner],
        state=CombatState(mode="st"),
        press_fn=lambda name: pressed_names.append(name),
        is_ready_fn=lambda name, threshold=None: True,
        pre_tick_hook=fake_pre_tick_hook,
    )
    pressed = engine.tick()
    assert pressed == "hook_winner", f"le hook (poids 90) doit gagner sur le scan (poids 50), reçu {pressed!r}"
    assert pressed_names == ["hook_winner"]
    print("RotationEngine.tick() retient bien l'Intent au poids le plus élevé (hook 90 > scan 50).")


def test_parse_sequences_builds_typed_objects_and_handles_absence():
    """parse_sequences (engine.py) -- absent/vide -> liste vide (Tariq
    aujourd'hui, comportement par défaut, cf. main.py::build_engine) ; bloc
    réel -> objets Sequence/SequenceStep typés, cf. config/warden.yaml."""
    assert parse_sequences(None) == []
    assert parse_sequences([]) == []

    raw = [{
        "name": "seq_a",
        "condition": "last:x",
        "interruptible": False,
        "steps": [
            {"ability": "step_a"},
            {"ability": "step_b", "condition": "last:step_a"},
        ],
    }]
    parsed = parse_sequences(raw)
    assert len(parsed) == 1
    seq = parsed[0]
    assert seq.name == "seq_a"
    assert seq.condition == "last:x"
    assert seq.interruptible is False
    assert len(seq.steps) == 2
    assert seq.steps[0].ability == "step_a" and seq.steps[0].condition == ""
    assert seq.steps[1].ability == "step_b" and seq.steps[1].condition == "last:step_a"
    print("parse_sequences : absence -> liste vide, bloc réel -> Sequence/SequenceStep typés correctement.")


def test_sequence_tracker_advances_through_all_steps_in_isolation():
    """Sequence Tracker (engine.py::_advance_sequence) -- vérifie la
    traversée complète des 4 séquences Storm/Earth (build+spend, cf.
    config/warden.yaml) SANS aucun hook Python en concurrence (pas de
    pre_tick_hook), pour prouver le mécanisme de suivi lui-même : entrée sur
    condition, avance step par step UNIQUEMENT quand le step gagne
    réellement, purge une fois terminée. La préséance du hook pendant la
    transition est testée séparément (cf.
    test_sequence_never_overrides_active_meiko_pre_tick_hook)."""
    _, meta = load_abilities("config/warden.yaml")
    sequences = parse_sequences(meta.get("sequences"))
    assert {s.name for s in sequences} == {
        "meiko_rising_storm", "meiko_lashing_stormkick",
        "meiko_rising_earth", "meiko_earthfist_barrage",
    }

    # (nom de séquence, ouvreur déjà casté, ressource, valeur, 2e pièce attendue)
    scenarios = [
        ("meiko_rising_storm", "wind_kick", "stormfall", 0.0, "spirit_palm_2"),
        ("meiko_lashing_stormkick", "wind_kick", "stormfall", 1.0, "earth_fist_2"),
        ("meiko_rising_earth", "earth_fist", "earthfall", 0.0, "spirit_palm_2"),
        ("meiko_earthfist_barrage", "earth_fist", "earthfall", 1.0, "wind_kick_2"),
    ]
    for seq_name, opener, resource_key, resource_value, expected_step0 in scenarios:
        pressed_names: list[str] = []
        state = CombatState(mode="aoe")
        state.last_cast = opener   # simule : l'ouvreur vient d'être casté (scan classique, hors séquence)
        state.resources[resource_key] = resource_value
        engine = RotationEngine(
            abilities=[],
            state=state,
            press_fn=lambda name: pressed_names.append(name),
            is_ready_fn=lambda name, threshold=None: True,
            sequences=sequences,
        )

        assert engine.tick() == expected_step0, f"{seq_name} : step0 attendu {expected_step0!r}"
        assert state.sequences["active_sequence"] == seq_name
        assert state.sequences["sequence_step"] == 1

        assert engine.tick() == "fighting_techniques", f"{seq_name} : step1 attendu fighting_techniques"
        assert state.sequences["sequence_step"] == 2

        # Séquence terminée (step_index=2 >= len(steps)=2) -> purge -- plus
        # rien à proposer (abilities=[] -> le scan classique ne propose rien
        # non plus).
        assert engine.tick() is None, f"{seq_name} : doit se purger une fois terminée"
        assert state.sequences.get("active_sequence") is None
        assert pressed_names == [expected_step0, "fighting_techniques"]
    print("Sequence Tracker : les 4 séquences Storm/Earth (build+spend) se déroulent bien step par step, purgent une fois terminées.")


def test_sequence_never_overrides_active_meiko_pre_tick_hook():
    """Le point central de la migration Phase 3 : tant que meiko_pre_tick
    (poids 90) gère encore un combo Storm/Earth, AUCUNE des 4 séquences
    (poids 80, config/warden.yaml) ne doit avoir le dernier mot -- que le
    hook et la séquence proposent la même ability (build) ou des abilities
    DIFFÉRENTES (spend, qui pose en plus un stack de Warden of the Temple,
    cf. hooks.py). Vérifie aussi que la bonne séquence s'active en parallèle
    (mutuelle exclusion build/spend via stormfall_above/earthfall_above,
    engine.py::_eval_token) sans jamais avancer d'un cran."""
    _, meta = load_abilities("config/warden.yaml")
    sequences = parse_sequences(meta.get("sequences"))

    # (ouvreur, ressource, valeur, ability attendue du hook, séquence attendue active)
    scenarios = [
        ("wind_kick", "stormfall", 0.0, "spirit_palm_2", "meiko_rising_storm"),
        ("wind_kick", "stormfall", 1.0, "earth_fist_2", "meiko_lashing_stormkick"),
        ("earth_fist", "earthfall", 0.0, "spirit_palm_2", "meiko_rising_earth"),
        ("earth_fist", "earthfall", 1.0, "wind_kick_2", "meiko_earthfist_barrage"),
    ]
    for opener, resource_key, resource_value, expected_pressed, expected_active_sequence in scenarios:
        state = CombatState(mode="aoe")
        state.last_cast = opener
        state.resources[resource_key] = resource_value
        engine = RotationEngine(
            abilities=[],
            state=state,
            press_fn=lambda name: None,
            is_ready_fn=lambda name, threshold=None: True,
            pre_tick_hook=meiko_pre_tick,
            post_tick_hook=meiko_post_tick,
            sequences=sequences,
        )
        pressed = engine.tick()
        assert pressed == expected_pressed, (
            f"{opener}/{resource_key}={resource_value} : attendu {expected_pressed!r}, reçu {pressed!r}"
        )
        assert "meiko_pre_tick" in state.debug_choice_reason, (
            f"le hook doit rester la source, reçu {state.debug_choice_reason!r}"
        )
        assert state.sequences.get("active_sequence") == expected_active_sequence, (
            f"la bonne séquence doit s'activer en parallèle (mutuelle exclusion build/spend), "
            f"reçu {state.sequences.get('active_sequence')!r}"
        )
        assert state.sequences.get("sequence_step", 0) == 0, "la séquence ne doit PAS avancer quand le hook gagne à sa place"
    print("Aucune des 4 séquences Storm/Earth ne prend jamais le pas sur meiko_pre_tick tant qu'il gère le combo (poids 90 > 80).")


def test_load_weight_overrides_absent_file_returns_empty_dict():
    """Fichier absent -> {} sans erreur -- comportement par défaut tant que
    l'utilisateur n'a rien approuvé (cf. kb_engine.py, "human-in-the-loop")."""
    missing = Path(tempfile.gettempdir()) / "definitely_missing_tuning_file_xyz.json"
    assert not missing.exists()
    assert load_weight_overrides(missing) == {}
    print("load_weight_overrides : fichier absent -> {} sans erreur.")


def test_load_weight_overrides_clamps_to_max_weight_modifier():
    """Chaque offset est clampé à ±MAX_WEIGHT_MODIFIER À LA LECTURE -- même
    si kb_engine.py (ou une édition manuelle) produit une valeur aberrante,
    cf. discussion 'les 3 règles d'or', règle du 'borner l'apprentissage'."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tuning.json"
        path.write_text(json.dumps({
            "overrides": {
                "hammer_storm_proc": {"weight_offset": 5},
                "wild_swing": {"weight_offset": -999},
                "chain_lightning": 999,
            }
        }), encoding="utf-8")
        overrides = load_weight_overrides(path)
        assert overrides["hammer_storm_proc"] == 5
        assert overrides["wild_swing"] == -MAX_WEIGHT_MODIFIER
        assert overrides["chain_lightning"] == MAX_WEIGHT_MODIFIER
    print("load_weight_overrides : les offsets sont bien clampés à ±MAX_WEIGHT_MODIFIER.")


def test_load_weight_overrides_malformed_json_returns_empty_dict():
    """Un fichier de tuning cassé ne doit jamais empêcher le bot de démarrer."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tuning.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert load_weight_overrides(path) == {}
    print("load_weight_overrides : JSON invalide -> {} sans exception.")


def test_weight_overrides_can_let_scan_win_over_a_weakened_sequence():
    """GARDE-FOU du court-circuit (engine.py::tick(), Phase 4) : un offset
    appris qui affaiblit fortement une séquence (poids effectif sous le pire
    poids possible du scan, 50+MAX_WEIGHT_MODIFIER) doit laisser le scan
    reprendre la main -- le court-circuit ne doit PAS ignorer aveuglément le
    scan dès qu'une séquence propose quelque chose."""
    sequence = Sequence(
        name="weak_seq",
        condition="last:opener",
        steps=[SequenceStep(ability="sequence_ability")],
    )
    scan_winner = Ability(name="scan_ability", priority_st=0, skip_detection=True, cooldown_ms=0)

    pressed_names: list[str] = []
    state = CombatState(mode="st")
    state.last_cast = "opener"
    engine = RotationEngine(
        abilities=[scan_winner],
        state=state,
        press_fn=lambda name: pressed_names.append(name),
        is_ready_fn=lambda name, threshold=None: True,
        sequences=[sequence],
        weight_overrides={
            "sequence_ability": -MAX_WEIGHT_MODIFIER,   # 80 - 20 = 60
            "scan_ability": MAX_WEIGHT_MODIFIER,         # 50 + 20 = 70
        },
    )

    pressed = engine.tick()
    assert pressed == "scan_ability", (
        f"le scan (poids effectif 70) doit gagner sur la séquence affaiblie (poids effectif 60), reçu {pressed!r}"
    )
    print("Un offset appris peut bien laisser le scan reprendre la main sur une séquence affaiblie -- le garde-fou fonctionne.")


def test_weight_overrides_never_let_scan_beat_a_weakened_hook():
    """Même à l'offset maximal, le hook (base 90) reste toujours >= le scan
    au mieux (50+20=70, 90-20=70 -- égalité jamais dépassée) : contrairement
    à une séquence, le hook garde la priorité même affaibli au maximum."""
    def hook(state):
        return [Intent(ability="hook_ability", weight=90, source="test_hook")]

    scan_winner = Ability(name="scan_ability", priority_st=0, skip_detection=True, cooldown_ms=0)

    pressed_names: list[str] = []
    engine = RotationEngine(
        abilities=[scan_winner],
        state=CombatState(mode="st"),
        press_fn=lambda name: pressed_names.append(name),
        is_ready_fn=lambda name, threshold=None: True,
        pre_tick_hook=hook,
        weight_overrides={
            "hook_ability": -MAX_WEIGHT_MODIFIER,
            "scan_ability": MAX_WEIGHT_MODIFIER,
        },
    )
    pressed = engine.tick()
    assert pressed == "hook_ability", f"le hook doit gagner même affaibli au maximum (égalité jamais dépassée), reçu {pressed!r}"
    print("Le hook garde toujours la priorité sur le scan même affaibli au maximum (égalité à la borne, jamais dépassée).")


def test_telemetry_logs_only_when_something_is_pressed():
    """RotationEngine.tick() ne doit journaliser QUE les ticks où une
    décision est RÉELLEMENT pressée -- jamais un tick à vide (cf.
    telemetry.py, 'chaque décision majeure', pas chaque tick à 30-60Hz)."""
    from wardenbot.telemetry import TelemetryWorker

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "telemetry.jsonl"
        worker = TelemetryWorker(log_file=log_path)
        worker.start()

        idle_ability = Ability(name="idle", priority_st=0, cooldown_ms=999999, skip_detection=True)
        state = CombatState(mode="st")
        state.cooldown_until["idle"] = time.monotonic() + 999.0   # jamais prêt -> aucune presse possible
        engine = RotationEngine(
            abilities=[idle_ability],
            state=state,
            press_fn=lambda name: None,
            is_ready_fn=lambda name, threshold=None: True,
            telemetry=worker,
        )
        assert engine.tick() is None   # rien de pressé -> pas de log

        engine.state.cooldown_until.pop("idle")
        pressed = engine.tick()
        assert pressed == "idle"

        worker.stop(timeout=5.0)
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1, f"une seule décision RÉELLEMENT pressée -> une seule ligne, reçu {len(lines)}"
        entry = json.loads(lines[0])
        assert entry["decision"]["ability"] == "idle"
        assert "gcd_slack_ms" in entry and "effective_weight" in entry and "choice_reason" in entry
    print("RotationEngine ne journalise que les ticks avec une vraie presse, jamais les ticks à vide.")


def test_strikes_not_recast_immediately_after_open_due_to_detection_lag():
    """BUG TROUVÉ EN JEU (2026-07-17, rapport de session l'utilisateur) : Strikes
    castée deux fois de suite en ouverture -- needs_refresh rouvrait le
    combo défensif à tort quand la détection de Strikes n'avait pas encore
    rattrapé le cast qui venait de réussir, et le 'défaut sûr' de la branche
    spirit_palm recastait Strikes aveuglément. Reproduit ici : Vortex
    confirmée up, Strikes VIENT d'être ouverte
    (_strikes_open_pending_since tout juste posé) mais sa détection n'a pas
    encore rattrapé (strikes_up=False)."""
    state = CombatState()
    state.last_cast = "fighting_techniques"   # combo Strikes tout juste terminé
    state.buffs["spirited_vortex"] = True     # Vortex déjà confirmée (1er cast du double-open)
    state.buffs["spirited_strikes"] = False   # détection PAS ENCORE rattrapée -- le coeur du bug
    state.hooks["_strikes_open_pending_since"] = time.monotonic()   # on VIENT de tenter Strikes

    choice = _meiko_choose_combo_step(state)
    assert choice is None, (
        f"ne doit PAS rouvrir le combo défensif tant que la fenêtre de confirmation de Strikes n'est pas écoulée, reçu {choice!r}"
    )
    print("Strikes en attente de confirmation -> pas de réouverture prématurée du combo défensif (bug du 2026-07-17 corrigé).")


def test_defensive_combo_reopens_once_pending_window_truly_expires():
    """Symétrique du test précédent : si la fenêtre de confirmation expire
    VRAIMENT sans que Strikes ne soit jamais confirmée (le cast a réellement
    échoué, pas juste un hoquet de détection), le combo défensif doit
    reprendre la main -- le fix ne doit pas bloquer un vrai besoin de refresh."""
    from wardenbot.hooks import MEIKO_DEFENSE_CONFIRM_WINDOW_S

    state = CombatState()
    state.last_cast = "fighting_techniques"
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.hooks["_strikes_open_pending_since"] = time.monotonic() - (MEIKO_DEFENSE_CONFIRM_WINDOW_S + 0.1)

    choice = _meiko_choose_combo_step(state)
    assert choice == "spirit_palm", f"la fenêtre a vraiment expiré -> doit rouvrir pour de bon, reçu {choice!r}"
    print("Fenêtre de confirmation Strikes réellement expirée -> le combo défensif reprend bien la main.")


def test_lashing_stormkick_fires_again_when_one_orb_still_remains():
    """BUG TROUVÉ EN JEU (2026-07-17, rapport de session l'utilisateur) : 'Rising
    Storm -> Lashing Stormkick -> Rising Storm' (reset prématuré) au lieu de
    'Rising Storm -> Lashing Stormkick -> Lashing Stormkick -> Rising Storm'
    -- Lashing Stormkick ne consomme qu'UN orbe par cast (stormfall
    1.0 -> 0.5), pas les deux -- le hook doit continuer à dépenser tant
    qu'il en reste au moins un, pas revenir à 'build' dès que ce n'est plus
    1.0 pile."""
    state = CombatState()
    state.last_cast = "wind_kick"
    state.resources["stormfall"] = 0.5   # 1 seul orbe restant après une 1re dépense

    choice = _meiko_choose_combo_step(state)
    assert choice == "earth_fist_2", (
        f"doit continuer à dépenser (Lashing Stormkick) tant qu'il reste au moins un orbe, reçu {choice!r}"
    )
    print("Lashing Stormkick refeu bien tant qu'il reste au moins un orbe de Stormfall (bug du 2026-07-17 corrigé).")


def test_earthfist_barrage_fires_again_when_one_orb_still_remains():
    """Symétrique côté Earth Fist/Earthfall -- même bug, même fix, cf.
    STORM_EARTH_SPEND_THRESHOLD (hooks.py). Earthfall n'a pas encore de
    détection réelle en jeu (cf. CLAUDE.md), mais la LOGIQUE doit être
    cohérente dès aujourd'hui pour ne pas avoir à corriger ce même bug une
    2e fois plus tard."""
    state = CombatState()
    state.last_cast = "earth_fist"
    state.resources["earthfall"] = 0.5

    choice = _meiko_choose_combo_step(state)
    assert choice == "wind_kick_2", f"doit continuer à dépenser (Earthfist Barrage), reçu {choice!r}"
    print("Earthfist Barrage refeu bien tant qu'il reste au moins un orbe d'Earthfall (même fix, cohérence).")


if __name__ == "__main__":
    test_default_opens_when_buffs_missing()
    test_default_picks_whichever_buff_is_missing()
    test_three_stacks_forces_vortex_even_if_strikes_is_missing()
    test_full_sequence_matches_user_example()
    test_stack_counter_tracks_real_spend_combos_not_visual_badge()
    test_visual_debuff_detection_is_ignored_for_stack_counting()
    test_stack_timeout_forces_recalibration()
    test_storm_loop_not_interrupted_by_mid_loop_buff_drop()
    test_strikes_proactive_refresh_before_expiry()
    test_strikes_refresh_confirm_window_prevents_infinite_retrigger_loop()
    test_detection_exception_on_one_ability_does_not_freeze_the_scan()
    test_meiko_pre_tick_wraps_result_as_weighted_intent()
    test_engine_tick_prefers_hook_intent_over_scan_intent_by_weight()
    test_parse_sequences_builds_typed_objects_and_handles_absence()
    test_sequence_tracker_advances_through_all_steps_in_isolation()
    test_sequence_never_overrides_active_meiko_pre_tick_hook()
    test_load_weight_overrides_absent_file_returns_empty_dict()
    test_load_weight_overrides_clamps_to_max_weight_modifier()
    test_load_weight_overrides_malformed_json_returns_empty_dict()
    test_weight_overrides_can_let_scan_win_over_a_weakened_sequence()
    test_weight_overrides_never_let_scan_beat_a_weakened_hook()
    test_telemetry_logs_only_when_something_is_pressed()
    test_strikes_not_recast_immediately_after_open_due_to_detection_lag()
    test_defensive_combo_reopens_once_pending_window_truly_expires()
    test_lashing_stormkick_fires_again_when_one_orb_still_remains()
    test_earthfist_barrage_fires_again_when_one_orb_still_remains()
    print("\nTous les tests passent.")
