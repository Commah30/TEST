"""
test_vigour.py — prouve, sans écran ni jeu, que vigour_pre_tick suit la vraie
rotation du build Legendary Cloak (2026-07-16, cf. method.gg/fellowship/
heroes/vigour + playstyle-and-rotation, complétée par les cooldowns/coûts et
tooltips fournis par l'utilisateur), RÉVISÉE le 2026-07-16 (2e passe, 1er
retour de test en jeu -- cf. hooks.py::vigour_pre_tick, commentaire de tête)
pour piloter build/soin/dégât strictement par la vie réelle du groupe plutôt
que par un timer d'entretien ou un seuil "quasi toujours vrai" (<90%) :
Greater Heal (critique) > Circle of Light (urgence groupe) > Remove Magic
(cleanse) > Dawnbreaker Orb/Rune Of Renewal (quelqu'un <= 70%) > Soulbrand
(dégât, personne <= 70% mais pas tout le monde plein) > Dawnflare (build,
tout le monde plein). Prouve aussi que la donnée de vie périmée (freshness)
bloque toute décision plutôt que d'agir sur une valeur qu'on ne peut plus
garantir fraîche.

Lance avec : python test_vigour.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import time

from wardenbot.engine import CombatState
from wardenbot.freshness import DetectionMeta
from wardenbot.hooks import (
    vigour_post_tick, vigour_post_tick_early_game, vigour_pre_tick, vigour_pre_tick_early_game,
)


def _state_with_party(
    party: dict,
    orbs: int = 0,
    age_ms: float = 0.0,
    mana: "float | None" = None,
    early_game_rune_applied_ago_s: "float | None" = None,
) -> CombatState:
    """age_ms : âge voulu de la lecture au moment où vigour_pre_tick
    l'évaluera. vigour_pre_tick calcule son propre `now` en interne (pas
    d'injection possible, contrairement à DetectionMeta.age_ms) -- on ancre
    donc created_at sur le VRAI time.monotonic() actuel, décalé de age_ms
    (même principe que le reste du projet : jamais time.time() pour un
    calcul de durée).

    mana : absent par défaut (le hook lit resources.get("mana", 1.0) --
    plein par défaut, cf. hooks.py).

    early_game_rune_applied_ago_s : âge en secondes depuis la dernière presse
    CONFIRMÉE de Rune Of Renewal sur le tank (vigour_pre_tick_early_game) --
    pratique pour simuler "déjà rafraîchi récemment" sans dépendre de
    vigour_post_tick_early_game."""
    state = CombatState()
    state.party = party
    state.resources["orbs"] = orbs
    if mana is not None:
        state.resources["mana"] = mana
    state.detection_meta["vigour_party_bars"] = DetectionMeta(created_at=time.monotonic() - age_ms / 1000.0)
    if early_game_rune_applied_ago_s is not None:
        state.hooks["early_game_rune_of_renewal_tank_applied_at"] = time.monotonic() - early_game_rune_applied_ago_s
    return state


def test_no_party_data_at_all_proposes_nothing():
    state = CombatState()   # jamais lu -- detection_meta absent
    assert vigour_pre_tick(state) == []
    print("Aucune donnée de groupe jamais lue -- vigour_pre_tick ne propose rien plutôt que de deviner.")


def test_expired_party_data_proposes_nothing_even_if_someone_looks_critical():
    """Coeur du branchement freshness (cf. discussion architecture) : même un
    allié apparemment à 10% HP ne doit PAS déclencher de soin si la lecture
    est trop vieille pour être fiable -- agir sur une valeur périmée serait
    pire que ne rien faire."""
    state = _state_with_party(
        {1: {"present": True, "hp_pct": 0.10, "cleanse_needed": False, "hot_active": False}},
        age_ms=10_000.0,   # très largement au-delà de VIGOUR_PARTY_FRESHNESS_POLICY.stale_ms
    )
    assert vigour_pre_tick(state) == []
    print("Donnée de groupe EXPIRED -- aucune décision, même avec un allié apparemment critique.")


def test_critical_ally_triggers_greater_heal_over_everything_else():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.20, "cleanse_needed": True, "hot_active": False},   # critique ET cleanse
        2: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": False},
    })
    intents = vigour_pre_tick(state)
    assert len(intents) == 1
    assert intents[0].ability == "greater_heal"
    assert intents[0].target == "player_1"
    assert intents[0].weight == 90
    print("Allié sous le seuil critique -- Greater Heal prioritaire, même si un cleanse est aussi en attente.")


def test_picks_the_lowest_hp_ally_among_multiple_critical():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.25, "cleanse_needed": False, "hot_active": False},
        2: {"present": True, "hp_pct": 0.12, "cleanse_needed": False, "hot_active": False},
        3: {"present": True, "hp_pct": 0.28, "cleanse_needed": False, "hot_active": False},
    })
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "greater_heal"
    assert intents[0].target == "player_2", f"attendu l'allié le plus bas (12%), reçu {intents[0].target!r}"
    print("Plusieurs alliés critiques -- Greater Heal cible bien le plus bas en vie.")


def test_group_damage_triggers_circle_of_light_targeted_on_tank():
    """Circle of Light se lance normalement en mouseover ou au clic (cf.
    hooks.py, hypothèse de contournement) -- on vise le tank (slot 1) comme
    point d'ancrage plutôt que de ne pas cibler du tout."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.65, "cleanse_needed": False, "hot_active": False},   # tank
        2: {"present": True, "hp_pct": 0.60, "cleanse_needed": False, "hot_active": False},
        3: {"present": True, "hp_pct": 0.55, "cleanse_needed": False, "hot_active": False},
        4: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    })
    intents = vigour_pre_tick(state)
    assert len(intents) == 1
    assert intents[0].ability == "circle_of_light"
    assert intents[0].target == "player_1", "doit cibler le tank comme point d'ancrage, cf. hypothèse de ciblage"
    print("3+ alliés sous 70% HP -- Circle of Light proposé, ciblé sur le tank (contournement du ciblage).")


def test_group_damage_does_not_trigger_circle_of_light_if_tank_is_absent():
    """Sans tank vivant, pas de point d'ancrage fiable pour le contournement
    de ciblage -- on ne propose pas Circle of Light plutôt que de cibler au
    hasard."""
    state = _state_with_party({
        2: {"present": True, "hp_pct": 0.60, "cleanse_needed": False, "hot_active": False},
        3: {"present": True, "hp_pct": 0.55, "cleanse_needed": False, "hot_active": False},
        4: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": False},
    })
    intents = vigour_pre_tick(state)
    assert not intents or intents[0].ability != "circle_of_light"
    print("Groupe en danger mais tank absent -- pas de Circle of Light sans point d'ancrage fiable.")


def test_remove_magic_wins_over_hurt_handling_but_not_over_critical_or_group():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.90, "cleanse_needed": False, "hot_active": True},   # tank, plein -- ne doit rien déclencher
        2: {"present": True, "hp_pct": 0.50, "cleanse_needed": True, "hot_active": True},     # sous 70% ET cleanse
    }, orbs=3)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "remove_magic"
    assert intents[0].target == "player_2"
    print("Pas de critique/dégâts de groupe -- Remove Magic prioritaire, même si la cible est aussi sous 70%.")


def test_dawnbreaker_orb_heals_lowest_hp_ally_when_someone_is_hurt_and_affordable():
    """2026-07-16 (2e passe) : QUICONQUE (pas seulement le tank) sous 70% se
    fait soigner -- Dawnbreaker Orb (2 orbes, le plus gros complément) en 1er
    choix quand affordable, cible le plus bas en vie parmi ceux sous 70%
    (pas le tank par défaut)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},   # tank, plein
        2: {"present": True, "hp_pct": 0.65, "cleanse_needed": False, "hot_active": False},   # sous 70%
        3: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": False},   # encore plus bas
    }, orbs=3)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "dawnbreaker_orb"
    assert intents[0].target == "player_3", "doit cibler le plus bas en vie parmi ceux sous 70%, pas le tank"
    print("Quelqu'un sous 70% et 2+ orbes dispo -- Dawnbreaker Orb cible le plus bas en vie (plus le tank par défaut).")


def test_rune_of_renewal_used_when_hurt_but_not_enough_orbs_for_dawnbreaker():
    """1 seul orbe dispo (pas assez pour Dawnbreaker Orb, 2 orbes) --
    retombe sur Rune Of Renewal, moins cher (1 orbe)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.65, "cleanse_needed": False, "hot_active": False},
    }, orbs=1)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "rune_of_renewal"
    assert intents[0].target == "player_1"
    print("Sous 70% mais seulement 1 orbe -- Rune Of Renewal (1 orbe) plutôt que Dawnbreaker Orb (2 orbes).")


def test_dawnbreaker_orb_skipped_when_on_cooldown_falls_back_to_rune_of_renewal():
    """Dawnbreaker Orb vérifie désormais son propre cooldown (cf. hooks.py,
    commentaire de tête -- un hook_intent n'est PAS filtré par cooldown_until
    côté moteur) : en cooldown malgré 2+ orbes dispo, retombe sur Rune Of
    Renewal plutôt que de presser une touche qui ne partira pas."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.65, "cleanse_needed": False, "hot_active": False},
    }, orbs=3)
    state.cooldown_until["dawnbreaker_orb"] = time.monotonic() + 5.0
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "rune_of_renewal", "Dawnbreaker Orb en cooldown -- ne doit pas être pressé pour rien"
    print("Dawnbreaker Orb en cooldown -- retombe sur Rune Of Renewal plutôt que de brûler un GCD pour rien.")


def test_no_heal_at_all_falls_back_to_dawnflare_when_no_orbs():
    """Sans le moindre orbe, ni Dawnbreaker Orb ni Rune Of Renewal ne peuvent
    se déclencher -- tombe sur Dawnflare pour d'abord générer l'orbe qui
    manque, plutôt que de ne rien faire."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": True},
    }, orbs=0)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "dawnflare", f"attendu le filler faute d'orbe, reçu {intents[0].ability!r}"
    print("Allié sous 70% mais 0 orbe -- ni Dawnbreaker Orb ni Rune Of Renewal, retombe sur Dawnflare (build).")


def test_post_tick_is_a_no_op():
    """2026-07-16 (2e passe) : plus de timer à horodater -- vigour_post_tick
    ne doit strictement rien modifier sur state, quel que soit le sort
    pressé (cf. hooks.py, même convention que meiko_post_tick)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": False},
    }, orbs=3)
    hooks_before = dict(state.hooks)
    vigour_post_tick(state, "rune_of_renewal")
    vigour_post_tick(state, "dawnbreaker_orb")
    vigour_post_tick(state, "greater_heal")
    assert state.hooks == hooks_before, "vigour_post_tick ne doit plus rien écrire dans state.hooks"
    print("vigour_post_tick est un no-op -- plus de timer d'entretien à confirmer (spam supprimé, pas réajusté).")


def test_soulbrand_fires_when_someone_took_damage_but_nobody_is_at_or_below_70():
    """2026-07-16 (2e passe, retour de test en jeu) : personne sous 70%, mais
    pas tout le monde plein -- une simple égratignure ne justifie pas un
    orbe de soin, Soulbrand (dégât pur) prend le relai plutôt que de builder
    pour rien avec Dawnflare."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.85, "cleanse_needed": False, "hot_active": False},
        2: {"present": True, "hp_pct": 1.0, "cleanse_needed": False, "hot_active": False},
    }, orbs=3)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "soulbrand"
    assert intents[0].target is None, "Soulbrand cible un ennemi, jamais un allié"
    print("Personne sous 70% mais pas tout le monde plein -- Soulbrand (dps), pas de dépense de soin pour rien.")


def test_soulbrand_falls_back_to_dawnflare_when_no_orb_available():
    """Soulbrand coûte 1 orbe -- sans le moindre orbe, retombe sur Dawnflare
    (toujours affordable, gratuit) plutôt que de ne rien presser."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.85, "cleanse_needed": False, "hot_active": False},
    }, orbs=0)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "dawnflare"
    print("Pas full HP mais 0 orbe -- Soulbrand non affordable, retombe sur Dawnflare.")


def test_dawnflare_fires_when_everyone_is_topped_off():
    """2026-07-16 (2e passe) : tout le monde à (quasi) 100% -- build avec
    Dawnflare, MÊME si les orbes sont déjà au plafond (Dawnflare fait aussi
    des dégâts, jamais "gaspillé" -- l'ancien plafond d'orbe comme critère de
    décision a été retiré, cf. hooks.py commentaire de tête)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 1.0, "cleanse_needed": False, "hot_active": False},
        2: {"present": True, "hp_pct": 0.98, "cleanse_needed": False, "hot_active": False},   # dans la tolérance de bruit
    }, orbs=6)   # orbes déjà au plafond -- ne doit PLUS influencer cette décision
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "dawnflare"
    assert intents[0].target is None, "Dawnflare cible un ennemi, jamais un allié"
    print("Tout le monde (quasi) plein, même orbes au plafond -- Dawnflare (build), le plafond n'est plus un critère.")


def test_absent_slot_is_never_considered_for_any_heal():
    """Un slot absent ne doit jamais être ciblé par un soin (confondu avec un
    allié mort à 0%), ni compter contre "tout le monde plein" -- avec le
    seul allié présent au max, Dawnflare doit sortir (build), jamais une
    Intent avec target="player_1" (le slot absent)."""
    state = _state_with_party({
        1: {"present": False, "hp_pct": None, "cleanse_needed": False, "hot_active": False},
        2: {"present": True, "hp_pct": 1.0, "cleanse_needed": False, "hot_active": False},
    }, orbs=6)
    intents = vigour_pre_tick(state)
    assert intents[0].ability == "dawnflare"
    assert intents[0].target != "player_1", "le slot absent ne doit jamais être ciblé"
    print("Slot absent -- jamais confondu avec un allié mort à 0%, seul l'allié présent (plein) compte.")


def test_gcd_gate_blocks_every_decision_like_meiko_and_tariq():
    """Même gate GCD explicite que meiko_pre_tick/tariq_pre_tick (cf. CLAUDE.md
    bug #1) -- vigour_pre_tick ne doit rien proposer avant la fin du GCD."""
    state = _state_with_party({1: {"present": True, "hp_pct": 0.10, "cleanse_needed": False, "hot_active": False}})
    state.gcd_until = time.monotonic() + 10.0
    assert vigour_pre_tick(state) == []
    print("GCD actif -- vigour_pre_tick ne propose rien, même avec un allié critique.")


# =============================================================================
# VIGOUR EARLY GAME (2026-07-16) -- vigour_pre_tick_early_game, la rotation
# PRÉ-4e-ligne fournie par l'utilisateur (cf. hooks.py, commentaire de tête
# juste avant la fonction) : critique > cleanse > uptime Rune Of Renewal sur
# le tank > urgence tank (<70%) > génération (Dawnbreaker Orb puis Radiant
# Blast) > dépense au plafond (>=5 orbes, Luminous Barrier ou Soulbrand) >
# AoE groupe (Circle of Light) > filler (Dawnflare).
# =============================================================================

def test_early_game_no_party_data_proposes_nothing():
    state = CombatState()
    assert vigour_pre_tick_early_game(state) == []
    print("[early game] Aucune donnée de groupe -- ne propose rien plutôt que de deviner.")


def test_early_game_critical_ally_beats_buff_uptime():
    """L'item 0 (ajout, cf. commentaire de tête) doit gagner même si le
    buff d'écho du tank est dû depuis longtemps -- un allié mourant passe
    avant l'entretien économique."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},   # tank, plein
        2: {"present": True, "hp_pct": 0.15, "cleanse_needed": False, "hot_active": False},    # critique
    }, orbs=3)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "greater_heal"
    assert intents[0].target == "player_2"
    print("[early game] Allié critique -- Greater Heal gagne même sur l'uptime du buff (jamais redemandé, ajout de sécurité).")


def test_early_game_cleanse_beats_buff_uptime_but_not_critical():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": True, "hot_active": False},   # tank, cleanse
    }, orbs=3)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "remove_magic"
    assert intents[0].target == "player_1"
    print("[early game] Cleanse -- prioritaire sur l'uptime du buff (ajout, absent de la liste fournie).")


def test_early_game_rune_of_renewal_uptime_on_tank_when_due():
    """Item 1 (guide, 'priorité absolue') : jamais rafraîchi (absent du
    dict) -- doit se déclencher dès qu'un orbe est dispo."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=1)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "rune_of_renewal"
    assert intents[0].target == "player_1"
    print("[early game] Buff jamais posé sur le tank -- Rune Of Renewal, priorité absolue du guide.")


def test_early_game_rune_of_renewal_skipped_when_recently_applied():
    """Rafraîchi il y a seulement 5s (fenêtre de 25s) -- ne doit PAS se
    redéclencher, retombe sur la génération (Radiant Blast, faute d'orbe
    pour Dawnbreaker Orb)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=0, mana=1.0, early_game_rune_applied_ago_s=5.0)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability != "rune_of_renewal"
    print("[early game] Buff récemment rafraîchi -- pas de re-déclenchement, laisse la place à la suite.")


def test_early_game_tank_urgency_greater_heal_below_70():
    """Item 2 (guide) : tank < 70%, buff pas dû -- Greater Heal routine sur
    le tank spécifiquement."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.65, "cleanse_needed": False, "hot_active": False},
    }, orbs=0, early_game_rune_applied_ago_s=5.0)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "greater_heal"
    assert intents[0].target == "player_1"
    print("[early game] Tank sous 70%, buff pas dû -- Greater Heal routine sur le tank.")


def test_early_game_generation_prefers_dawnbreaker_orb_over_radiant_blast():
    """Item 3 (guide) : 2+ orbes dispo, hors cooldown -- Dawnbreaker Orb
    d'abord (cible le plus bas en vie, même sans urgence -- le but est
    l'orbe généré)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
        2: {"present": True, "hp_pct": 0.85, "cleanse_needed": False, "hot_active": False},
    }, orbs=2, early_game_rune_applied_ago_s=5.0)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "dawnbreaker_orb"
    assert intents[0].target == "player_2", "doit cibler le plus bas en vie, même sans urgence"
    print("[early game] 2+ orbes, hors cooldown -- Dawnbreaker Orb (génération) en 1er choix.")


def test_early_game_generation_falls_back_to_radiant_blast_when_dawnbreaker_on_cooldown():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=2, mana=1.0, early_game_rune_applied_ago_s=5.0)
    state.cooldown_until["dawnbreaker_orb"] = time.monotonic() + 5.0
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "radiant_blast"
    assert intents[0].target is None, "Radiant Blast cible un ennemi, jamais un allié"
    print("[early game] Dawnbreaker Orb en cooldown -- retombe sur Radiant Blast (mana suffisant).")


def test_early_game_radiant_blast_skipped_when_mana_too_low():
    """Radiant Blast coûte du mana (confirmé par l'utilisateur, 2026-07-16)
    -- sous le plancher de sécurité, ne doit PAS se déclencher, tombe
    jusqu'au filler Dawnflare (rien d'autre dû)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=0, mana=0.10, early_game_rune_applied_ago_s=5.0)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "dawnflare", f"attendu le filler, reçu {intents[0].ability!r}"
    print("[early game] Mana sous le plancher -- Radiant Blast sauté, retombe sur Dawnflare.")


def test_early_game_rune_spend_luminous_barrier_when_someone_hurt_and_cap_reached():
    """Item 4 (guide, règle du cap) : >= 5 orbes -- dépenser MAINTENANT.
    Quelqu'un encaisse des dégâts (pas plein) -- Luminous Barrier sur lui.
    Dawnbreaker Orb/Radiant Blast mis en cooldown pour isoler cette branche
    (sinon la génération, plus prioritaire, gagnerait avec 5 orbes dispo)."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},   # tank, quasi plein
        2: {"present": True, "hp_pct": 0.80, "cleanse_needed": False, "hot_active": False},   # encaisse des dégâts
    }, orbs=5, mana=1.0, early_game_rune_applied_ago_s=5.0)
    state.cooldown_until["dawnbreaker_orb"] = time.monotonic() + 5.0
    state.cooldown_until["radiant_blast"] = time.monotonic() + 5.0
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "luminous_barrier"
    assert intents[0].target == "player_2"
    print("[early game] Plafond de runes (>=5) et quelqu'un blessé -- Luminous Barrier plutôt que d'overcap.")


def test_early_game_rune_spend_soulbrand_when_nobody_hurt_and_cap_reached():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 1.0, "cleanse_needed": False, "hot_active": False},
    }, orbs=5, mana=1.0, early_game_rune_applied_ago_s=5.0)
    state.cooldown_until["dawnbreaker_orb"] = time.monotonic() + 5.0
    state.cooldown_until["radiant_blast"] = time.monotonic() + 5.0
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "soulbrand"
    assert intents[0].target is None, "Soulbrand cible un ennemi, jamais un allié"
    print("[early game] Plafond de runes (>=5), personne blessé -- Soulbrand (cible ennemie), pas d'overcap.")


def test_early_game_group_aoe_circle_of_light():
    """Item 5 (guide) : 3+ alliés sous 70% simultanément -- Circle of Light,
    ciblé sur le tank (même hypothèse de ciblage, cf. commentaire de tête --
    limite connue sur la vérification du cône). Tank volontairement AU-DESSUS
    de 70% ici, sinon l'item 3 (urgence tank) gagnerait avant d'atteindre
    l'AoE de groupe -- seuls les 3 autres alliés sont sous le seuil."""
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.75, "cleanse_needed": False, "hot_active": False},   # tank, au-dessus de 70%
        2: {"present": True, "hp_pct": 0.60, "cleanse_needed": False, "hot_active": False},
        3: {"present": True, "hp_pct": 0.55, "cleanse_needed": False, "hot_active": False},
        4: {"present": True, "hp_pct": 0.50, "cleanse_needed": False, "hot_active": False},
    }, orbs=0, mana=0.0, early_game_rune_applied_ago_s=5.0)   # mana=0 -- exclut Radiant Blast (item 4) de la course
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "circle_of_light"
    assert intents[0].target == "player_1"
    print("[early game] 3+ alliés sous 70% -- Circle of Light cible le tank (limite de cône connue, non résolue).")


def test_early_game_filler_dawnflare():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=0, mana=0.0, early_game_rune_applied_ago_s=5.0)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "dawnflare"
    assert intents[0].target is None
    print("[early game] Rien d'autre dû -- Dawnflare en boucle (jamais de temps mort).")


def test_early_game_post_tick_confirms_rune_of_renewal_only_when_actually_pressed():
    state = _state_with_party({
        1: {"present": True, "hp_pct": 0.95, "cleanse_needed": False, "hot_active": False},
    }, orbs=1)
    intents = vigour_pre_tick_early_game(state)
    assert intents[0].ability == "rune_of_renewal" and intents[0].target == "player_1"

    vigour_post_tick_early_game(state, "greater_heal")   # un autre sort a gagné -- pas de confirmation
    assert "early_game_rune_of_renewal_tank_applied_at" not in state.hooks

    vigour_post_tick_early_game(state, "rune_of_renewal")   # réellement pressé -- confirmation attendue
    assert "early_game_rune_of_renewal_tank_applied_at" in state.hooks
    print("[early game] post_tick horodate le tank seulement si Rune Of Renewal a vraiment été pressé.")


def test_early_game_gcd_gate_blocks_every_decision():
    state = _state_with_party({1: {"present": True, "hp_pct": 0.10, "cleanse_needed": False, "hot_active": False}})
    state.gcd_until = time.monotonic() + 10.0
    assert vigour_pre_tick_early_game(state) == []
    print("[early game] GCD actif -- ne propose rien, même avec un allié critique.")


if __name__ == "__main__":
    test_no_party_data_at_all_proposes_nothing()
    test_expired_party_data_proposes_nothing_even_if_someone_looks_critical()
    test_critical_ally_triggers_greater_heal_over_everything_else()
    test_picks_the_lowest_hp_ally_among_multiple_critical()
    test_group_damage_triggers_circle_of_light_targeted_on_tank()
    test_group_damage_does_not_trigger_circle_of_light_if_tank_is_absent()
    test_remove_magic_wins_over_hurt_handling_but_not_over_critical_or_group()
    test_dawnbreaker_orb_heals_lowest_hp_ally_when_someone_is_hurt_and_affordable()
    test_rune_of_renewal_used_when_hurt_but_not_enough_orbs_for_dawnbreaker()
    test_dawnbreaker_orb_skipped_when_on_cooldown_falls_back_to_rune_of_renewal()
    test_no_heal_at_all_falls_back_to_dawnflare_when_no_orbs()
    test_post_tick_is_a_no_op()
    test_soulbrand_fires_when_someone_took_damage_but_nobody_is_at_or_below_70()
    test_soulbrand_falls_back_to_dawnflare_when_no_orb_available()
    test_dawnflare_fires_when_everyone_is_topped_off()
    test_absent_slot_is_never_considered_for_any_heal()
    test_gcd_gate_blocks_every_decision_like_meiko_and_tariq()
    test_early_game_no_party_data_proposes_nothing()
    test_early_game_critical_ally_beats_buff_uptime()
    test_early_game_cleanse_beats_buff_uptime_but_not_critical()
    test_early_game_rune_of_renewal_uptime_on_tank_when_due()
    test_early_game_rune_of_renewal_skipped_when_recently_applied()
    test_early_game_tank_urgency_greater_heal_below_70()
    test_early_game_generation_prefers_dawnbreaker_orb_over_radiant_blast()
    test_early_game_generation_falls_back_to_radiant_blast_when_dawnbreaker_on_cooldown()
    test_early_game_radiant_blast_skipped_when_mana_too_low()
    test_early_game_rune_spend_luminous_barrier_when_someone_hurt_and_cap_reached()
    test_early_game_rune_spend_soulbrand_when_nobody_hurt_and_cap_reached()
    test_early_game_group_aoe_circle_of_light()
    test_early_game_filler_dawnflare()
    test_early_game_post_tick_confirms_rune_of_renewal_only_when_actually_pressed()
    test_early_game_gcd_gate_blocks_every_decision()
    print("\nTous les tests passent.")
