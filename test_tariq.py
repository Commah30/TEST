"""
test_tariq.py — prouve, sans écran ni jeu, que le fix "front montant" de
hammer_storm_proc_ready élimine le double-cast documenté dans message.txt
(bug : buff:hammer_storm_empowered est un test de NIVEAU, pas de FRONT).

Lance avec : python test_tariq.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from wardenbot.bar_reader import read_fill_pct
from wardenbot.engine import Ability, RotationEngine, State, evaluate_condition, load_abilities
from wardenbot.hooks import (
    BURST_PREP_WINDOW_S,
    BURST_TIMEOUT_MARGIN_S,
    HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED,
    PRESS_CONFIRM_WINDOW_S,
    SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED,
    tariq_pre_tick,
    tariq_post_tick,
)


def test_ready_flag_arms_only_on_rising_edge():
    state = State()

    # Buff absent -> jamais prêt.
    tariq_pre_tick(state)
    assert state.flags.get("hammer_storm_proc_ready") is None

    # Absent -> présent : front montant -> prêt.
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    print("Front montant absent->présent -> ready=True")


def test_stuck_active_reading_does_not_rearm_after_consumption():
    """Reproduit 'Case B' du rapport : le buff reste lu comme ACTIVE ~90
    ticks d'affilée (stall + plusieurs abilities castées entre-temps), sans
    JAMAIS repasser par 'not found'. Le fix ne doit pas redonner ready=True
    tant qu'aucune nouvelle transition n'a été vue, peu importe combien de
    ticks/casts s'écoulent."""
    state = State()

    # Proc initial : absent -> présent -> prêt -> consommé par le cast.
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    tariq_post_tick(state, "hammer_storm_proc")
    # Confirmation : une vraie canalisation démarre (cf.
    # HAMMER_STORM_PROC_CONFIRM_WINDOW_S) -- SEULE cette confirmation efface
    # ready désormais, pas la simple presse.
    state.flags["hammer_storm_casting"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is False
    state.flags["hammer_storm_casting"] = False

    # Le buff reste lu "actif" en continu sur ~90 ticks (cf. log du rapport),
    # avec d'autres abilities castées entre-temps (face_breaker, culling_strike
    # -- ne consomment pas le flag) -- ready ne doit jamais se rallumer sans
    # nouvelle transition absent->présent.
    for _ in range(90):
        tariq_pre_tick(state)
        assert state.flags["hammer_storm_proc_ready"] is False
        tariq_post_tick(state, "face_breaker")

    print("90 ticks 'ACTIVE' bloqué sans transition -> ready reste False (plus de double-cast).")


def test_legitimate_rapid_reproc_still_fires():
    """Reproduit 'Case A' du rapport (contre-exemple) : un vrai re-proc peut
    arriver ~1 GCD après le précédent (via Skull Crusher), avec une vraie
    transition ACTIVE -> not found -> ACTIVE entre les deux. Le fix doit
    laisser passer CE cas, sans attendre de cooldown_ms -- la preuve qu'il
    n'y a plus de valeur de délai à trouver.

    MIN_ABSENT_TICKS (2026-07-12) exige une absence confirmée sur plusieurs
    ticks consécutifs (filtre le bruit d'1 frame qui armait à tort le front
    montant sans vrai nouveau proc, cf. hooks.py) -- donc CE test simule
    l'absence sur MIN_ABSENT_TICKS ticks, pas un seul, pour rester une
    absence "confirmée" plutôt qu'un blip."""
    from wardenbot.hooks import MIN_ABSENT_TICKS
    state = State()

    # 1er proc.
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    tariq_post_tick(state, "hammer_storm_proc")
    state.flags["hammer_storm_casting"] = True   # confirme le 1er cast (cf. test précédent)
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is False
    state.flags["hammer_storm_casting"] = False

    # Vraie retombée du buff (détectée "not found"), confirmée sur plusieurs
    # ticks consécutifs -- pas un blip d'1 frame.
    state.buffs["hammer_storm_empowered"] = False
    for _ in range(MIN_ABSENT_TICKS):
        tariq_pre_tick(state)
        assert state.flags["hammer_storm_proc_ready"] is False

    # Nouveau proc réel l'instant d'après (~1 GCD plus tard dans le vrai jeu,
    # mais rien ici ne dépend du temps écoulé) -> nouveau front montant.
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    print("Retombée réelle CONFIRMÉE puis nouveau proc quasi immédiat -> ready=True (re-proc légitime autorisé).")


def test_single_frame_noise_does_not_rearm_ready_flag():
    """BUG TROUVÉ EN JEU (2026-07-12) : la détection peut clignoter sur UNE
    seule frame de bruit (absent->présent->absent->présent en quelques
    ticks) sans qu'un vrai nouveau proc n'ait eu lieu -- ready s'armait à
    tort sur ce bruit, causant des hammer_storm_proc/skull_crusher_proc
    castés "sans proc" réel. Un SEUL tick d'absence (pas confirmé sur
    MIN_ABSENT_TICKS) ne doit PAS suffire à réarmer ready après consommation."""
    state = State()

    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    tariq_post_tick(state, "hammer_storm_proc")
    state.flags["hammer_storm_casting"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is False
    state.flags["hammer_storm_casting"] = False

    # Un SEUL tick de bruit "absent" (pas confirmé) puis "présent" à nouveau.
    state.buffs["hammer_storm_empowered"] = False
    tariq_pre_tick(state)
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is False, "1 seul tick d'absence ne doit pas suffire à réarmer (bruit, pas un vrai proc)"
    print("1 tick de bruit isolé ne réarme pas ready -- filtré comme prévu.")


def test_skull_crusher_proc_mirrors_hammer_storm_pattern():
    """skull_crusher_proc a la même forme (buff:X level-check, cooldown_ms
    quasi nul) que hammer_storm_proc dans Schism V2.yaml, mais SANS même le
    'not last:' d'origine -- même fix appliqué par analogie (cf. hooks.py),
    pas encore confirmé par des logs en jeu."""
    state = State()

    state.buffs["skull_crusher_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["skull_crusher_proc_ready"] is True
    tariq_post_tick(state, "skull_crusher_proc")
    assert state.flags["skull_crusher_proc_ready"] is False

    # Bloqué "actif" sur plusieurs ticks -> ne redevient pas prêt tout seul.
    for _ in range(10):
        tariq_pre_tick(state)
        assert state.flags["skull_crusher_proc_ready"] is False
    print("skull_crusher_proc : même comportement front-montant que hammer_storm_proc.")


def test_the_two_proc_flags_are_tracked_independently():
    """Un front montant sur hammer_storm_empowered ne doit pas armer
    skull_crusher_proc_ready, et inversement -- ce sont deux instances de
    buff totalement indépendantes."""
    state = State()

    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True
    assert state.flags.get("skull_crusher_proc_ready") is None

    state.buffs["skull_crusher_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["skull_crusher_proc_ready"] is True
    assert state.flags["hammer_storm_proc_ready"] is True  # pas touché par l'autre
    print("Les deux flags de proc sont bien indépendants l'un de l'autre.")


def test_priority_cleave_is_a_distinct_mode_from_st_and_aoe():
    """Schism V2.yaml donne des priorités différentes par mode (ex.
    chain_lightning : st=6, cleave=3, aoe=3) -- le mode 'cleave' doit être
    un troisième mode à part entière, pas un alias de 'st' ou 'aoe'."""
    ab = Ability(name="chain_lightning", priority_st=6, priority_cleave=3, priority_aoe=3)
    assert ab.priority("st") == 6
    assert ab.priority("cleave") == 3
    assert ab.priority("aoe") == 3
    print("priority_cleave distinct de priority_st/priority_aoe : OK")


def test_condition_per_mode_falls_back_to_generic_condition():
    """focused_wrath n'a PAS de `condition` générique dans Schism V2.yaml,
    seulement condition_st/condition_cleave/condition_aoe -- vérifie que
    condition_for() choisit la bonne par mode, et retombe sur `condition`
    pour une ability qui n'a que ce champ générique (ex. hammer_storm_proc)."""
    focused_wrath = Ability(
        name="focused_wrath",
        condition_st="buff:skull_crusher_empowered",
        condition_cleave="buff:hammer_storm_empowered",
        condition_aoe="buff:hammer_storm_empowered",
    )
    assert focused_wrath.condition_for("st") == "buff:skull_crusher_empowered"
    assert focused_wrath.condition_for("cleave") == "buff:hammer_storm_empowered"

    hammer_storm_proc = Ability(name="hammer_storm_proc", condition="fury_above:51")
    assert hammer_storm_proc.condition_for("aoe") == "fury_above:51"
    print("condition_for() : priorité au champ par-mode, sinon fallback sur `condition` générique.")


def test_fury_above_condition_token():
    """Ressource nommée "fury" (pas "mana") -- confirmé par les tooltips en
    jeu ("25 Fury", "50 Fury" sur Skull Crusher/Hammer Storm)."""
    state = State()
    state.resources["fury"] = 0.50
    assert evaluate_condition("fury_above:51", state) is False
    assert evaluate_condition("fury_above:50", state) is True
    assert evaluate_condition("not fury_above:76", state) is True
    print("Token fury_above:N évalué correctement.")


def test_chain_lightning_uses_normal_detection_with_only_fury_gate():
    """DÉTECTION NORMALE (2026-07-14, choix de l'utilisateur) : chain_lightning est
    repassée en détection standard -- elle matche sa PROPRE icône allumée via
    is_ready_fn (comme toute ability sans skip_detection), et sa condition YAML
    ne garde plus que le gate de Fury (not fury_above:85). Plus de
    'not debuff:chain_lightning_dim' (l'ancienne détection inversée du template
    grisé a été retirée, cf. config/tariq.yaml + wardenbot/state_update.py)."""
    abilities, _ = load_abilities("config/tariq.yaml")
    chain = [a for a in abilities if a.name == "chain_lightning"][0]

    # La détection se fait maintenant par template (is_ready_fn), pas par une
    # condition inversée -> skip_detection doit être False et la condition ne
    # doit plus référencer chain_lightning_dim.
    assert chain.skip_detection is False, "chain_lightning doit repasser en détection normale (skip_detection=False)"
    assert "chain_lightning_dim" not in chain.condition, f"la condition ne doit plus référencer le debuff grisé, reçu {chain.condition!r}"

    # Seul le gate de Fury reste : bloquée au-dessus de 85%, libre en-dessous.
    state = State()
    state.resources["fury"] = 0.90
    assert evaluate_condition(chain.condition, state) is False, "au-dessus de 85% Fury, chain_lightning doit être bloquée"
    state.resources["fury"] = 0.50
    assert evaluate_condition(chain.condition, state) is True, "sous 85% Fury, la condition passe (la détection template gère le reste)"
    print("chain_lightning : détection normale, seul le gate de Fury (not fury_above:85) reste dans la condition.")


def test_bar_reader_ltr_and_rtl_directions():
    """Utilitaire générique -- vérifie que read_fill_pct distingue bien un
    remplissage ancré à gauche d'un remplissage ancré à droite. (Hammer
    Storm s'est avéré être ltr en pratique -- rempli ancré à gauche, le bord
    droit recule vers la gauche pendant la canalisation, vérifié image par
    image sur un vrai clip Game Bar -- mais la fonction supporte les deux
    cas puisque rage_bar ou une autre barre pourrait être ancrée à droite.)"""
    fill = (200, 50, 20)
    half_left_filled = np.zeros((6, 100, 3), dtype=np.uint8)
    half_left_filled[:, :50] = fill

    assert abs(read_fill_pct(half_left_filled, fill, direction="ltr") - 0.5) < 0.02
    assert read_fill_pct(half_left_filled, fill, direction="rtl") < 0.02

    half_right_filled = np.zeros((6, 100, 3), dtype=np.uint8)
    half_right_filled[:, 50:] = fill
    assert abs(read_fill_pct(half_right_filled, fill, direction="rtl") - 0.5) < 0.02
    print("bar_reader : ltr et rtl distinguent bien le côté rempli.")


def test_bar_reader_tolerates_single_pixel_border_artifact():
    """Bug réel trouvé sur le vrai clip Hammer Storm : la bordure de la
    barre a 1-2px anti-aliasés qui ratent le seuil de couleur à eux seuls --
    sans tolérance de gap, la lecture entière retombait à 0% même sur une
    frame remplie à 90% à l'oeil. max_gap doit absorber un petit défaut
    isolé sans pour autant ignorer une vraie fin de remplissage."""
    fill = (200, 50, 20)
    frame = np.zeros((4, 100, 3), dtype=np.uint8)
    frame[:, :80] = fill
    frame[:, 0] = (0, 0, 0)   # 1 pixel de bordure "mort" au tout début du scan

    # Sans tolérance de gap, ce pixel isolé ferait chuter la lecture à 0%.
    assert read_fill_pct(frame, fill, direction="ltr", max_gap=0) == 0.0
    # Avec la tolérance par défaut, il est absorbé -- lecture correcte (~80%).
    pct = read_fill_pct(frame, fill, direction="ltr")
    assert abs(pct - 0.80) < 0.02, f"attendu ~80%, reçu {pct:.1%}"
    print("bar_reader : un pixel de bordure isolé n'invalide plus toute la lecture.")


def test_hard_lock_blocks_even_off_gcd_abilities():
    """Hammer Storm ne doit JAMAIS être coupé par une autre ability pendant
    sa canalisation -- même une off_gcd, qui bypasse normalement le gate de
    GCD classique (culling_strike, focused_wrath, thunder_call...). Le
    verrou hard_lock_until doit bloquer sans exception."""
    state = State()
    state.hard_lock_until = time.monotonic() + 10.0  # verrouillé loin dans le futur

    ability = Ability(name="culling_strike", priority_st=0, off_gcd=True, skip_detection=True)
    engine = RotationEngine(
        abilities=[ability],
        state=state,
        press_fn=lambda name: None,
        is_ready_fn=lambda name, threshold=None: True,
    )
    pressed = engine.tick()
    assert pressed is None, f"hard_lock doit bloquer même une ability off_gcd, reçu {pressed!r}"
    print("hard_lock_until bloque bien une ability off_gcd pendant la canalisation.")


def test_hammer_storm_proc_fires_on_any_single_proc():
    """SIMPLIFICATION VOLONTAIRE (2026-07-12) : plus d'attente d'un 2e stack
    (le jeu n'affiche jamais "2" sur ce badge, cf. hooks.py) -- Hammer Storm
    est la source de dégâts principale ("aucun proc ne doit rater"), elle
    doit partir dès que prête + fury suffisante, sur N'IMPORTE QUEL proc,
    QUE Focused Wrath soit up ou non."""
    abilities, meta = load_abilities("config/tariq.yaml")
    state = State(mode="st")
    state.resources["fury"] = 1.0
    state.flags["hammer_storm_proc_ready"] = True
    # Simule un état de jeu réaliste pour les trois autres off_gcd de
    # priorité 0 (sinon un State() vierge les laisse "toujours prêtes" et
    # elles gagnent le scan avant même d'atteindre hammer_storm_proc) :
    state.debuffs["culling_strike_dim"] = True  # cible pas sous 35% PV -> indisponible
    state.cooldown_until["thunder_call"] = time.monotonic() + 999.0  # déjà utilisée récemment (cooldown 43s)
    state.cooldown_until["focused_wrath"] = time.monotonic() + 999.0  # déjà utilisée récemment (cooldown 86s)
    # heavy_strike (priority_st:3) gagne désormais sans ambiguïté sur
    # hammer_storm_proc (priority_st:4 depuis 2026-07-15, cf. config/tariq.yaml)
    # -- gaté ici comme les 3 autres ci-dessus pour isoler ce que ce test
    # vérifie vraiment (hammer_storm_proc part sans focused_wrath_empowered),
    # sans dépendre de l'arbitrage heavy_strike/hammer_storm_proc lui-même.
    state.cooldown_until["heavy_strike"] = time.monotonic() + 999.0

    engine = RotationEngine(
        abilities=abilities,
        state=state,
        press_fn=lambda name: None,
        is_ready_fn=lambda name, threshold=None: True,
        gcd_ms=meta.get("gcd_ms", 1000),
        pre_tick_hook=tariq_pre_tick,
        post_tick_hook=tariq_post_tick,
        off_gcd_hook_abilities=frozenset({"focused_wrath"}),
    )

    pressed = engine.tick()
    assert pressed == "hammer_storm_proc", f"attendu hammer_storm_proc même sans focused_wrath_empowered, reçu {pressed!r}"
    print("hammer_storm_proc part bien sans focused_wrath_empowered -- OK.")


def test_focused_wrath_discount_fires_hammer_storm_below_normal_threshold():
    """BUG TROUVÉ EN JEU (2026-07-12), signalé par l'utilisateur et vérifié sur
    fellowship.wiki.fextralife.com/Focused+Wrath : Focused Wrath réduit de
    50% le coût en Fury des 2 prochains Hammer Storm/Skull Crusher -- le buff
    peut venir de Leap Smash (castée manuellement par l'utilisateur), pas seulement
    d'un cast du bot. Fury à 35% (sous le seuil plein 55, au-dessus du seuil
    réduit 30 -- cf. commentaire HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED,
    hooks.py, marge ajoutée le 2026-07-13) + focused_wrath_empowered actif ->
    doit quand même partir, sinon le bot croit ne pas avoir assez de Fury
    pour un proc qu'il peut réellement se permettre."""
    state = State()
    state.buffs["focused_wrath_empowered"] = True
    state.flags["hammer_storm_proc_ready"] = True
    state.resources["fury"] = 0.35
    assert state.resources["fury"] * 100 > HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
    pressed = tariq_pre_tick(state)
    assert pressed == "hammer_storm_proc", f"attendu hammer_storm_proc au seuil réduit, reçu {pressed!r}"
    print("Focused Wrath actif (ex: Leap Smash) -> hammer_storm_proc part sous le seuil plein.")


def test_focused_wrath_discount_does_not_fire_below_discounted_threshold():
    """Même le seuil RÉDUIT doit rester respecté -- Focused Wrath ne rend pas
    le proc gratuit, juste moins cher."""
    state = State()
    state.buffs["focused_wrath_empowered"] = True
    state.flags["hammer_storm_proc_ready"] = True
    state.resources["fury"] = 0.10
    assert state.resources["fury"] * 100 < HAMMER_STORM_PROC_FURY_THRESHOLD_DISCOUNTED
    pressed = tariq_pre_tick(state)
    assert pressed != "hammer_storm_proc", f"ne devrait pas partir sous le seuil réduit, reçu {pressed!r}"
    print("Focused Wrath actif mais Fury sous le seuil réduit -> hammer_storm_proc ne part pas.")


def test_focused_wrath_discount_fires_skull_crusher_below_normal_threshold():
    """Même mécanisme que Hammer Storm -- Focused Wrath réduit aussi le coût
    de Skull Crusher (2 charges partagées entre les deux abilities)."""
    state = State()
    state.buffs["focused_wrath_empowered"] = True
    state.flags["skull_crusher_proc_ready"] = True
    state.resources["fury"] = 0.15
    assert state.resources["fury"] * 100 > SKULL_CRUSHER_PROC_FURY_THRESHOLD_DISCOUNTED
    pressed = tariq_pre_tick(state)
    assert pressed == "skull_crusher_proc", f"attendu skull_crusher_proc au seuil réduit, reçu {pressed!r}"
    print("Focused Wrath actif -> skull_crusher_proc part aussi sous son seuil réduit.")


def test_no_discount_without_focused_wrath_active():
    """Sans focused_wrath_empowered, le seuil réduit ne doit JAMAIS
    s'appliquer -- le hook ne force rien, le scan normal (fury_above:55
    plein, config/tariq.yaml) reste seul juge."""
    state = State()
    state.flags["hammer_storm_proc_ready"] = True
    state.resources["fury"] = 0.30   # au-dessus du seuil réduit, en dessous du seuil plein
    pressed = tariq_pre_tick(state)
    assert pressed != "hammer_storm_proc", f"ne devrait pas forcer le proc sans Focused Wrath actif, reçu {pressed!r}"
    print("Pas de Focused Wrath actif -> pas de seuil réduit, le hook ne force rien.")


def test_hammer_storm_casting_flag_refreshes_hard_lock():
    """tariq_pre_tick doit renouveler hard_lock_until tant que
    state.flags['hammer_storm_casting'] est vrai (détection écran fraîche
    du tick), et NE PLUS le renouveler dès que ça repasse à False -- pas
    besoin de connaître la durée exacte du cast, juste de suivre l'état
    réel détecté à chaque tick (même principe que tout le reste de
    wardenbot : détecter, pas estimer)."""
    state = State()
    now = time.monotonic()

    state.flags["hammer_storm_casting"] = True
    tariq_pre_tick(state)
    assert state.hard_lock_until > now, "le verrou doit être renouvelé pendant la canalisation"

    locked_until_mid_cast = state.hard_lock_until

    # Toujours en train de caster au tick suivant -> le verrou est repoussé.
    tariq_pre_tick(state)
    assert state.hard_lock_until >= locked_until_mid_cast

    # Canalisation terminée (détection repasse à False) -> plus de renouvellement.
    state.flags["hammer_storm_casting"] = False
    locked_until_end = state.hard_lock_until
    tariq_pre_tick(state)
    assert state.hard_lock_until == locked_until_end, "le verrou ne doit plus être repoussé une fois la canalisation terminée"
    print("hard_lock_until suit bien state.flags['hammer_storm_casting'] tick par tick.")


def test_hammer_storm_proc_press_without_real_cast_stays_ready_for_retry():
    """BUG TROUVÉ EN JEU (2026-07-13, rapporté par l'utilisateur depuis l'observation
    directe en jeu) : le bot presse hammer_storm_proc alors que la Fury
    RÉELLE en jeu est insuffisante (lecture périmée pendant un des épisodes
    de gel du thread de détection, cf. debug.log 2026-07-12) -- le jeu
    refuse silencieusement le cast, mais tariq_post_tick marquait quand même
    le proc "consommé" (ready -> False) sur la seule foi d'avoir pressé la
    touche. Résultat : plus aucun nouveau front montant ne pouvait s'armer
    tant que le buff (resté actif en jeu, jamais réellement consommé) ne
    retombe pas -- Hammer Storm restait bloqué indéfiniment.

    Fix : ready ne doit RESTER effacé que si hammer_storm_casting (cast bar,
    vérité terrain) confirme une vraie canalisation dans la fenêtre qui suit
    la presse. Sans confirmation, ready doit rester tel quel (True) pour que
    le prochain tick retente naturellement."""
    from wardenbot.hooks import HAMMER_STORM_PROC_CONFIRM_WINDOW_S

    state = State()
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is True

    tariq_post_tick(state, "hammer_storm_proc")
    # Toujours "en attente" juste après la presse -- ready pas encore jugé.
    assert state.flags["_hammer_storm_proc_pending"] is True

    # Le jeu ne démarre JAMAIS de canalisation (Fury réelle insuffisante) --
    # hammer_storm_casting reste False tout du long. On avance le temps
    # au-delà de la fenêtre de confirmation sans jamais poser casting=True.
    state.flags["_hammer_storm_proc_pending_since"] -= (HAMMER_STORM_PROC_CONFIRM_WINDOW_S + 0.1)
    tariq_pre_tick(state)

    assert state.flags["hammer_storm_proc_ready"] is True, (
        "un press qui n'a mené à AUCUNE canalisation réelle ne doit pas "
        "consommer le proc -- sinon le bot croit à tort l'avoir utilisé"
    )
    assert state.flags["_hammer_storm_proc_pending"] is False, "le marqueur d'attente doit être levé une fois la fenêtre expirée"
    print("Press sans canalisation réelle confirmée -> ready reste True, retente possible au tick suivant.")


def test_hammer_storm_proc_press_with_real_cast_confirms_consumption():
    """Contre-exemple du test précédent : quand le jeu démarre réellement la
    canalisation (Fury suffisante, cast accepté), ready doit bien finir par
    passer à False -- la confirmation fonctionne, pas seulement le rejet."""
    state = State()
    state.buffs["hammer_storm_empowered"] = True
    tariq_pre_tick(state)
    tariq_post_tick(state, "hammer_storm_proc")
    assert state.flags["hammer_storm_proc_ready"] is True, "pas encore consommé avant confirmation"

    state.flags["hammer_storm_casting"] = True
    tariq_pre_tick(state)
    assert state.flags["hammer_storm_proc_ready"] is False, "la canalisation confirmée doit consommer le proc"
    print("Press suivi d'une vraie canalisation détectée -> ready passe bien à False.")


def test_heavy_strike_outranks_skull_crusher_proc_in_priority():
    """BUG TROUVÉ EN JEU (2026-07-12, debug.log) : heavy_strike et
    skull_crusher_proc étaient à égalité de priority_st (3) -- au tie-break
    par ordre de déclaration, skull_crusher_proc (déclarée avant dans le
    YAML) gagnait systématiquement, retardant heavy_strike bien au-delà de
    la fin de son propre cooldown alors que l'utilisateur a dit que c'est un point
    clé de la rotation. Vérifie que la vraie config/tariq.yaml a bien
    heavy_strike strictement prioritaire désormais."""
    abilities, meta = load_abilities("config/tariq.yaml")
    by_name = {a.name: a for a in abilities}
    assert by_name["heavy_strike"].priority_st < by_name["skull_crusher_proc"].priority_st
    print("heavy_strike a bien une priority_st strictement inférieure (= prioritaire) à skull_crusher_proc.")


def test_planner_stays_normal_when_no_burst_window_imminent():
    """Ni proche l'un de l'autre (>BURST_PREP_WINDOW_S), ni déjà simultanés :
    le Planner ne doit jamais entrer en prepare_burst sans raison -- pas de
    régression sur le comportement normal (aucune attente artificielle)."""
    state = State()
    now = time.monotonic()

    # Les deux loin d'être prêts.
    state.cooldown_until["focused_wrath"] = now + 50.0
    state.cooldown_until["thunder_call"] = now + 50.0
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "normal"
    assert state.flags["burst_hold"] is False

    # Les deux DÉJÀ prêts en même temps -- pas besoin d'attendre, le scan
    # normal les prend l'un après l'autre sans intervention du planner.
    state.cooldown_until.pop("focused_wrath", None)
    state.cooldown_until.pop("thunder_call", None)
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "normal"
    assert state.flags["burst_hold"] is False
    print("Planner : reste 'normal' hors fenêtre d'anticipation et quand les deux sont déjà simultanés.")


def test_planner_holds_the_ready_cooldown_until_the_other_catches_up():
    """Coeur du Planner (cf. hooks.py, _update_tariq_planner) : thunder_call
    prêt maintenant, focused_wrath prêt dans 5s (< BURST_PREP_WINDOW_S) ->
    entre en prepare_burst et RETIENT thunder_call (burst_hold) via la
    condition YAML réelle (not flag:burst_hold), au lieu de le tirer seul --
    preuve concrète que ça corrige l'écart de 22-27s mesuré dans debug.log."""
    from wardenbot.engine import load_abilities, evaluate_condition

    state = State()
    now = time.monotonic()
    state.cooldown_until["focused_wrath"] = now + 5.0   # pas encore prêt, mais bientôt

    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "prepare_burst"
    assert state.flags["burst_hold"] is True

    abilities, _ = load_abilities("config/tariq.yaml")
    by_name = {a.name: a for a in abilities}
    assert evaluate_condition(by_name["thunder_call"].condition_for("st"), state) is False, (
        "thunder_call doit être retenu par burst_hold en attendant focused_wrath"
    )
    print("Planner : thunder_call bien retenu (burst_hold) le temps que focused_wrath rattrape.")


def test_planner_releases_hold_once_both_are_ready():
    """Une fois que le second cooldown rattrape le premier, le burst doit se
    déclencher -- retour à 'normal' et burst_hold levé, le scan normal presse
    les deux (focused_wrath puis thunder_call, à un tick d'écart tout au
    plus, cf. tie-break par ordre de déclaration dans le YAML)."""
    state = State()
    now = time.monotonic()
    state.cooldown_until["focused_wrath"] = now + 5.0
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "prepare_burst"

    # Les 5s se sont "écoulées" -- focused_wrath est maintenant prêt aussi.
    state.cooldown_until["focused_wrath"] = time.monotonic() - 0.01
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "normal"
    assert state.flags["burst_hold"] is False
    print("Planner : libère la retenue dès que les deux cooldowns sont prêts (burst déclenché).")


def test_planner_timeout_prevents_indefinite_hold():
    """Sécurité anti-blocage : si l'attente dépasse largement la fenêtre
    prévue (ex: Hammer Storm hard_lock a retardé toute la rotation pendant
    l'attente), le planner doit abandonner plutôt que de rester bloqué
    indéfiniment en prepare_burst."""
    state = State()
    now = time.monotonic()
    state.cooldown_until["focused_wrath"] = now + 5.0
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "prepare_burst"

    # focused_wrath n'est TOUJOURS pas prêt (reste à now+5.0, dans le futur),
    # mais l'objectif a été pris il y a bien plus longtemps que la fenêtre +
    # sa marge -- le timeout doit forcer le retour à 'normal'.
    state.flags["tariq_objective_since"] = time.monotonic() - (BURST_PREP_WINDOW_S + BURST_TIMEOUT_MARGIN_S + 0.1)
    tariq_pre_tick(state)
    assert state.flags["tariq_objective"] == "normal"
    assert state.flags["burst_hold"] is False
    print("Planner : le timeout de sécurité débloque l'attente si ça dérive trop.")


def test_fury_conserve_blocks_only_discretionary_skull_crusher_filler():
    """fury_conserve (posé par le planner pendant prepare_burst) ne doit
    retenir QUE skull_crusher (dump discrétionnaire) -- jamais les procs
    (règle historique "aucun proc ne doit rater") ni les générateurs
    (face_breaker/chain_lightning/wild_swing), qui doivent au contraire
    continuer à builder de la Fury pendant l'attente d'un burst -- sinon le
    burst démarrerait avec MOINS de Fury que possible, l'inverse du but."""
    from wardenbot.engine import load_abilities, evaluate_condition

    abilities, _ = load_abilities("config/tariq.yaml")
    by_name = {a.name: a for a in abilities}

    state = State(mode="st")
    state.resources["fury"] = 1.0
    state.flags["fury_conserve"] = True
    assert evaluate_condition(by_name["skull_crusher"].condition_for("st"), state) is False, (
        "skull_crusher (dump discrétionnaire) doit être retenu pendant fury_conserve"
    )

    state.flags["skull_crusher_proc_ready"] = True
    state.flags["hammer_storm_proc_ready"] = True
    assert evaluate_condition(by_name["skull_crusher_proc"].condition_for("st"), state) is True
    assert evaluate_condition(by_name["hammer_storm_proc"].condition_for("st"), state) is True

    state.resources["fury"] = 0.5   # sous les seuils not fury_above des générateurs -- isole fury_conserve de leur propre garde anti-overcap
    assert evaluate_condition(by_name["face_breaker"].condition_for("st"), state) is True
    assert evaluate_condition(by_name["chain_lightning"].condition_for("st"), state) is True
    assert evaluate_condition(by_name["wild_swing"].condition_for("st"), state) is True
    print("fury_conserve retient bien skull_crusher (filler) sans toucher aux procs/générateurs.")


def test_gcd_veil_confirms_a_tracked_press_as_success():
    """Le voile GCD partagé (state.buffs['gcd_veil_active']) doit confirmer
    une presse GCD (ex: heavy_strike) comme SUCCESS dès qu'il apparaît dans
    la fenêtre de confirmation -- cf. hooks.py, PRESS_CONFIRM_WINDOW_S
    (2026-07-14, suite au signalement de l'utilisateur sur les presses fantômes
    visibles en jeu mais invisibles dans les logs pour TOUTE ability autre
    que hammer_storm_proc)."""
    import wardenbot.diagnostics as diagnostics
    test_log = Path("test_press_confirm_scratch.log")
    if test_log.exists():
        test_log.unlink()
    original_path = diagnostics.PRESS_CONFIRM_LOG_PATH
    diagnostics.PRESS_CONFIRM_LOG_PATH = test_log
    try:
        state = State()
        tariq_post_tick(state, "heavy_strike")
        assert state.flags["_press_confirmations_pending"], "une confirmation doit être enregistrée pour une ability GCD trackée"

        state.buffs["gcd_veil_active"] = True
        tariq_pre_tick(state)

        content = test_log.read_text()
        assert "heavy_strike" in content and "SUCCESS" in content
        assert not state.flags["_press_confirmations_pending"], "la confirmation résolue doit être retirée de la liste d'attente"
    finally:
        diagnostics.PRESS_CONFIRM_LOG_PATH = original_path
        if test_log.exists():
            test_log.unlink()
    print("Voile GCD partagé : confirme bien une presse GCD comme SUCCESS.")


def test_gcd_veil_timeout_flags_press_as_phantom():
    """Si le voile GCD n'apparaît jamais dans la fenêtre de confirmation, la
    presse doit être journalisée PHANTOM_PRESS -- exactement le signal que
    l'utilisateur voit visuellement en jeu (icône qui flashe sans effet) mais qu'on
    ne pouvait pas prouver dans le code jusqu'ici pour la plupart des
    abilities."""
    import wardenbot.diagnostics as diagnostics
    test_log = Path("test_press_confirm_scratch.log")
    if test_log.exists():
        test_log.unlink()
    original_path = diagnostics.PRESS_CONFIRM_LOG_PATH
    diagnostics.PRESS_CONFIRM_LOG_PATH = test_log
    try:
        state = State()
        tariq_post_tick(state, "wild_swing")
        # simule l'écoulement de la fenêtre de confirmation SANS jamais voir le voile.
        state.flags["_press_confirmations_pending"][0]["requested_at_monotonic"] -= (PRESS_CONFIRM_WINDOW_S + 0.1)
        state.buffs["gcd_veil_active"] = False
        tariq_pre_tick(state)

        content = test_log.read_text()
        assert "wild_swing" in content and "PHANTOM_PRESS" in content
        assert not state.flags["_press_confirmations_pending"]
    finally:
        diagnostics.PRESS_CONFIRM_LOG_PATH = original_path
        if test_log.exists():
            test_log.unlink()
    print("Fenêtre de confirmation expirée sans voile -> PHANTOM_PRESS journalisé.")


def test_off_gcd_veil_is_independent_from_shared_gcd_veil():
    """culling_strike (off_gcd) doit être confirmée par SON PROPRE voile
    (veil_culling_strike, suit SON cooldown de 4.44s) -- pas par le voile GCD
    partagé, qu'elle ne consomme pas. Une presse culling_strike doit pouvoir
    se confirmer via veil_culling_strike seul, même si gcd_veil_active reste
    False pendant ce temps."""
    import wardenbot.diagnostics as diagnostics
    test_log = Path("test_press_confirm_scratch.log")
    if test_log.exists():
        test_log.unlink()
    original_path = diagnostics.PRESS_CONFIRM_LOG_PATH
    diagnostics.PRESS_CONFIRM_LOG_PATH = test_log
    try:
        state = State()
        tariq_post_tick(state, "culling_strike")
        state.buffs["gcd_veil_active"] = False   # le GCD partagé ne bouge pas -- culling_strike ne le consomme pas
        state.buffs["veil_culling_strike"] = True
        tariq_pre_tick(state)

        content = test_log.read_text()
        assert "culling_strike" in content and "SUCCESS" in content
    finally:
        diagnostics.PRESS_CONFIRM_LOG_PATH = original_path
        if test_log.exists():
            test_log.unlink()
    print("culling_strike confirmée via son propre voile, indépendamment du GCD partagé.")


def test_press_confirmation_abandoned_not_phantom_after_pause_gap():
    """BUG TROUVÉ EN JEU (2026-07-14, 1re session avec cette instrumentation) :
    13/38 PHANTOM_PRESS coïncidaient exactement avec un cycle pause/reprise
    (touche C) -- tariq_pre_tick ne tourne pas pendant la pause, mais
    time.monotonic() continue d'avancer, donc au réveil tout ce qui était en
    attente dépassait déjà la fenêtre de confirmation et se journalisait à
    tort comme un échec. Un grand écart entre deux appels consécutifs de
    _resolve_press_confirmations doit ABANDONNER silencieusement les
    confirmations en attente (ininterprétables), pas les journaliser
    PHANTOM_PRESS."""
    import wardenbot.diagnostics as diagnostics
    test_log = Path("test_press_confirm_scratch.log")
    if test_log.exists():
        test_log.unlink()
    original_path = diagnostics.PRESS_CONFIRM_LOG_PATH
    diagnostics.PRESS_CONFIRM_LOG_PATH = test_log
    try:
        state = State()
        tariq_post_tick(state, "focused_wrath")
        tariq_pre_tick(state)   # 1er appel -- initialise _press_confirm_last_tick_at, rien à résoudre encore
        assert state.flags["_press_confirmations_pending"]

        # Simule une pause de plusieurs secondes (touche C) -- tariq_pre_tick
        # n'a PAS été appelé pendant ce temps, donc _press_confirm_last_tick_at
        # reste très en arrière par rapport à un vrai now() après la coupure.
        state.flags["_press_confirm_last_tick_at"] -= 5.0
        state.buffs["veil_focused_wrath"] = False
        tariq_pre_tick(state)

        assert not state.flags["_press_confirmations_pending"], "la confirmation doit être abandonnée, pas laissée en attente"
        content = test_log.read_text() if test_log.exists() else ""
        assert "PHANTOM_PRESS" not in content, "un écart type pause ne doit JAMAIS produire un faux PHANTOM_PRESS"
    finally:
        diagnostics.PRESS_CONFIRM_LOG_PATH = original_path
        if test_log.exists():
            test_log.unlink()
    print("Écart type pause -> confirmation abandonnée silencieusement, pas de faux PHANTOM_PRESS.")


def test_debug_choice_reason_explains_why_the_winner_beat_the_runner_up():
    """AUDIT (2026-07-14) : trou d'observabilité trouvé dans l'audit rotation/
    stabilité -- debug_skip_trace répond à 'pourquoi PAS X' (seulement quand
    rien n'est pressé), mais rien ne répondait à 'pourquoi X A gagné' quand
    une ability EST choisie. Vérifie que _pick_ability remplit
    state.debug_choice_reason avec sa priorité et le concurrent de priorité
    plus haute qu'il a devancé."""
    abilities, meta = load_abilities("config/tariq.yaml")
    state = State(mode="st")
    state.resources["fury"] = 1.0
    state.flags["hammer_storm_proc_ready"] = True
    state.debuffs["culling_strike_dim"] = True
    state.cooldown_until["thunder_call"] = time.monotonic() + 999.0
    state.cooldown_until["focused_wrath"] = time.monotonic() + 999.0
    # heavy_strike (priority_st:3) gagne désormais sur hammer_storm_proc
    # (priority_st:4 depuis 2026-07-15) -- gaté pour isoler ce que ce test
    # vérifie (le contenu de debug_choice_reason), cf. même fix dans
    # test_hammer_storm_proc_fires_on_any_single_proc ci-dessus.
    state.cooldown_until["heavy_strike"] = time.monotonic() + 999.0

    engine = RotationEngine(
        abilities=abilities, state=state,
        press_fn=lambda name: None, is_ready_fn=lambda name, threshold=None: True,
        gcd_ms=meta.get("gcd_ms", 1000),
    )
    pressed = engine.tick()
    assert pressed == "hammer_storm_proc"
    assert "priority=4" in state.debug_choice_reason
    assert "devançait" in state.debug_choice_reason
    print(f"choice_reason: {state.debug_choice_reason!r}")
    print("debug_choice_reason explique bien pourquoi hammer_storm_proc a gagné.")


if __name__ == "__main__":
    test_ready_flag_arms_only_on_rising_edge()
    test_stuck_active_reading_does_not_rearm_after_consumption()
    test_legitimate_rapid_reproc_still_fires()
    test_single_frame_noise_does_not_rearm_ready_flag()
    test_skull_crusher_proc_mirrors_hammer_storm_pattern()
    test_the_two_proc_flags_are_tracked_independently()
    test_priority_cleave_is_a_distinct_mode_from_st_and_aoe()
    test_condition_per_mode_falls_back_to_generic_condition()
    test_fury_above_condition_token()
    test_chain_lightning_uses_normal_detection_with_only_fury_gate()
    test_bar_reader_ltr_and_rtl_directions()
    test_bar_reader_tolerates_single_pixel_border_artifact()
    test_hard_lock_blocks_even_off_gcd_abilities()
    test_hammer_storm_proc_fires_on_any_single_proc()
    test_focused_wrath_discount_fires_hammer_storm_below_normal_threshold()
    test_focused_wrath_discount_does_not_fire_below_discounted_threshold()
    test_focused_wrath_discount_fires_skull_crusher_below_normal_threshold()
    test_no_discount_without_focused_wrath_active()
    test_hammer_storm_casting_flag_refreshes_hard_lock()
    test_hammer_storm_proc_press_without_real_cast_stays_ready_for_retry()
    test_hammer_storm_proc_press_with_real_cast_confirms_consumption()
    test_heavy_strike_outranks_skull_crusher_proc_in_priority()
    test_planner_stays_normal_when_no_burst_window_imminent()
    test_planner_holds_the_ready_cooldown_until_the_other_catches_up()
    test_planner_releases_hold_once_both_are_ready()
    test_planner_timeout_prevents_indefinite_hold()
    test_fury_conserve_blocks_only_discretionary_skull_crusher_filler()
    test_gcd_veil_confirms_a_tracked_press_as_success()
    test_gcd_veil_timeout_flags_press_as_phantom()
    test_off_gcd_veil_is_independent_from_shared_gcd_veil()
    test_press_confirmation_abandoned_not_phantom_after_pause_gap()
    test_debug_choice_reason_explains_why_the_winner_beat_the_runner_up()
    print("\nTous les tests passent.")
