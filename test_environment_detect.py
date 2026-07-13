"""
test_environment_detect.py — prouve que la détection d'environnement
(résolution, écrans, fenêtre du jeu) retourne des valeurs cohérentes sur une
vraie machine Windows. Contrairement à dpi_awareness.py, ces fonctions sont
en LECTURE SEULE (pas de "ne peut être fixé qu'une fois par process") --
donc pas besoin d'injection de dépendance, on peut les appeler pour de vrai
autant de fois qu'on veut.

Lance avec : python test_environment_detect.py
"""
from __future__ import annotations

from wardenbot.environment_detect import (
    find_window_by_title_hint,
    get_primary_resolution,
    list_monitors,
)


def test_get_primary_resolution_returns_sane_dimensions():
    width, height = get_primary_resolution()
    assert 100 < width < 20000, f"largeur suspecte : {width}"
    assert 100 < height < 20000, f"hauteur suspecte : {height}"
    print(f"Résolution principale détectée : {width}x{height}")


def test_list_monitors_finds_at_least_one_with_exactly_one_primary():
    monitors = list_monitors()
    assert len(monitors) >= 1, "au moins l'écran principal doit être détecté"
    primaries = [m for m in monitors if m.is_primary]
    assert len(primaries) == 1, f"exactement un écran principal attendu, reçu {len(primaries)}"
    for m in monitors:
        assert m.width > 0 and m.height > 0
    print(f"{len(monitors)} écran(s) détecté(s) : {monitors}")


def test_find_window_by_title_hint_returns_none_for_nonexistent_window():
    result = find_window_by_title_hint("xyz_fenetre_qui_nexiste_surement_jamais_12345")
    assert result is None
    print("Fenêtre inexistante -> None, comme attendu.")


def test_find_window_by_title_hint_does_not_crash_with_default_hint():
    """Ne vérifie pas que le jeu EST ouvert (dépend de la machine qui lance
    le test) -- juste que l'appel avec le hint par défaut (GAME_WINDOW_TITLE_HINT)
    ne plante jamais, jeu ouvert ou non."""
    result = find_window_by_title_hint()
    assert result is None or isinstance(result, str)
    print(f"find_window_by_title_hint() par défaut : {result!r} (pas de crash).")


if __name__ == "__main__":
    test_get_primary_resolution_returns_sane_dimensions()
    test_list_monitors_finds_at_least_one_with_exactly_one_primary()
    test_find_window_by_title_hint_returns_none_for_nonexistent_window()
    test_find_window_by_title_hint_does_not_crash_with_default_hint()
    print("\nTous les tests passent.")
