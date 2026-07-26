"""
test_helena.py — prouve, sans écran ni jeu, que le portage Helena (Season 3,
cf. config/helena.yaml + wardenbot/hooks.py::helena_pre_tick/helena_post_tick,
2026-07-18/19) se charge correctement, que la chaîne CDR Veteran of War fait
exactement ce qu'elle annonce (réductions partielles ET le reset complet de
Hold the Line), et que le front montant de shield_throw_proc reproduit le fix
"double-cast" déjà validé côté Tariq (TARIQ_PROC_BUFFS/MIN_ABSENT_TICKS).

Lance avec : python test_helena.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import time

import numpy as np

from wardenbot.bar_reader import read_fill_pct
from wardenbot.engine import CombatState, evaluate_condition, load_abilities
from wardenbot.freshness import DetectionMeta
from wardenbot.hooks import HELENA_CDR_MS, HELENA_MIN_ABSENT_TICKS, helena_post_tick, helena_pre_tick


def _fresh(state: CombatState) -> CombatState:
    """Marque `state` comme ayant une lecture helena_bars fraîche -- requis
    depuis l'ajout du gate de fraîcheur (2026-07-19, cf. hooks.py::
    helena_pre_tick) pour que les tests de front montant continuent
    d'exercer la vraie logique plutôt que de se faire bloquer par le gate
    lui-même (même helper que test_tariq.py::_fresh)."""
    state.detection_meta["helena_bars"] = DetectionMeta(created_at=time.monotonic())
    return state
from wardenbot.state_update import HELENA_TOUGHNESS_BAR_FILL_BGR


def test_helena_yaml_loads_and_matches_the_documented_auto_rotation():
    """config/helena.yaml doit charger sans lever (Ability est un dataclass
    strict -- toute clé inconnue ferait planter Ability(**a)) et contenir
    exactement les 9 abilities auto-castées documentées en tête de fichier --
    Shield Throw + son proc AJOUTÉS le 2026-07-19 (demande explicite), Charge
    toujours manuel (non demandé)."""
    abilities, meta = load_abilities("config/helena.yaml")
    names = {a.name for a in abilities}
    assert names == {
        "shields_up", "sweeping_strike", "shockwave", "shield_slam",
        "shield_throw_proc", "shield_throw", "hold_the_line",
        "power_strike", "measured_strike",
    }
    assert "charge" not in names
    assert meta.get("gcd_ms") == 1500
    print("config/helena.yaml charge avec exactement les 9 abilities auto attendues.")


def test_real_cooldowns_added_2026_07_19():
    """hold_the_line/shockwave n'avaient AUCUN cooldown_ms avant le
    2026-07-19 (seule la détection template les gatait -> spam observé en
    jeu). Vérifie les vraies valeurs mesurées par l'utilisateur."""
    abilities, _ = load_abilities("config/helena.yaml")
    by_name = {a.name: a for a in abilities}
    assert by_name["hold_the_line"].cooldown_ms == 30000
    assert by_name["shockwave"].cooldown_ms == 29870
    assert by_name["sweeping_strike"].cooldown_ms == 30000
    assert by_name["shield_throw"].cooldown_ms == 21000
    print("hold_the_line=30000ms, shockwave=29870ms, sweeping_strike=30000ms, shield_throw=21000ms confirmés.")


def test_shield_throw_proc_skips_detection_and_gates_on_the_ready_flag():
    """Même schéma que hammer_storm_proc/skull_crusher_proc (Tariq) :
    skip_detection:true, cooldown_ms placeholder, la VRAIE garde est le flag
    de front montant, pas l'icône de la barre de sorts."""
    abilities, _ = load_abilities("config/helena.yaml")
    proc = next(a for a in abilities if a.name == "shield_throw_proc")
    assert proc.skip_detection is True
    assert proc.condition == "hooks:shield_throw_proc_ready"
    print("shield_throw_proc : skip_detection + condition hooks:shield_throw_proc_ready confirmés.")


def test_shields_up_condition_reads_toughness_not_mana():
    """Le token de la spec fournie (`not mana_above:40`) n'existe pas dans le
    moteur -- corrigé en `not resource_above:toughness:40` (cf.
    engine.py::_eval_token, 2026-07-18 ; token générique resource_above
    depuis la généricité du moteur, 2026-07-26). Vérifie la vraie condition
    telle qu'elle est chargée depuis le YAML, pas une réécriture à la main
    dans ce test."""
    abilities, _ = load_abilities("config/helena.yaml")
    shields_up = next(a for a in abilities if a.name == "shields_up")
    assert shields_up.condition == "not resource_above:toughness:40"

    state = CombatState()
    state.resources["toughness"] = 0.30
    assert evaluate_condition(shields_up.condition, state) is True  # 30% < 40%
    state.resources["toughness"] = 0.55
    assert evaluate_condition(shields_up.condition, state) is False  # 55% >= 40%
    print("shields_up.condition lit bien resources[\"toughness\"], pas resources[\"mana\"].")


def test_toughness_defaults_to_zero_when_never_detected():
    """Avant que make_helena_state_update_fn tourne au moins un tick (ou si le
    profil n'a pas la région toughness_bar), la ressource vaut 0.0 par
    défaut -- donc `not resource_above:toughness:40` reste VRAI en
    permanence et Shields Up part à chaque fois qu'il est off-cooldown.
    Comportement ATTENDU pré-détection, pas un bug."""
    state = CombatState()
    assert evaluate_condition("not resource_above:toughness:40", state) is True
    print("Sans détection, resource_above:toughness reste à 0 -> Shields Up jamais gaté (comportement attendu).")


def test_toughness_bar_reader_reads_full_bar_despite_threshold_tick_marks():
    """La barre de Toughness a 3 graduations de seuil (5/15/25/35% DR) de
    3-4px chacune qui NE MATCHENT PAS la couleur de remplissage -- max_gap=6
    doit les franchir sans tronquer la lecture. Frame synthétique construite
    avec la vraie géométrie mesurée (228px large, graduations à x=59-62/
    117-120/175-177, cf. state_update.py::HELENA_TOUGHNESS_BAR_FILL_BGR)."""
    h, w = 11, 228
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = HELENA_TOUGHNESS_BAR_FILL_BGR
    for tick in [59, 60, 61, 62, 117, 118, 119, 120, 175, 176, 177]:
        frame[:, tick] = (255, 255, 187)

    pct = read_fill_pct(frame, HELENA_TOUGHNESS_BAR_FILL_BGR, tolerance=30, direction="ltr", max_gap=6)
    assert pct > 0.99, f"attendu ~1.0 (barre pleine malgré les graduations), lu {pct}"
    print(f"Barre pleine + 3 graduations -> lue à {pct:.3f} (pas tronquée à la 1re graduation).")


def test_toughness_bar_reader_tracks_a_partial_fill():
    """Même frame synthétique, mais remplie seulement jusqu'à la moitié --
    prouve que max_gap=6 n'est pas SI généreux qu'il masquerait un vrai
    pourcentage bas (risque symétrique au test ci-dessus)."""
    h, w = 11, 228
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = HELENA_TOUGHNESS_BAR_FILL_BGR
    frame[:, 114:] = (20, 20, 20)  # fond vide au-delà de 50%

    pct = read_fill_pct(frame, HELENA_TOUGHNESS_BAR_FILL_BGR, tolerance=30, direction="ltr", max_gap=6)
    assert 0.45 < pct < 0.55, f"attendu ~0.5, lu {pct}"
    print(f"Barre à moitié remplie -> lue à {pct:.3f}.")


def test_cdr_reduces_an_already_pending_cooldown():
    """Measured Strike (pressed) doit avancer le cooldown_until de Shield Slam
    de 1.5s -- pas le poser, le RACCOURCIR (cf. docstring helena_post_tick)."""
    state = CombatState()
    now = time.monotonic()
    state.cooldown_until["shield_slam"] = now + 5.0
    helena_post_tick(state, "measured_strike")
    assert abs(state.cooldown_until["shield_slam"] - (now + 3.5)) < 1e-6
    print("measured_strike raccourcit bien shield_slam de 1.5s.")


def test_cdr_measured_strike_and_power_strike_also_reduce_shield_throw():
    """Ajouté le 2026-07-19 : Shield Throw est maintenant auto-castée, donc sa
    CDR (Measured Strike/Power Strike -> -1.5s) devient observable."""
    state = CombatState()
    now = time.monotonic()
    state.cooldown_until["shield_throw"] = now + 10.0
    helena_post_tick(state, "measured_strike")
    assert abs(state.cooldown_until["shield_throw"] - (now + 8.5)) < 1e-6

    state2 = CombatState()
    state2.cooldown_until["shield_throw"] = now + 10.0
    helena_post_tick(state2, "power_strike")
    assert abs(state2.cooldown_until["shield_throw"] - (now + 8.5)) < 1e-6
    print("measured_strike/power_strike raccourcissent bien shield_throw de 1.5s chacun.")


def test_cdr_chain_shield_slam_into_shockwave():
    state = CombatState()
    now = time.monotonic()
    state.cooldown_until["shockwave"] = now + 10.0
    helena_post_tick(state, "shield_slam")
    assert abs(state.cooldown_until["shockwave"] - (now + 7.0)) < 1e-6
    print("shield_slam raccourcit bien shockwave de 3.0s.")


def test_cdr_shield_throw_and_its_proc_also_reduce_shockwave():
    """Shield Throw (base ET proc) accorde -3s à Shockwave, comme Shield
    Slam -- shield_throw_proc reste un cast de Shield Throw mécaniquement,
    même CDR (cf. commentaire HELENA_CDR_MS)."""
    now = time.monotonic()

    state = CombatState()
    state.cooldown_until["shockwave"] = now + 10.0
    helena_post_tick(state, "shield_throw")
    assert abs(state.cooldown_until["shockwave"] - (now + 7.0)) < 1e-6

    state2 = CombatState()
    state2.cooldown_until["shockwave"] = now + 10.0
    helena_post_tick(state2, "shield_throw_proc")
    assert abs(state2.cooldown_until["shockwave"] - (now + 7.0)) < 1e-6
    print("shield_throw et shield_throw_proc raccourcissent bien shockwave de 3.0s chacun.")


def test_cdr_chain_shockwave_into_shields_up():
    state = CombatState()
    now = time.monotonic()
    state.cooldown_until["shields_up"] = now + 20.0
    helena_post_tick(state, "shockwave")
    assert abs(state.cooldown_until["shields_up"] - (now + 14.0)) < 1e-6
    print("shockwave raccourcit bien shields_up de 6.0s.")


def test_hold_the_line_fully_resets_shield_slam_not_a_partial_reduction():
    """'Resets Shield Slam CD' (spec d'origine) est un RESET COMPLET, pas une
    réduction en ms -- Hold the Line est délibérément ABSENT de HELENA_CDR_MS
    (cf. sa docstring), traité comme cas spécial dans helena_post_tick."""
    assert "hold_the_line" not in HELENA_CDR_MS
    state = CombatState()
    state.cooldown_until["shield_slam"] = time.monotonic() + 999.0
    helena_post_tick(state, "hold_the_line")
    assert "shield_slam" not in state.cooldown_until
    print("hold_the_line efface bien shield_slam de cooldown_until (reset complet).")


def test_hold_the_line_reset_is_a_no_op_without_a_pending_shield_slam_cooldown():
    state = CombatState()
    assert "shield_slam" not in state.cooldown_until
    helena_post_tick(state, "hold_the_line")  # ne doit pas lever
    assert "shield_slam" not in state.cooldown_until
    print("hold_the_line sans cooldown_slam préexistant : no-op propre.")


def test_cdr_never_creates_a_cooldown_out_of_nothing():
    """shield_slam n'a jamais été casté -> pas d'entrée dans cooldown_until.
    power_strike ne doit RIEN y écrire (pas de cooldown fictif inventé)."""
    state = CombatState()
    assert "shield_slam" not in state.cooldown_until
    helena_post_tick(state, "power_strike")
    assert "shield_slam" not in state.cooldown_until
    print("power_strike sans cooldown_slam préexistant : aucune entrée créée.")


def test_cdr_ignores_presses_with_no_table_entry():
    """shields_up et sweeping_strike ne sont dans aucune relation CDR (comme
    source) -- doit être un no-op silencieux, pas une KeyError."""
    state = CombatState()
    state.cooldown_until["shield_slam"] = time.monotonic() + 5.0
    before = dict(state.cooldown_until)
    helena_post_tick(state, "shields_up")
    helena_post_tick(state, "sweeping_strike")
    assert state.cooldown_until == before
    print("Presses hors-table : no-op, aucune KeyError.")


def test_cdr_table_only_covers_abilities_the_bot_can_actually_press():
    """Charge accorde aussi du CDR dans le guide d'origine mais reste MANUEL
    (non demandé) -- ne doit apparaître ni comme clé (pressed) ni comme
    cible dans HELENA_CDR_MS."""
    abilities, _ = load_abilities("config/helena.yaml")
    auto_cast_names = {a.name for a in abilities}
    for pressed, reductions in HELENA_CDR_MS.items():
        assert pressed in auto_cast_names, f"{pressed} n'est pas auto-castée par config/helena.yaml"
        for target in reductions:
            assert target in auto_cast_names, f"{target} n'est pas auto-castée par config/helena.yaml"
    print("HELENA_CDR_MS ne référence que des abilities réellement auto-castées.")


# =============================================================================
# helena_pre_tick -- front montant de shield_throw_proc, même schéma que
# TARIQ_PROC_BUFFS/MIN_ABSENT_TICKS (bug double-cast du 2026-07-12, hooks.py).
# =============================================================================

def test_pre_tick_never_proposes_an_intent():
    """Bookkeeping pur -- doit toujours retourner [] (jamais presser lui-même,
    cf. docstring helena_pre_tick)."""
    state = _fresh(CombatState())
    state.buffs["shield_throw_proc"] = True
    assert helena_pre_tick(state) == []
    print("helena_pre_tick retourne toujours [] (bookkeeping pur).")


def test_rising_edge_arms_ready_on_absent_to_present_transition():
    state = _fresh(CombatState())
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    assert state.hooks.get("shield_throw_proc_ready", False) is False

    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True
    print("Front montant absent -> présent : shield_throw_proc_ready s'arme.")


def test_ready_does_not_rearm_while_buff_stays_active():
    """Simule le bug historique de Tariq : si le buff reste lu 'actif'
    plusieurs ticks d'affilée, un SEUL front montant doit s'armer -- pas un
    par tick."""
    state = _fresh(CombatState())
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True

    # consommation manuelle simulée (comme le ferait helena_post_tick)
    state.hooks["shield_throw_proc_ready"] = False
    # le buff reste "actif" plusieurs ticks de plus -- ne doit PAS réarmer
    for _ in range(5):
        helena_pre_tick(state)
        assert state.hooks["shield_throw_proc_ready"] is False
    print("Buff qui reste actif plusieurs ticks : un seul front montant, pas de réarmement en boucle.")


def test_single_frame_noise_does_not_rearm_ready_flag():
    """Même garde-fou que côté Tariq (2026-07-12) : un clignotement d'UNE
    frame (présent -> absent -> présent, sans MIN_ABSENT_TICKS d'absence
    réelle) ne doit PAS compter comme un nouveau front montant."""
    state = _fresh(CombatState())
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True
    state.hooks["shield_throw_proc_ready"] = False  # consommé

    # clignotement d'1 seule frame absente (bruit), PAS MIN_ABSENT_TICKS
    state.buffs["shield_throw_proc"] = False
    helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is False, "1 frame de bruit n'a pas dû réarmer le flag"
    print("Bruit d'1 frame (present->absent->present) : ne réarme pas ready, comme prévu.")


def test_legitimate_reproc_after_enough_absent_ticks_rearms_ready():
    """Contre-partie du test précédent : une VRAIE absence de
    MIN_ABSENT_TICKS+ doit permettre un nouveau front montant légitime."""
    state = _fresh(CombatState())
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True
    state.hooks["shield_throw_proc_ready"] = False  # consommé

    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True
    print("Absence réelle >= MIN_ABSENT_TICKS puis reproc : nouveau front montant légitime.")


def test_post_tick_consumes_the_proc_ready_flag_on_real_press():
    state = CombatState()
    state.hooks["shield_throw_proc_ready"] = True
    helena_post_tick(state, "shield_throw_proc")
    assert state.hooks["shield_throw_proc_ready"] is False
    print("helena_post_tick consomme bien shield_throw_proc_ready sur la vraie presse.")


# =============================================================================
# Fraîcheur (2026-07-19, ajoutée en même temps que le passage en thread de
# make_helena_state_update_fn -- cf. son commentaire de tête, state_update.py).
# =============================================================================

def test_expired_helena_detection_blocks_shield_throw_proc_ready():
    """Sans lecture helena_bars fraîche (jamais alimentée, ou périmée), un
    front montant absent -> présent NE DOIT PAS armer shield_throw_proc_ready
    -- on ne force pas une presse sur une donnée qu'on ne peut plus garantir
    à jour (même principe que l'audit 2026-07-17 côté Meiko/Tariq)."""
    state = CombatState()  # PAS _fresh() -- c'est exactement ce qu'on teste
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        state.buffs["shield_throw_proc"] = False
        helena_pre_tick(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks.get("shield_throw_proc_ready", False) is False, (
        "shield_throw_proc_ready ne doit jamais s'armer sans detection_meta['helena_bars'] fraîche"
    )
    print("Sans détection fraîche, shield_throw_proc_ready ne s'arme jamais (comportement attendu).")


def test_rising_edge_bookkeeping_keeps_running_even_when_detection_is_stale():
    """Le gate de fraîcheur bloque UNIQUEMENT l'armement de ready -- le suivi
    absent_ticks/prev doit continuer à tourner même périmé, sinon une période
    périmée fausserait la détection de front montant une fois la détection
    revenue (même raison que TARIQ_PROC_BUFFS, cf. hooks.py)."""
    state = CombatState()  # jamais fraîchi
    state.buffs["shield_throw_proc"] = False
    for _ in range(HELENA_MIN_ABSENT_TICKS + 1):
        helena_pre_tick(state)
    assert state.hooks["_shield_throw_proc_absent_ticks"] >= HELENA_MIN_ABSENT_TICKS

    # la detection redevient fraiche -- le front montant doit pouvoir s'armer
    # immediatement, PAS attendre encore MIN_ABSENT_TICKS de plus (preuve que
    # le compteur n'a pas ete gele/reinitialise pendant la periode perimee).
    _fresh(state)
    state.buffs["shield_throw_proc"] = True
    helena_pre_tick(state)
    assert state.hooks["shield_throw_proc_ready"] is True
    print("absent_ticks continue de tourner pendant une période périmée -- pas de retard artificiel au retour de la fraîcheur.")


if __name__ == "__main__":
    test_helena_yaml_loads_and_matches_the_documented_auto_rotation()
    test_real_cooldowns_added_2026_07_19()
    test_shield_throw_proc_skips_detection_and_gates_on_the_ready_flag()
    test_shields_up_condition_reads_toughness_not_mana()
    test_toughness_defaults_to_zero_when_never_detected()
    test_toughness_bar_reader_reads_full_bar_despite_threshold_tick_marks()
    test_toughness_bar_reader_tracks_a_partial_fill()
    test_cdr_reduces_an_already_pending_cooldown()
    test_cdr_measured_strike_and_power_strike_also_reduce_shield_throw()
    test_cdr_chain_shield_slam_into_shockwave()
    test_cdr_shield_throw_and_its_proc_also_reduce_shockwave()
    test_cdr_chain_shockwave_into_shields_up()
    test_hold_the_line_fully_resets_shield_slam_not_a_partial_reduction()
    test_hold_the_line_reset_is_a_no_op_without_a_pending_shield_slam_cooldown()
    test_cdr_never_creates_a_cooldown_out_of_nothing()
    test_cdr_ignores_presses_with_no_table_entry()
    test_cdr_table_only_covers_abilities_the_bot_can_actually_press()
    test_pre_tick_never_proposes_an_intent()
    test_rising_edge_arms_ready_on_absent_to_present_transition()
    test_ready_does_not_rearm_while_buff_stays_active()
    test_single_frame_noise_does_not_rearm_ready_flag()
    test_legitimate_reproc_after_enough_absent_ticks_rearms_ready()
    test_post_tick_consumes_the_proc_ready_flag_on_real_press()
    test_expired_helena_detection_blocks_shield_throw_proc_ready()
    test_rising_edge_bookkeeping_keeps_running_even_when_detection_is_stale()
    print("\nTous les tests Helena sont passés.")
