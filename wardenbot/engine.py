"""
engine.py — coeur du moteur de rotation.

Reprend un dialecte YAML custom (name, priority_st/priority_aoe, condition,
cooldown_ms, off_gcd, skip_detection, requires_ready) pour que Warden.yaml
soit réutilisable quasi tel quel.

pre_tick_hook / post_tick_hook ne sont pas des noms de fonctions à
retrouver dans un binaire fermé. Ce sont des callables Python que TU écris
dans hooks.py et que tu passes explicitement à RotationEngine(). Tu vois et
contrôles littéralement chaque ligne qui décide quoi presser -- plus de "je
pense que le hook fait ça".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import yaml


# =============================================================================
# État mutable du bot : dernier cast, cooldowns internes, flags, ressources,
# debuffs/buffs détectés, vie, etc. C'est ce que les hooks et les conditions
# lisent/écrivent à chaque tick.
# =============================================================================
@dataclass
class State:
    last_cast: Optional[str] = None
    last_cast_time: float = 0.0
    cast_history: list = field(default_factory=list)   # plus récent en dernier, max 10
    cooldown_until: dict = field(default_factory=dict)  # name -> timestamp
    gcd_until: float = 0.0
    hard_lock_until: float = 0.0    # bloque TOUTE ability, même off_gcd -- pour les canalisations qui ne doivent jamais être coupées (ex. Hammer Storm côté Tariq)
    flags: dict = field(default_factory=dict)           # name -> bool, posés par TES hooks
    debuffs: dict = field(default_factory=dict)         # name -> bool, posés par la détection
    buffs: dict = field(default_factory=dict)           # name -> bool
    resources: dict = field(default_factory=dict)       # name -> 0.0-1.0
    health_pct: float = 100.0
    mode: str = "st"                                    # "st"/"cleave"/"aoe", fixé par toi (pas de comptage auto)
    # BUG TROUVÉ EN JEU (2026-07-12) : 6s+ sans une seule presse alors qu'une
    # reproduction isolée avec les MÊMES valeurs (fury, buffs, cooldowns tirés
    # du log réel) fait choisir skull_crusher sans problème -- la logique de
    # _pick_ability est correcte en soi, mais impossible de savoir CE QUE le
    # bot en train de tourner voyait réellement à cet instant précis sans
    # cette trace. Rempli par _pick_ability à chaque appel (écrasé, pas
    # cumulé) -- une paire (nom, raison) par ability qui n'a pas été retenue,
    # dans l'ordre de priorité. Lu par make_debug_logger (main.py) UNIQUEMENT
    # quand pressed est None, pour ne pas bruiter le log le reste du temps.
    debug_skip_trace: list = field(default_factory=list)

    def record_cast(self, name: str, now: float, cooldown_ms: int):
        self.last_cast = name
        self.last_cast_time = now
        self.cast_history.append(name)
        self.cast_history = self.cast_history[-10:]
        if cooldown_ms > 0:
            self.cooldown_until[name] = now + cooldown_ms / 1000.0

    def is_on_cooldown(self, name: str, now: float) -> bool:
        return now < self.cooldown_until.get(name, 0.0)


# =============================================================================
# Une entrée d'ability telle que définie dans le YAML.
# =============================================================================
@dataclass
class Ability:
    name: str
    priority_st: int = 99
    priority_aoe: int = 99
    priority_cleave: int = 99
    condition: str = ""
    condition_st: str = ""       # si non-vide, prime sur `condition` en mode "st"
    condition_cleave: str = ""   # idem en mode "cleave"
    condition_aoe: str = ""      # idem en mode "aoe"
    cooldown_ms: int = 0
    off_gcd: bool = False
    skip_detection: bool = False
    requires_ready: Optional[str] = None
    template: Optional[str] = None  # nom du template PNG ; défaut = name
    confidence_threshold: Optional[float] = None  # seuil custom pour CETTE ability (défaut : 0.70, cf. matcher.py)

    # --- Champs importés du YAML Tariq (Schism V2), acceptés pour que le
    # fichier charge, mais PAS ENCORE câblés dans le moteur -- sémantique à
    # confirmer avant d'y toucher pour de vrai (cf. discussion) :
    gcd_ms: Optional[int] = None   # override du GCD global pour cette ability -- ignoré pour l'instant (risque de rejouer le bug "spam" si mal interprété, cf. CLAUDE.md bug #1 -- à clarifier avec l'utilisateur)
    is_proc: bool = False          # métadonnée -- aucun effet moteur pour l'instant
    brightness_min: Optional[float] = None  # détection par luminosité plutôt que template -- méthode pas encore implémentée (même famille que le futur travail tesseract/pixel-scan)
    blind_sweep: bool = False      # censé bypasser un gate "cible requise" -- ce gate n'existe pas encore dans le moteur, donc champ inerte pour l'instant

    def priority(self, mode: str) -> int:
        if mode == "cleave":
            return self.priority_cleave
        return self.priority_aoe if mode == "aoe" else self.priority_st

    def condition_for(self, mode: str) -> str:
        per_mode = {"st": self.condition_st, "cleave": self.condition_cleave, "aoe": self.condition_aoe}
        return per_mode.get(mode, "") or self.condition

    def template_name(self) -> str:
        return self.template or self.name


def load_abilities(yaml_path: str) -> tuple[list[Ability], dict]:
    """Charge les abilities + la config générale (gcd_ms, hooks déclarés, etc.)."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    abilities = [Ability(**a) for a in raw.get("abilities", [])]
    meta = {k: v for k, v in raw.items() if k != "abilities"}
    return abilities, meta


# =============================================================================
# Évaluation des conditions -- tokens
# séparés par des virgules (AND), préfixe "not " pour la négation.
#   last:X            -> state.last_cast == X
#   flag:X             -> state.flags[X] est vrai (posé par TES hooks)
#   debuff:X           -> state.debuffs[X] est vrai (posé par la détection)
#   buff:X             -> state.buffs[X] est vrai
#   resource_full       -> state.resources["primary"] >= 0.999
#   resource_above:N    -> state.resources["primary"]*100 >= N
#   fury_above:N        -> state.resources["fury"]*100 >= N
#   health_below:N / health_above:N
# =============================================================================
def _eval_token(token: str, state: State) -> bool:
    token = token.strip()
    negate = token.startswith("not ")
    if negate:
        token = token[4:].strip()

    key, _, value = token.partition(":")

    if key == "last":
        result = state.last_cast == value
    elif key == "flag":
        result = bool(state.flags.get(value, False))
    elif key == "debuff":
        result = bool(state.debuffs.get(value, False))
    elif key == "buff":
        result = bool(state.buffs.get(value, False))
    elif key == "resource_full":
        result = state.resources.get("primary", 0.0) >= 0.999
    elif key == "resource_above":
        result = state.resources.get("primary", 0.0) * 100 >= float(value)
    elif key == "fury_above":
        result = state.resources.get("fury", 0.0) * 100 >= float(value)
    elif key == "health_below":
        result = state.health_pct < float(value)
    elif key == "health_above":
        result = state.health_pct > float(value)
    else:
        raise ValueError(f"Condition inconnue : {token!r}")

    return (not result) if negate else result


def evaluate_condition(condition: str, state: State) -> bool:
    if not condition:
        return True
    return all(_eval_token(t, state) for t in condition.split(","))


# =============================================================================
# Le moteur lui-même.
# =============================================================================
class RotationEngine:
    def __init__(
        self,
        abilities: list[Ability],
        state: State,
        press_fn: Callable[[str], None],
        is_ready_fn: Callable[..., bool],
        gcd_ms: int = 1100,
        pre_tick_hook: Optional[Callable[[State], Optional[str]]] = None,
        post_tick_hook: Optional[Callable[[State, str], None]] = None,
        state_update_fn: Optional[Callable[[State], None]] = None,
        off_gcd_hook_abilities: frozenset = frozenset({"fighting_techniques"}),
    ):
        """
        press_fn(ability_name)       -> envoie réellement la touche
        is_ready_fn(ability_name, confidence_threshold=None)
            -> True si le template matche (ignoré si skip_detection). threshold
            à None -> l'implémentation utilise son défaut (0.70 côté matcher.py).
        state_update_fn(state)
            Appelé en tout premier, AVANT tout le reste, à chaque tick. Sert à
            lire l'écran et remplir state.resources/state.debuffs/state.buffs
            (charges Stormfall/Earthfall, debuff Warden of the Temple x3,
            etc.) -- sans ça, rien ne met ces valeurs à jour automatiquement.
        pre_tick_hook(state) -> Optional[str]
            Appelé APRÈS state_update_fn, AVANT le scan de priorité. Si elle
            retourne un nom d'ability, CE nom est pressé directement ce tick,
            le scan normal est sauté. Sert à compléter un combo en cours
            (équivalent à meiko_combo_pre_tick, mais en clair).
        post_tick_hook(state, pressed_name)
            Appelé APRÈS qu'une touche a été pressée (par le hook ou par le
            scan). Sert à mettre à jour flags/timers (équivalent à
            meiko_combo_post_tick).
        off_gcd_hook_abilities
            Noms pressés par pre_tick_hook qui ne posent PAS de nouveau GCD
            (ex: le finisher, off-GCD par nature). Tout le reste pressé par
            le hook pose un GCD normal, comme une ability classique.
        """
        self.abilities = abilities
        self.state = state
        self.press_fn = press_fn
        self.is_ready_fn = is_ready_fn
        self.gcd_ms = gcd_ms
        self.pre_tick_hook = pre_tick_hook
        self.post_tick_hook = post_tick_hook
        self.state_update_fn = state_update_fn
        self.off_gcd_hook_abilities = off_gcd_hook_abilities

    def _pick_ability(self, now: float) -> Optional[Ability]:
        self.state.debug_skip_trace = []
        if now < self.state.hard_lock_until:
            self.state.debug_skip_trace.append(("*", f"hard_lock actif encore {self.state.hard_lock_until - now:.3f}s"))
            return None
        candidates = sorted(self.abilities, key=lambda a: a.priority(self.state.mode))
        for ab in candidates:
            if not ab.off_gcd and now < self.state.gcd_until:
                self.state.debug_skip_trace.append((ab.name, f"gcd actif encore {self.state.gcd_until - now:.3f}s"))
                continue
            if self.state.is_on_cooldown(ab.name, now):
                self.state.debug_skip_trace.append((ab.name, f"cooldown actif encore {self.state.cooldown_until.get(ab.name, 0.0) - now:.3f}s"))
                continue
            if ab.requires_ready and not self.is_ready_fn(ab.requires_ready):
                self.state.debug_skip_trace.append((ab.name, f"requires_ready:{ab.requires_ready} pas pret"))
                continue
            if not evaluate_condition(ab.condition_for(self.state.mode), self.state):
                self.state.debug_skip_trace.append((ab.name, f"condition fausse: {ab.condition_for(self.state.mode)!r}"))
                continue
            if not ab.skip_detection and not self.is_ready_fn(ab.template_name(), ab.confidence_threshold):
                self.state.debug_skip_trace.append((ab.name, "template ne matche pas"))
                continue
            self.state.debug_skip_trace = []   # une ability a été retenue -- pas de trace à afficher
            return ab
        return None

    def tick(self) -> Optional[str]:
        """Une itération du moteur. Retourne le nom pressé, ou None."""
        now = time.monotonic()

        if self.state_update_fn is not None:
            self.state_update_fn(self.state)

        pressed = None

        if self.pre_tick_hook is not None:
            # Tourne MÊME sous hard_lock -- c'est lui qui détecte la fin de
            # la canalisation et lève le verrou (cf. tariq_pre_tick). Le
            # verrou n'empêche que la PRESSE, pas la détection.
            pressed = self.pre_tick_hook(self.state)

        if now < self.state.hard_lock_until:
            # Canalisation en cours (ex. Hammer Storm) -- bloque TOUTE presse
            # ce tick, même une ability off_gcd ou une décision du hook.
            pressed = None

        if pressed is None:
            ability = self._pick_ability(now)
            if ability is not None:
                pressed = ability.name
                cooldown_ms = ability.cooldown_ms
                self.press_fn(pressed)
                self.state.record_cast(pressed, now, cooldown_ms)
                if not ability.off_gcd:
                    self.state.gcd_until = now + self.gcd_ms / 1000.0
        else:
            # pressed a été décidé par le pre_tick_hook. On pose un GCD
            # comme pour n'importe quelle ability, SAUF pour celles listées
            # comme off-GCD (le finisher) -- sans ça, la 2e pièce du combo
            # ne bloquait rien et le hook enchaînait bien trop vite par
            # rapport au vrai jeu (cf. le bug "spam Wind Kick").
            self.press_fn(pressed)
            self.state.record_cast(pressed, now, 0)
            if pressed not in self.off_gcd_hook_abilities:
                self.state.gcd_until = now + self.gcd_ms / 1000.0

        if pressed is not None and self.post_tick_hook is not None:
            self.post_tick_hook(self.state, pressed)

        return pressed

    def run_forever(
        self,
        tick_hz: float = 30.0,
        on_tick: Optional[Callable[[Optional[str]], None]] = None,
        is_paused: Optional[Callable[[], bool]] = None,
    ):
        """
        Boucle principale. tick_hz = fréquence d'évaluation (pas de press).
        on_tick(pressed_name_or_None) est appelé après chaque tick, si fourni
        -- sert par ex. à brancher un logger de debug sans dupliquer la boucle.
        is_paused() -> bool, si fourni et vrai, saute complètement self.tick()
        pour cette itération -- aucune touche pressée, aucun état modifié
        (pause totale, pas juste un no-op sur press_fn qui laisserait l'état
        interne du combo dériver du vrai jeu). on_tick continue d'être appelé
        même en pause -- sert par ex. à lire une touche de reprise.
        """
        period = 1.0 / tick_hz
        try:
            while True:
                start = time.monotonic()
                pressed = None if (is_paused is not None and is_paused()) else self.tick()
                if on_tick is not None:
                    on_tick(pressed)
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, period - elapsed))
        except KeyboardInterrupt:
            print("Arrêt demandé (Ctrl+C).")
