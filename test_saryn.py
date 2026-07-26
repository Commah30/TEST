"""
test_saryn.py — prouve, sans écran ni jeu, que le premier personnage hors
Fellowship (Saryn Prime, Warframe, cf. config/saryn.yaml, 2026-07-20) charge
correctement et que ses 2 conditions (buff retombé + Énergie suffisante) se
comportent exactement comme annoncé -- en particulier que `energy_above`
compare une VALEUR ABSOLUE (lue en OCR), PAS un pourcentage comme les autres
tokens `_above` (fury_above, toughness_above, etc.).

Lance avec : python test_saryn.py
Aucune dépendance externe (seul PyYAML est requis, via wardenbot.engine).
"""
from __future__ import annotations

from wardenbot.engine import CombatState, evaluate_condition, load_abilities


def test_saryn_yaml_loads_with_exactly_the_two_abilities():
    """config/saryn.yaml ne doit contenir QUE toxic_lash et nourish -- pas de
    visée/déplacement/3e sort, l'utilisateur garde la main dessus (cf.
    commentaire de tête du YAML, discussion CGU Warframe)."""
    abilities, meta = load_abilities("config/saryn.yaml")
    names = {a.name for a in abilities}
    assert names == {"toxic_lash", "nourish"}
    assert meta.get("gcd_ms") == 100
    print("config/saryn.yaml charge avec exactement les 2 abilities attendues.")


def test_both_abilities_are_off_gcd_and_skip_detection():
    """Warframe n'a pas de GCD partagé façon Fellowship -- les deux doivent
    être off_gcd pour ne jamais se bloquer l'une l'autre. skip_detection :
    la disponibilité est governée par la condition (buff + énergie), pas par
    un template d'icône."""
    abilities, _ = load_abilities("config/saryn.yaml")
    for a in abilities:
        assert a.off_gcd is True, f"{a.name} doit être off_gcd (pas de GCD Fellowship sur Warframe)"
        assert a.skip_detection is True, f"{a.name} doit être skip_detection (gaté par condition, pas template)"
    print("toxic_lash et nourish sont bien off_gcd + skip_detection.")


def test_toxic_lash_condition_requires_buff_expired_and_energy_above_200():
    abilities, _ = load_abilities("config/saryn.yaml")
    toxic_lash = next(a for a in abilities if a.name == "toxic_lash")
    assert toxic_lash.condition == "not buff:toxic_lash_active, energy_above:200"

    state = CombatState()
    state.resources["energy"] = 250
    state.buffs["toxic_lash_active"] = False
    assert evaluate_condition(toxic_lash.condition, state) is True, "buff retombé + énergie suffisante -> doit presser"

    state.resources["energy"] = 150
    assert evaluate_condition(toxic_lash.condition, state) is False, "énergie sous 200 -> ne doit PAS presser"

    state.resources["energy"] = 250
    state.buffs["toxic_lash_active"] = True
    assert evaluate_condition(toxic_lash.condition, state) is False, "buff encore actif -> ne doit PAS gâcher l'énergie à le rafraîchir"
    print("toxic_lash : condition gate bien buff retombé ET énergie >= 200.")


def test_nourish_condition_requires_buff_expired_and_energy_above_200():
    abilities, _ = load_abilities("config/saryn.yaml")
    nourish = next(a for a in abilities if a.name == "nourish")
    assert nourish.condition == "not buff:nourish_active, energy_above:200"

    state = CombatState()
    state.resources["energy"] = 300
    state.buffs["nourish_active"] = False
    assert evaluate_condition(nourish.condition, state) is True

    state.buffs["nourish_active"] = True
    assert evaluate_condition(nourish.condition, state) is False
    print("nourish : même garde-fou buff+énergie, indépendant de toxic_lash.")


def test_energy_above_compares_absolute_value_not_percentage():
    """LE POINT CRITIQUE (2026-07-20) : contrairement à fury_above/
    toughness_above/stormfall_above (qui multiplient resources[...] par 100,
    lisant une barre de remplissage 0.0-1.0), energy_above compare la valeur
    BRUTE -- l'Énergie Warframe est lue en OCR (read_digits) comme un nombre
    absolu (110, 200...) contre une réserve dont le plafond varie avec les
    mods, jamais normalisée 0.0-1.0. Si quelqu'un réintroduisait le *100 par
    erreur (copier-coller depuis un autre token), ce test doit le détecter :
    resources['energy']=200 doit satisfaire energy_above:200 (200 >= 200),
    PAS échouer comme le ferait 200*100=20000 >= 200 (toujours vrai, masquant
    le bug) ou 2.0 >= 200 (avec la valeur déjà pré-divisée, toujours faux)."""
    state = CombatState()
    state.resources["energy"] = 200.0
    assert evaluate_condition("energy_above:200", state) is True, "200 d'énergie brute doit satisfaire energy_above:200"
    state.resources["energy"] = 199.0
    assert evaluate_condition("energy_above:200", state) is False, "199 < 200 -> ne doit pas satisfaire"
    state.resources["energy"] = 1.0   # si quelqu'un stockait par erreur une valeur normalisee 0.0-1.0
    assert evaluate_condition("energy_above:200", state) is False, (
        "energy_above ne doit PAS multiplier par 100 -- une valeur normalisee par erreur doit rester sous le seuil"
    )
    print("energy_above compare bien la valeur absolue, pas un pourcentage (*100).")


def test_energy_defaults_to_zero_when_never_detected():
    """Avant que make_saryn_state_update_fn tourne (ou si le profil n'a pas
    la région 'energy'), la ressource vaut 0.0 par défaut -- energy_above:200
    reste donc faux en permanence, aucune presse accidentelle sans lecture
    réelle."""
    state = CombatState()
    assert evaluate_condition("energy_above:200", state) is False
    print("Sans détection, energy_above:200 reste faux -> aucune presse tant que l'Énergie n'est jamais lue (comportement attendu).")


if __name__ == "__main__":
    test_saryn_yaml_loads_with_exactly_the_two_abilities()
    test_both_abilities_are_off_gcd_and_skip_detection()
    test_toxic_lash_condition_requires_buff_expired_and_energy_above_200()
    test_nourish_condition_requires_buff_expired_and_energy_above_200()
    test_energy_above_compares_absolute_value_not_percentage()
    test_energy_defaults_to_zero_when_never_detected()
    print("\nTous les tests Saryn sont passés.")
