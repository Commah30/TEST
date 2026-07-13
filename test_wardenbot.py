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

from wardenbot.engine import Ability, RotationEngine, State
from wardenbot.hooks import meiko_pre_tick, meiko_post_tick


def drive_combo(state: State, opener: str) -> list[str]:
    """Presse `opener`, puis laisse meiko_pre_tick compléter le combo
    jusqu'au finisher inclus. Retourne la séquence complète."""
    sequence = [opener]
    state.record_cast(opener, now=0.0, cooldown_ms=0)
    meiko_post_tick(state, opener)

    for _ in range(5):  # garde-fou anti-boucle infinie
        nxt = meiko_pre_tick(state)
        if nxt is None:
            break
        sequence.append(nxt)
        state.record_cast(nxt, now=0.0, cooldown_ms=0)
        meiko_post_tick(state, nxt)
        if nxt == "fighting_techniques":
            break
    return sequence


def test_default_opens_when_buffs_missing():
    state = State()
    # Combat start : state.buffs est vide -> les deux buffs sont considérés
    # absents -> pre_tick doit décider d'ouvrir lui-même.
    opener = meiko_pre_tick(state)
    assert opener == "spirit_palm", f"attendu spirit_palm en ouverture, reçu {opener!r}"
    seq = drive_combo(state, "spirit_palm")
    print("Ouverture combat :", seq)
    assert seq[-2] in ("earth_fist_2", "wind_kick_2")


def test_default_picks_whichever_buff_is_missing():
    state = State()
    # Vortex déjà up (détecté à l'écran), Strikes absent -> sans debuff,
    # Strikes doit être choisi puisque c'est LUI qui manque.
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"

    choice = meiko_pre_tick(state)
    assert choice == "wind_kick_2", f"attendu wind_kick_2 (Strikes manquant), reçu {choice!r}"
    print("Vortex up / Strikes absent -> choix:", choice)


def test_three_stacks_forces_vortex_even_if_strikes_is_missing():
    state = State()
    # Vortex up, Strikes absent -> sans les 3 stacks, Strikes gagnerait
    # (comme le test précédent). À 3 stacks (compteur), Vortex doit gagner
    # QUAND MÊME. Les stacks sont comptées en code, pas lues sur le badge
    # (cf. hooks.py) -- on les pose ici directement dans state.flags.
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"

    without_stacks = meiko_pre_tick(state)
    assert without_stacks == "wind_kick_2"
    print("Sans 3 stacks, Strikes manquant gagne -> choix:", without_stacks)

    state.flags["warden_stacks"] = 3
    with_stacks = meiko_pre_tick(state)
    assert with_stacks == "earth_fist_2", (
        f"avec 3 stacks, attendu earth_fist_2 (Vortex) malgré Strikes manquant, reçu {with_stacks!r}"
    )
    print("Avec 3 stacks, Vortex forcé malgré Strikes manquant -> choix:", with_stacks)


def test_full_sequence_matches_user_example():
    """Reproduit ta séquence de combat start. state.buffs est mis à jour
    manuellement à chaque étape pour simuler ce que state_update_fn ferait
    en lisant vraiment l'écran (dans le vrai bot, c'est automatique)."""
    state = State()
    state.resources["stormfall"] = 0.0

    full_log: list[str] = []

    # 1) Ouverture défensive (aucun buff up) -> Vortex manquant en premier -> Vortex
    full_log += drive_combo(state, meiko_pre_tick(state))
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
    assert state.flags.get("warden_stacks") == 3, state.flags
    state.resources["stormfall"] = 0.0

    # 5) Les deux buffs sont encore up ici (30s pas écoulées) -- seules les
    #    3 stacks doivent forcer la réouverture, et forcer Vortex.
    opener = meiko_pre_tick(state)
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
    state = State()
    state.resources["stormfall"] = 1.0

    # 2 Lashing Stormkick (spend) -> 2 stacks, pas encore assez pour forcer Vortex
    drive_combo(state, "wind_kick")
    drive_combo(state, "wind_kick")
    assert state.flags.get("warden_stacks") == 2, state.flags

    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"
    choice = meiko_pre_tick(state)
    assert choice == "wind_kick_2", f"à 2 stacks, Strikes doit encore gagner (pas Vortex forcé), reçu {choice!r}"
    print("2 Lashing Stormkick -> compteur=2, Strikes gagne encore -> choix:", choice)

    # 3e Lashing Stormkick -> 3 stacks -> force Vortex même si Strikes manque
    drive_combo(state, "wind_kick")
    assert state.flags.get("warden_stacks") == 3, state.flags

    state.last_cast = "spirit_palm"
    choice = meiko_pre_tick(state)
    assert choice == "earth_fist_2", f"à 3 stacks (compteur), Vortex doit être forcé, reçu {choice!r}"
    print("3e Lashing Stormkick -> compteur=3, Vortex forcé -> choix:", choice)

    # Le Vortex empowered consomme les 3 stacks -> retombe à 0
    seq = drive_combo(state, "spirit_palm")
    assert seq == ["spirit_palm", "earth_fist_2", "fighting_techniques"], seq
    assert state.flags.get("warden_stacks") == 0, state.flags
    print("Vortex empowered casté -> compteur remis à 0 :", seq)


def test_visual_debuff_detection_is_ignored_for_stack_counting():
    """Testé en jeu : state.debuffs["warden_of_the_temple_3"] reste souvent
    bloqué à True en continu (détection peu fiable, cf. hooks.py). Un
    premier filet de sécurité qui s'y fiait pour "rattraper" le compteur a
    été retiré car il reproduisait le spam qu'on corrigeait. Ce test prouve
    que ce debuff visuel n'a plus aucune influence sur la décision."""
    state = State()
    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = False
    state.last_cast = "spirit_palm"
    state.debuffs["warden_of_the_temple_3"] = True   # bloqué à True, comme en jeu

    choice = meiko_pre_tick(state)
    assert choice == "wind_kick_2", (
        f"le badge visuel seul ne doit plus forcer Vortex (compteur à 0) -- reçu {choice!r}"
    )
    assert state.flags.get("warden_stacks", 0) == 0
    print("Debuff visuel bloqué à True mais compteur à 0 -> Strikes gagne normalement -> choix:", choice)


def test_stack_timeout_forces_recalibration():
    """Seul filet de sécurité anti-désynchro restant : si le compteur reste
    bloqué à 3 sans qu'un Vortex empowered ne parte pour le consommer
    pendant WARDEN_STACK_TIMEOUT_S, on force un recalibrage à 0."""
    from wardenbot.hooks import WARDEN_STACK_TIMEOUT_S, _set_warden_stacks
    import time

    state = State()
    now = time.monotonic()
    _set_warden_stacks(state, now - WARDEN_STACK_TIMEOUT_S - 1.0, 3)  # 3 stacks "posées" il y a longtemps
    assert state.flags.get("warden_stacks") == 3

    state.buffs["spirited_vortex"] = True
    state.buffs["spirited_strikes"] = True   # les deux up -> sans les 3 stacks, rien à rouvrir
    choice = meiko_pre_tick(state)
    assert choice is None, f"après timeout, le compteur doit être recalibré à 0 -> rien à forcer, reçu {choice!r}"
    assert state.flags.get("warden_stacks") == 0
    print("Compteur à 3 depuis trop longtemps -> recalibré à 0 automatiquement.")


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
        state=State(mode="st"),
        press_fn=lambda name: pressed_names.append(name),
        is_ready_fn=is_ready_fn_that_throws,
    )
    pressed = engine.tick()
    assert pressed == "filler", (
        f"le filler (priorité inférieure) doit partir malgré l'exception de détection sur 'detector', reçu {pressed!r}"
    )
    assert pressed_names == ["filler"]
    print("Exception de détection sur une ability -> le scan continue, le filler part quand même.")


if __name__ == "__main__":
    test_default_opens_when_buffs_missing()
    test_default_picks_whichever_buff_is_missing()
    test_three_stacks_forces_vortex_even_if_strikes_is_missing()
    test_full_sequence_matches_user_example()
    test_stack_counter_tracks_real_spend_combos_not_visual_badge()
    test_visual_debuff_detection_is_ignored_for_stack_counting()
    test_stack_timeout_forces_recalibration()
    test_detection_exception_on_one_ability_does_not_freeze_the_scan()
    print("\nTous les tests passent.")
