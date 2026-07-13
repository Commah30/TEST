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

EN: engine.py — core of the rotation engine.
EN:
EN: It uses a custom YAML dialect (name, priority_st/priority_aoe, condition,
EN: cooldown_ms, off_gcd, skip_detection, requires_ready) so that Warden.yaml
EN: is reusable almost as-is.
EN:
EN: pre_tick_hook / post_tick_hook are not function names to find in a closed
EN: binary. They are Python callables that YOU write in hooks.py and pass
EN: explicitly to RotationEngine(). You literally see and control every line
EN: that decides what to press -- no more "I think the hook does that".
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import yaml


# =============================================================================
# État mutable du bot : dernier cast, cooldowns internes, flags, ressources,
# debuffs/buffs détectés, vie, etc. C'est ce que les hooks et les conditions
# lisent/écrivent à chaque tick.
# EN: Mutable bot state: last cast, internal cooldowns, flags, resources,
# EN: detected debuffs/buffs, health, etc. This is what the hooks and
# EN: conditions read/write every tick.
# =============================================================================
@dataclass
class State:
    last_cast: Optional[str] = None
    last_cast_time: float = 0.0
    cast_history: list = field(default_factory=list)   # plus récent en dernier, max 10 · EN: most recent last, max 10
    cooldown_until: dict = field(default_factory=dict)  # name -> timestamp
    gcd_until: float = 0.0
    hard_lock_until: float = 0.0    # bloque TOUTE ability, même off_gcd -- pour les canalisations qui ne doivent jamais être coupées (ex. Hammer Storm côté Tariq) · EN: blocks EVERY ability, even off_gcd -- for channels that must never be interrupted (e.g. Hammer Storm on Tariq)
    flags: dict = field(default_factory=dict)           # name -> bool, posés par TES hooks · EN: set by YOUR hooks
    debuffs: dict = field(default_factory=dict)         # name -> bool, posés par la détection · EN: set by detection
    buffs: dict = field(default_factory=dict)           # name -> bool
    resources: dict = field(default_factory=dict)       # name -> 0.0-1.0
    health_pct: float = 100.0
    mode: str = "st"                                    # "st"/"cleave"/"aoe", fixé par toi (pas de comptage auto) · EN: set by you (no auto target counting)
    # BUG TROUVÉ EN JEU (2026-07-12) : 6s+ sans une seule presse alors qu'une
    # reproduction isolée avec les MÊMES valeurs (fury, buffs, cooldowns tirés
    # du log réel) fait choisir skull_crusher sans problème -- la logique de
    # _pick_ability est correcte en soi, mais impossible de savoir CE QUE le
    # bot en train de tourner voyait réellement à cet instant précis sans
    # cette trace. Rempli par _pick_ability à chaque appel (écrasé, pas
    # cumulé) -- une paire (nom, raison) par ability qui n'a pas été retenue,
    # dans l'ordre de priorité. Lu par make_debug_logger (main.py) UNIQUEMENT
    # quand pressed est None, pour ne pas bruiter le log le reste du temps.
    # EN: BUG FOUND IN-GAME (2026-07-12): 6s+ with not a single press while an
    # EN: isolated repro with the SAME values (fury, buffs, cooldowns taken from
    # EN: the real log) picks skull_crusher without a problem -- the
    # EN: _pick_ability logic is correct in itself, but impossible to know WHAT
    # EN: the running bot actually saw at that precise instant without this
    # EN: trace. Filled by _pick_ability on each call (overwritten, not
    # EN: accumulated) -- one (name, reason) pair per ability not selected, in
    # EN: priority order. Read by make_debug_logger (main.py) ONLY when pressed
    # EN: is None, so as not to spam the log the rest of the time.
    debug_skip_trace: list = field(default_factory=list)
    # AUDIT 2026-07-14 : debug_skip_trace répond à "pourquoi PAS X" (seulement
    # quand rien n'est pressé) mais rien ne répondait à "pourquoi X A gagné"
    # quand une ability EST choisie -- trou d'observabilité identifié dans
    # l'audit rotation/stabilité. Rempli par _pick_ability juste avant de
    # retourner une ability retenue (écrasé, pas cumulé) ; lu par
    # make_debug_logger (main.py) quand pressed n'est PAS None.
    # EN: AUDIT 2026-07-14: debug_skip_trace answers "why NOT X" (only when
    # EN: nothing is pressed) but nothing answered "why did X WIN" when an
    # EN: ability IS chosen -- an observability gap identified in the
    # EN: rotation/stability audit. Filled by _pick_ability just before
    # EN: returning a selected ability (overwritten, not accumulated); read by
    # EN: make_debug_logger (main.py) when pressed is NOT None.
    debug_choice_reason: str = ""

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
# EN: One ability entry as defined in the YAML.
# =============================================================================
@dataclass
class Ability:
    name: str
    priority_st: int = 99
    priority_aoe: int = 99
    priority_cleave: int = 99
    condition: str = ""
    condition_st: str = ""       # si non-vide, prime sur `condition` en mode "st" · EN: if non-empty, overrides `condition` in "st" mode
    condition_cleave: str = ""   # idem en mode "cleave" · EN: same in "cleave" mode
    condition_aoe: str = ""      # idem en mode "aoe" · EN: same in "aoe" mode
    cooldown_ms: int = 0
    off_gcd: bool = False
    skip_detection: bool = False
    requires_ready: Optional[str] = None
    template: Optional[str] = None  # nom du template PNG ; défaut = name · EN: PNG template name; default = name
    confidence_threshold: Optional[float] = None  # seuil custom pour CETTE ability (défaut : 0.70, cf. matcher.py) · EN: custom threshold for THIS ability (default: 0.70, cf. matcher.py)

    # --- Champs importés du YAML Tariq (Schism V2), acceptés pour que le
    # fichier charge, mais PAS ENCORE câblés dans le moteur -- sémantique à
    # confirmer avant d'y toucher pour de vrai (cf. discussion) :
    # EN: --- Fields imported from the Tariq YAML (Schism V2), accepted so the
    # EN: file loads, but NOT YET wired into the engine -- semantics to confirm
    # EN: before really touching them (cf. discussion):
    gcd_ms: Optional[int] = None   # override du GCD global pour cette ability -- ignoré pour l'instant (risque de rejouer le bug "spam" si mal interprété, cf. CLAUDE.md bug #1 -- à clarifier avec l'utilisateur) · EN: per-ability override of the global GCD -- ignored for now (risk of replaying the "spam" bug if misinterpreted, cf. CLAUDE.md bug #1 -- to clarify with l'utilisateur)
    is_proc: bool = False          # métadonnée -- aucun effet moteur pour l'instant · EN: metadata -- no engine effect for now
    brightness_min: Optional[float] = None  # détection par luminosité plutôt que template -- méthode pas encore implémentée (même famille que le futur travail tesseract/pixel-scan) · EN: brightness-based detection rather than template -- method not implemented yet (same family as the future tesseract/pixel-scan work)
    blind_sweep: bool = False      # censé bypasser un gate "cible requise" -- ce gate n'existe pas encore dans le moteur, donc champ inerte pour l'instant · EN: meant to bypass a "target required" gate -- that gate doesn't exist in the engine yet, so this field is inert for now

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
    """Charge les abilities + la config générale (gcd_ms, hooks déclarés, etc.).

    EN: Loads the abilities + the general config (gcd_ms, declared hooks, etc.).
    """
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
# EN: Condition evaluation -- tokens separated by commas (AND), prefix "not "
# EN: for negation.
# EN:   last:X            -> state.last_cast == X
# EN:   flag:X             -> state.flags[X] is true (set by YOUR hooks)
# EN:   debuff:X           -> state.debuffs[X] is true (set by detection)
# EN:   buff:X             -> state.buffs[X] is true
# EN:   resource_full       -> state.resources["primary"] >= 0.999
# EN:   resource_above:N    -> state.resources["primary"]*100 >= N
# EN:   fury_above:N        -> state.resources["fury"]*100 >= N
# EN:   health_below:N / health_above:N
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
        raise ValueError(f"Condition inconnue : {token!r}")  # EN: Unknown condition

    return (not result) if negate else result


def evaluate_condition(condition: str, state: State) -> bool:
    if not condition:
        return True
    return all(_eval_token(t, state) for t in condition.split(","))


# =============================================================================
# Le moteur lui-même.
# EN: The engine itself.
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

        EN: press_fn(ability_name)       -> actually sends the key
        EN: is_ready_fn(ability_name, confidence_threshold=None)
        EN:     -> True if the template matches (ignored if skip_detection).
        EN:     threshold None -> the implementation uses its default (0.70 in
        EN:     matcher.py).
        EN: state_update_fn(state)
        EN:     Called first of all, BEFORE everything else, every tick. Used to
        EN:     read the screen and fill state.resources/state.debuffs/state.buffs
        EN:     (Stormfall/Earthfall charges, Warden of the Temple x3 debuff,
        EN:     etc.) -- without it, nothing updates these values automatically.
        EN: pre_tick_hook(state) -> Optional[str]
        EN:     Called AFTER state_update_fn, BEFORE the priority scan. If it
        EN:     returns an ability name, THAT name is pressed directly this tick,
        EN:     the normal scan is skipped. Used to complete an in-progress combo
        EN:     (equivalent to meiko_combo_pre_tick, but in plain code).
        EN: post_tick_hook(state, pressed_name)
        EN:     Called AFTER a key has been pressed (by the hook or the scan).
        EN:     Used to update flags/timers (equivalent to meiko_combo_post_tick).
        EN: off_gcd_hook_abilities
        EN:     Names pressed by pre_tick_hook that do NOT set a new GCD (e.g.
        EN:     the finisher, off-GCD by nature). Everything else pressed by the
        EN:     hook sets a normal GCD, like a regular ability.
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

    def _safe_is_ready(self, *args) -> "tuple[bool, Optional[str]]":
        """Appelle is_ready_fn en isolant ses exceptions. Retourne
        (ready, err_msg) : si is_ready_fn LÈVE (typiquement une capture
        d'écran transitoire qui hoquette -- ScreenShotError/BitBlt, bascule
        plein écran du jeu...), on renvoie (False, message) pour que le scan
        SAUTE cette ability et CONTINUE vers les priorités inférieures.

        BUG TROUVÉ EN JEU (2026-07-14, analyse vidéo + logs Tariq) : sans ce
        garde-fou, une exception dans is_ready_fn remontait à travers
        _pick_ability et tuait TOUT le tick (rattrapée seulement tout en haut
        par run_forever) -- donc dès que le scan atteignait face_breaker (la
        SEULE ability Tariq sans skip_detection, donc la seule qui déclenche
        une capture ici), un simple hoquet de capture empêchait wild_swing
        (priorité inférieure) de jamais être évalué. Symptôme : la rotation
        "gèle" plusieurs secondes une fois heavy_strike/procs indisponibles,
        face_breaker ET wild_swing ne partent jamais. Une détection qui échoue
        sur UNE ability ne doit dégrader QUE cette ability, pas figer toute la
        rotation -- même principe que les autres garde-fous du projet
        (run_forever, worker de détection).

        EN: Calls is_ready_fn while isolating its exceptions. Returns
        EN: (ready, err_msg): if is_ready_fn RAISES (typically a transient
        EN: screen capture that hiccups -- ScreenShotError/BitBlt, game
        EN: fullscreen switch...), we return (False, message) so the scan SKIPS
        EN: this ability and CONTINUES toward lower priorities.
        EN:
        EN: BUG FOUND IN-GAME (2026-07-14, video + Tariq logs analysis): without
        EN: this guardrail, an exception in is_ready_fn propagated through
        EN: _pick_ability and killed the WHOLE tick (caught only at the very top
        EN: by run_forever) -- so as soon as the scan reached face_breaker (the
        EN: ONLY Tariq ability without skip_detection, thus the only one that
        EN: triggers a capture here), a simple capture hiccup prevented
        EN: wild_swing (lower priority) from ever being evaluated. Symptom: the
        EN: rotation "freezes" several seconds once heavy_strike/procs are
        EN: unavailable, face_breaker AND wild_swing never fire. A detection that
        EN: fails on ONE ability must degrade ONLY that ability, not freeze the
        EN: whole rotation -- same principle as the project's other guardrails
        EN: (run_forever, detection worker).
        """
        try:
            return bool(self.is_ready_fn(*args)), None
        except Exception as exc:
            return False, f"detection a levé {type(exc).__name__} -- ability sautée ce tick"  # EN: detection raised ... -- ability skipped this tick

    def _pick_ability(self, now: float) -> Optional[Ability]:
        self.state.debug_skip_trace = []
        if now < self.state.hard_lock_until:
            self.state.debug_skip_trace.append(("*", f"hard_lock actif encore {self.state.hard_lock_until - now:.3f}s"))  # EN: hard_lock active for another ...s
            return None
        candidates = sorted(self.abilities, key=lambda a: a.priority(self.state.mode))
        for ab in candidates:
            if not ab.off_gcd and now < self.state.gcd_until:
                self.state.debug_skip_trace.append((ab.name, f"gcd actif encore {self.state.gcd_until - now:.3f}s"))  # EN: gcd active for another ...s
                continue
            if self.state.is_on_cooldown(ab.name, now):
                self.state.debug_skip_trace.append((ab.name, f"cooldown actif encore {self.state.cooldown_until.get(ab.name, 0.0) - now:.3f}s"))  # EN: cooldown active for another ...s
                continue
            if ab.requires_ready:
                ready, err = self._safe_is_ready(ab.requires_ready)
                if err is not None:
                    self.state.debug_skip_trace.append((ab.name, err))
                    continue
                if not ready:
                    self.state.debug_skip_trace.append((ab.name, f"requires_ready:{ab.requires_ready} pas pret"))  # EN: requires_ready:... not ready
                    continue
            if not evaluate_condition(ab.condition_for(self.state.mode), self.state):
                self.state.debug_skip_trace.append((ab.name, f"condition fausse: {ab.condition_for(self.state.mode)!r}"))  # EN: condition false: ...
                continue
            if not ab.skip_detection:
                ready, err = self._safe_is_ready(ab.template_name(), ab.confidence_threshold)
                if err is not None:
                    self.state.debug_skip_trace.append((ab.name, err))
                    continue
                if not ready:
                    self.state.debug_skip_trace.append((ab.name, "template ne matche pas"))  # EN: template does not match
                    continue
            # AUDIT 2026-07-14 : les entrées déjà accumulées dans debug_skip_trace
            # à ce stade sont TOUTES les abilities de priorité plus haute (donc
            # évaluées avant celle-ci dans ce même scan) qui ont échoué --
            # capturées ICI, avant de vider la trace, pour expliquer pourquoi
            # `ab` a gagné plutôt qu'elles.
            # EN: AUDIT 2026-07-14: the entries already accumulated in
            # EN: debug_skip_trace at this point are ALL the higher-priority
            # EN: abilities (thus evaluated before this one in the same scan)
            # EN: that failed -- captured HERE, before clearing the trace, to
            # EN: explain why `ab` won rather than them.
            priority = ab.priority(self.state.mode)
            if self.state.debug_skip_trace:
                competitor_name, competitor_reason = self.state.debug_skip_trace[0]
                self.state.debug_choice_reason = (
                    f"priority={priority}, devançait {competitor_name} ({competitor_reason})"  # EN: priority=..., beat <competitor> (<reason>)
                    + (f" +{len(self.state.debug_skip_trace) - 1} autre(s)" if len(self.state.debug_skip_trace) > 1 else "")  # EN: +N other(s)
                )
            else:
                self.state.debug_choice_reason = f"priority={priority}, aucune ability plus prioritaire en lice"  # EN: priority=..., no higher-priority ability in the running
            self.state.debug_skip_trace = []   # une ability a été retenue -- pas de trace à afficher · EN: an ability was selected -- no trace to display
            return ab
        return None

    def tick(self) -> Optional[str]:
        """Une itération du moteur. Retourne le nom pressé, ou None.

        EN: One engine iteration. Returns the pressed name, or None.
        """
        now = time.monotonic()

        if self.state_update_fn is not None:
            self.state_update_fn(self.state)

        pressed = None

        if self.pre_tick_hook is not None:
            # Tourne MÊME sous hard_lock -- c'est lui qui détecte la fin de
            # la canalisation et lève le verrou (cf. tariq_pre_tick). Le
            # verrou n'empêche que la PRESSE, pas la détection.
            # EN: Runs EVEN under hard_lock -- it's the hook that detects the end
            # EN: of the channel and releases the lock (cf. tariq_pre_tick). The
            # EN: lock only prevents the PRESS, not detection.
            pressed = self.pre_tick_hook(self.state)

        if now < self.state.hard_lock_until:
            # Canalisation en cours (ex. Hammer Storm) -- bloque TOUTE presse
            # ce tick, même une ability off_gcd ou une décision du hook.
            # EN: Channel in progress (e.g. Hammer Storm) -- blocks EVERY press
            # EN: this tick, even an off_gcd ability or a hook decision.
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
            # EN: pressed was decided by pre_tick_hook. We set a GCD like for any
            # EN: ability, EXCEPT for those listed as off-GCD (the finisher) --
            # EN: without it, the combo's 2nd piece blocked nothing and the hook
            # EN: chained far too fast relative to the real game (cf. the "spam
            # EN: Wind Kick" bug).
            self.press_fn(pressed)
            self.state.record_cast(pressed, now, 0)
            self.state.debug_choice_reason = "décidé par pre_tick_hook (court-circuite le scan de priorité)"  # EN: decided by pre_tick_hook (short-circuits the priority scan)
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
        on_error: Optional[Callable[[BaseException], None]] = None,
        stop_event: Optional[threading.Event] = None,
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

        on_error(exc) : appelé si self.tick() lève une exception (frame de
        capture invalide, glitch matcher/YOLO, etc.) -- SANS ça, une seule
        exception dans un tick tuerait tout le bot en pleine partie (rien ne
        rattrapait KeyboardInterrupt à part le Ctrl+C). Le tick fautif est
        traité comme "rien pressé" (pressed=None) et la boucle continue au
        tick suivant, plutôt que de planter tout le process pour un incident
        probablement transitoire (capture d'écran, inférence GPU). Si
        non fourni, imprime juste la trace (comportement visible en console,
        pas de crash silencieux).

        stop_event (2026-07-15, cf. app.py::BotController -- GUI desktop) :
        Event externe optionnel. S'il est fourni et déjà "set", la boucle
        sort proprement à la fin de l'itération en cours -- seule alternative
        à Ctrl+C (KeyboardInterrupt) pour un appelant qui pilote ce thread
        depuis une UI (bouton Stop), où lever un KeyboardInterrupt inter-
        thread n'est pas fiable. None (défaut) = comportement STRICTEMENT
        inchangé pour main.py (qui ne passe jamais stop_event).

        EN: Main loop. tick_hz = evaluation frequency (not press frequency).
        EN: on_tick(pressed_name_or_None) is called after each tick, if provided
        EN: -- used e.g. to plug in a debug logger without duplicating the loop.
        EN: is_paused() -> bool, if provided and true, completely skips
        EN: self.tick() for that iteration -- no key pressed, no state modified
        EN: (full pause, not just a no-op on press_fn that would let the combo's
        EN: internal state drift from the real game). on_tick keeps being called
        EN: even while paused -- used e.g. to read a resume key.
        EN:
        EN: on_error(exc): called if self.tick() raises an exception (invalid
        EN: capture frame, matcher/YOLO glitch, etc.) -- WITHOUT it, a single
        EN: exception in one tick would kill the whole bot mid-game (nothing
        EN: caught it except Ctrl+C). The faulty tick is treated as "nothing
        EN: pressed" (pressed=None) and the loop continues at the next tick,
        EN: rather than crashing the whole process for a probably-transient
        EN: incident (screen capture, GPU inference). If not provided, it just
        EN: prints the traceback (visible in console, no silent crash).
        EN:
        EN: stop_event (2026-07-15, cf. app.py::BotController -- desktop GUI):
        EN: Optional external Event. If provided and already "set", the loop
        EN: exits cleanly at the end of the current iteration -- the only
        EN: alternative to Ctrl+C (KeyboardInterrupt) for a caller driving this
        EN: thread from a UI (Stop button), where raising a cross-thread
        EN: KeyboardInterrupt is unreliable. None (default) = STRICTLY unchanged
        EN: behavior for main.py (which never passes stop_event).
        """
        period = 1.0 / tick_hz
        try:
            while stop_event is None or not stop_event.is_set():
                start = time.monotonic()
                if is_paused is not None and is_paused():
                    pressed = None
                else:
                    try:
                        pressed = self.tick()
                    except Exception as exc:
                        pressed = None
                        if on_error is not None:
                            on_error(exc)
                        else:
                            traceback.print_exc()
                if on_tick is not None:
                    on_tick(pressed)
                elapsed = time.monotonic() - start
                time.sleep(max(0.0, period - elapsed))
        except KeyboardInterrupt:
            print("Arrêt demandé (Ctrl+C).")  # EN: Stop requested (Ctrl+C).
