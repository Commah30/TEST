"""
engine.py — coeur du moteur de rotation.

Reprend un dialecte YAML custom (name, priorities, condition,
cooldown_ms, off_gcd, skip_detection, requires_ready) pour que Warden.yaml
soit réutilisable quasi tel quel.

pre_tick_hook / post_tick_hook ne sont pas des noms de fonctions à
retrouver dans un binaire fermé. Ce sont des callables Python que TU écris
dans hooks.py et que tu passes explicitement à RotationEngine(). Tu vois et
contrôles littéralement chaque ligne qui décide quoi presser -- plus de "je
pense que le hook fait ça".

EN: engine.py — core of the rotation engine.
EN:
EN: It uses a custom YAML dialect (name, priorities, condition,
EN: cooldown_ms, off_gcd, skip_detection, requires_ready) so that Warden.yaml
EN: is reusable almost as-is.
EN:
EN: pre_tick_hook / post_tick_hook are not function names to find in a closed
EN: binary. They are Python callables that YOU write in hooks.py and pass
EN: explicitly to RotationEngine(). You literally see and control every line
EN: that decides what to press -- no more "I think the hook does that".
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from .freshness import DetectionMeta
from .telemetry import TelemetryWorker


# =============================================================================
# État mutable du bot : dernier cast, cooldowns internes, flags, ressources,
# debuffs/buffs détectés, vie, etc. C'est ce que les hooks et les conditions
# lisent/écrivent à chaque tick.
#
# SCISSION detection/hooks/planner (2026-07-14, migration mécanique --
# CombatState, cf. CLAUDE.md/discussion architecture) : remplace l'ancien
# `flags` (un seul dict fourre-tout, écrit à la fois par state_update.py ET
# hooks.py, avec pour seule garantie un commentaire "ne jamais écraser en
# bloc") par 3 dicts dont le PROPRIÉTAIRE EN ÉCRITURE est structurellement
# distinct. Migration purement mécanique : mêmes clés, mêmes valeurs, aucun
# changement de comportement -- seulement le tiroir dans lequel chaque clé
# vit désormais. `planner` n'est PAS vide : il reprend dès maintenant
# tariq_objective/burst_hold/fury_conserve (_update_tariq_planner, hooks.py),
# qui existent déjà en prod.
# EN: Mutable bot state: last cast, internal cooldowns, flags, resources,
# EN: detected debuffs/buffs, health, etc. This is what the hooks and
# EN: conditions read/write every tick.
# EN:
# EN: detection/hooks/planner SPLIT (2026-07-14, mechanical migration --
# EN: CombatState, cf. CLAUDE.md/architecture discussion): replaces the former
# EN: `flags` (a single catch-all dict, written by BOTH state_update.py AND
# EN: hooks.py, with only a comment "never overwrite wholesale" as a
# EN: guarantee) with 3 dicts whose WRITE OWNER is structurally distinct.
# EN: Purely mechanical migration: same keys, same values, no behavior change
# EN: -- only which drawer each key now lives in. `planner` is NOT empty: it
# EN: already holds tariq_objective/burst_hold/fury_conserve
# EN: (_update_tariq_planner, hooks.py), which already exist in production.
# =============================================================================
@dataclass
class CombatState:
    last_cast: Optional[str] = None
    last_cast_time: float = 0.0
    cast_history: list = field(default_factory=list)   # plus récent en dernier, max 10 · EN: most recent last, max 10
    cooldown_until: dict = field(default_factory=dict)  # name -> timestamp
    gcd_until: float = 0.0
    hard_lock_until: float = 0.0    # bloque TOUTE ability, même off_gcd -- pour les canalisations qui ne doivent jamais être coupées (ex. Hammer Storm côté Tariq) · EN: blocks EVERY ability, even off_gcd -- for channels that must never be interrupted (e.g. Hammer Storm on Tariq)
    # name -> bool/float, écrit UNIQUEMENT par state_update_fn (threads de
    # détection : YOLO/OCR/template match/bar_reader) -- jamais par les hooks.
    # EN: name -> bool/float, written ONLY by state_update_fn (detection
    # EN: threads: YOLO/OCR/template match/bar_reader) -- never by the hooks.
    detection: dict = field(default_factory=dict)
    # source (str, ex: "meiko_yolo"/"meiko_ocr"/"tariq_yolo"/"tariq_bars") ->
    # DetectionMeta (cf. wardenbot/freshness.py) -- horodatage de la DERNIÈRE
    # publication réelle de CETTE source, PAS les valeurs détectées
    # elles-mêmes (qui restent dans `detection` ci-dessus, inchangé). Dict
    # SÉPARÉ plutôt qu'un champ "age_ms" mélangé dans `detection` : ce
    # seraient deux propriétaires différents (state_update.py écrit les
    # valeurs ET leur fraîcheur, mais ce sont deux natures d'information
    # distinctes -- "ce qui a été vu" vs "à quel point c'est encore fiable")
    # dans un même dict, exactement le genre de mélange que la scission
    # detection/hooks/planner a corrigé une fois déjà (cf. plus haut).
    # Rien ne LIT encore ce champ pour décider quoi que ce soit (Phase 0,
    # 2026-07-14, cf. discussion) -- seulement mesuré et classifiable via
    # wardenbot/freshness.py::FreshnessPolicy.classify(), jamais encore câblé
    # dans hooks.py.
    # EN: source (str, e.g. "meiko_yolo"/"meiko_ocr"/"tariq_yolo"/
    # EN: "tariq_bars") -> DetectionMeta (cf. wardenbot/freshness.py) --
    # EN: timestamp of the LAST real publication of THIS source, NOT the
    # EN: detected values themselves (which stay in `detection` above,
    # EN: unchanged). SEPARATE dict rather than an "age_ms" field mixed into
    # EN: `detection`: these would be two different owners (state_update.py
    # EN: writes the values AND their freshness, but these are two distinct
    # EN: kinds of information -- "what was seen" vs "how reliable is it
    # EN: still") in the same dict, exactly the kind of mixing the
    # EN: detection/hooks/planner split already fixed once (cf. above).
    # EN: Nothing READS this field yet to decide anything (Phase 0,
    # EN: 2026-07-14, cf. discussion) -- only measured and classifiable via
    # EN: wardenbot/freshness.py::FreshnessPolicy.classify(), not yet wired
    # EN: into hooks.py.
    detection_meta: "dict[str, DetectionMeta]" = field(default_factory=dict)
    # name -> bool/int/float/str, mémoire des hooks pre/post-tick (hooks.py) :
    # compteurs de debounce, marqueurs "en attente de confirmation", compteur
    # de stacks Warden of the Temple, etc.
    # EN: name -> bool/int/float/str, memory of the pre/post-tick hooks
    # EN: (hooks.py): debounce counters, "pending confirmation" markers, the
    # EN: Warden of the Temple stack counter, etc.
    hooks: dict = field(default_factory=dict)
    # name -> bool/str/float, intentions du Planner (_update_tariq_planner,
    # hooks.py) -- lues par les conditions YAML via planner:X, au même titre
    # que hooks:X, mais écrites par une couche de décision distincte (au-
    # dessus des priorités fixes), pas par le bookkeeping pre/post-tick.
    # EN: name -> bool/str/float, Planner intentions (_update_tariq_planner,
    # EN: hooks.py) -- read by YAML conditions via planner:X, just like
    # EN: hooks:X, but written by a separate decision layer (above the fixed
    # EN: priorities), not by the pre/post-tick bookkeeping.
    planner: dict = field(default_factory=dict)
    # active_sequence (str|None) / sequence_step (int) -- possédé EXCLUSIVEMENT
    # par RotationEngine._advance_sequence (le Sequence Tracker, cf. Sequence
    # plus bas) -- PAS par hooks.py. Volontairement PAS rangé dans `hooks` :
    # ce serait remélanger deux propriétaires différents dans un même dict
    # (exactement le problème que la scission detection/hooks/planner
    # ci-dessus vient de corriger) -- le moteur lui-même a maintenant besoin
    # de sa propre mémoire, distincte de celle des hooks Python (2026-07-14,
    # migration sequences).
    # EN: active_sequence (str|None) / sequence_step (int) -- owned EXCLUSIVELY
    # EN: by RotationEngine._advance_sequence (the Sequence Tracker, cf.
    # EN: Sequence below) -- NOT by hooks.py. Deliberately NOT stored in
    # EN: `hooks`: that would remix two different owners into the same dict
    # EN: (exactly the problem the detection/hooks/planner split above just
    # EN: fixed) -- the engine itself now needs its own memory, distinct from
    # EN: the Python hooks' (2026-07-14, sequences migration).
    sequences: dict = field(default_factory=dict)
    debuffs: dict = field(default_factory=dict)         # name -> bool, posés par la détection · EN: set by detection
    buffs: dict = field(default_factory=dict)           # name -> bool
    resources: dict = field(default_factory=dict)       # name -> 0.0-1.0
    # party (2026-07-15, Vigour) : int (slot 1-4) -> {"present": bool,
    # "hp_pct": Optional[float], "cleanse_needed": bool, "hot_active": bool}.
    # Champ DÉDIÉ plutôt que fourré dans `detection` ci-dessus : ce dernier
    # est documenté "name -> bool/float" (valeur scalaire par clé) --
    # une structure par-allié imbriquée y romprait la convention. Écrit
    # UNIQUEMENT par state_update.py::make_vigour_state_update_fn, même
    # discipline que buffs/debuffs/resources.
    # EN: party (2026-07-15, Vigour): int (slot 1-4) -> {"present": bool,
    # EN: "hp_pct": Optional[float], "cleanse_needed": bool, "hot_active": bool}.
    # EN: DEDICATED field rather than stuffed into `detection` above: the
    # EN: latter is documented as "name -> bool/float" (one scalar value per
    # EN: key) -- a nested per-ally structure would break that convention.
    # EN: Written ONLY by state_update.py::make_vigour_state_update_fn, same
    # EN: discipline as buffs/debuffs/resources.
    party: dict = field(default_factory=dict)
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
    # priorities/conditions : dict {mode: valeur}, PAS des champs fixes
    # priority_st/priority_aoe/priority_cleave (2026-07-26, généricité du
    # moteur) -- avant ça, exactement 3 noms de mode ("st"/"aoe"/"cleave")
    # étaient codés en dur dans le SCHÉMA de cette dataclass : ajouter un 4e
    # mode exigeait de modifier engine.py, pas juste le YAML. `priorities`
    # accepte désormais N'IMPORTE QUEL nom de mode librement défini dans le
    # YAML (cf. priority()/condition_for() ci-dessous) -- un mode absent du
    # dict retombe sur 99 (jamais prioritaire) plutôt que de lever une
    # KeyError, même comportement que l'ancien défaut par champ.
    # EN: priorities/conditions: dict {mode: value}, NOT fixed
    # EN: priority_st/priority_aoe/priority_cleave fields (2026-07-26, engine
    # EN: genericity) -- before this, exactly 3 mode names
    # EN: ("st"/"aoe"/"cleave") were hardcoded into this dataclass's SCHEMA:
    # EN: adding a 4th mode required editing engine.py, not just the YAML.
    # EN: `priorities` now accepts ANY mode name freely defined in the YAML
    # EN: (cf. priority()/condition_for() below) -- a mode absent from the
    # EN: dict falls back to 99 (never priority) rather than raising
    # EN: KeyError, same behavior as the old per-field default.
    priorities: dict = field(default_factory=dict)
    condition: str = ""
    conditions: dict = field(default_factory=dict)  # {mode: condition}, prime sur `condition` si présent pour ce mode · EN: {mode: condition}, overrides `condition` if present for that mode
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
        return self.priorities.get(mode, 99)

    def condition_for(self, mode: str) -> str:
        return self.conditions.get(mode, "") or self.condition

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
# sequences: -- bloc YAML de haut niveau, séparé de abilities: (2026-07-14,
# migration Phase 3, discussion architecture). Une séquence est un enchaînement
# STRICT d'actions : si sa condition d'entrée devient vraie, le Sequence
# Tracker (RotationEngine._advance_sequence) s'y engage et propose ses steps
# dans l'ordre, un par tick, plutôt que de laisser le scan de priorité YAML
# classique (abilities:) choisir librement.
#
# MIGRATION PROGRESSIVE, PAS UNE RÉÉCRITURE : un step de séquence propose un
# Intent de poids 80 (cf. Intent) -- volontairement ENTRE le scan de priorité
# (50) et le pre_tick_hook (90). Tant qu'un pre_tick_hook Python gère encore
# le même combo (cas de meiko_pre_tick aujourd'hui), il garde la main à
# chaque fois qu'il propose quelque chose : la séquence YAML "essaie" en
# parallèle, sans jamais pouvoir court-circuiter une logique Python encore en
# place. Elle ne prend RÉELLEMENT le relai que pour les ticks où le hook ne
# propose rien (aujourd'hui : la toute première ouverture de combo, cf.
# hooks.py). Couper la logique Python correspondante (pour que la séquence
# gagne vraiment tout le temps) est un choix VOLONTAIREMENT reporté à une
# phase ultérieure, une fois la séquence validée en conditions réelles.
# EN: sequences: -- top-level YAML block, separate from abilities: (2026-07-14,
# EN: Phase 3 migration, architecture discussion). A sequence is a STRICT
# EN: chain of actions: if its entry condition becomes true, the Sequence
# EN: Tracker (RotationEngine._advance_sequence) engages and proposes its
# EN: steps in order, one per tick, instead of letting the classic YAML
# EN: priority scan (abilities:) choose freely.
# EN:
# EN: PROGRESSIVE MIGRATION, NOT A REWRITE: a sequence step proposes an Intent
# EN: of weight 80 (cf. Intent) -- deliberately BETWEEN the priority scan (50)
# EN: and the pre_tick_hook (90). As long as a Python pre_tick_hook still
# EN: handles the same combo (meiko_pre_tick's case today), it keeps the hand
# EN: every time it proposes something: the YAML sequence "tries" in
# EN: parallel, never able to short-circuit Python logic still in place. It
# EN: only REALLY takes over on ticks where the hook proposes nothing (today:
# EN: the very first combo opener, cf. hooks.py). Cutting the corresponding
# EN: Python logic (so the sequence truly wins all the time) is a choice
# EN: DELIBERATELY deferred to a later phase, once the sequence is validated
# EN: under real conditions.
# =============================================================================
@dataclass
class SequenceStep:
    ability: str
    condition: str = ""   # évaluée par evaluate_condition (même grammaire que Ability.condition) -- vide = toujours vraie · EN: evaluated by evaluate_condition (same grammar as Ability.condition) -- empty = always true


@dataclass
class Sequence:
    name: str
    condition: str = ""   # condition d'ENTRÉE -- évaluée seulement tant qu'aucune instance de cette séquence n'est déjà active · EN: ENTRY condition -- evaluated only while no instance of this sequence is already active
    # accepté mais PAS ENCORE câblé dans le moteur (cf. Ability.gcd_ms/is_proc
    # ci-dessus, même famille de champs "acceptés pour que le YAML charge,
    # sémantique à confirmer avant d'y toucher") -- pensé pour empêcher toute
    # AUTRE source de gagner pendant qu'une séquence tourne, une fois que le
    # cutover (cf. docstring du bloc plus haut) rendra ça nécessaire.
    # EN: accepted but NOT YET wired into the engine (cf. Ability.gcd_ms/is_proc
    # EN: above, same family of fields "accepted so the YAML loads, semantics
    # EN: to confirm before touching") -- meant to prevent any OTHER source
    # EN: from winning while a sequence runs, once the cutover (cf. the block's
    # EN: docstring above) makes that necessary.
    interruptible: bool = True
    steps: "list[SequenceStep]" = field(default_factory=list)


def parse_sequences(raw_sequences: "list[dict] | None") -> "list[Sequence]":
    """Convertit le bloc `sequences:` brut du YAML (déjà exposé tel quel dans
    le `meta` retourné par load_abilities, cf. ci-dessus -- pas besoin de
    relire le fichier) en objets Sequence/SequenceStep typés. Absent du YAML
    (raw_sequences=None, ou clé absente de `meta`) -> liste vide : comportement
    par défaut pour tout personnage qui n'a pas encore de séquence migrée
    (Tariq aujourd'hui) -- RotationEngine doit fonctionner à l'identique
    qu'avant cette migration quand cette liste est vide.

    EN: Converts the raw `sequences:` block from the YAML (already exposed
    EN: as-is in the `meta` returned by load_abilities, cf. above -- no need to
    EN: re-read the file) into typed Sequence/SequenceStep objects. Absent from
    EN: the YAML (raw_sequences=None, or the key missing from `meta`) -> empty
    EN: list: default behavior for any character without a migrated sequence
    EN: yet (Tariq today) -- RotationEngine must behave identically to before
    EN: this migration when this list is empty.
    """
    if not raw_sequences:
        return []
    sequences = []
    for raw_seq in raw_sequences:
        steps = [SequenceStep(**step) for step in raw_seq.get("steps", [])]
        sequences.append(Sequence(
            name=raw_seq["name"],
            condition=raw_seq.get("condition", ""),
            interruptible=raw_seq.get("interruptible", True),
            steps=steps,
        ))
    return sequences


# =============================================================================
# Évaluation des conditions -- tokens
# séparés par des virgules (AND), préfixe "not " pour la négation.
#   last:X            -> state.last_cast == X
#   hooks:X            -> state.hooks[X] est vrai (posé par les hooks pre/post-tick)
#   planner:X          -> state.planner[X] est vrai (posé par le Planner, ex. _update_tariq_planner)
#   debuff:X           -> state.debuffs[X] est vrai (posé par la détection)
#   buff:X             -> state.buffs[X] est vrai
#   resource_full       -> state.resources["primary"] >= 0.999
#   resource_above:N    -> state.resources["primary"]*100 >= N
#   fury_above:N        -> state.resources["fury"]*100 >= N
#   stormfall_above:N   -> state.resources["stormfall"]*100 >= N (Meiko, 2026-07-14 -- entrée des séquences Storm)
#   earthfall_above:N   -> state.resources["earthfall"]*100 >= N (Meiko, 2026-07-14 -- entrée des séquences Earth)
#   toughness_above:N   -> state.resources["toughness"]*100 >= N (Helena, 2026-07-18 -- gate Shields Up, cf. config/helena.yaml)
#   energy_above:N      -> state.resources["energy"] >= N (Saryn/Warframe, 2026-07-20 -- VALEUR ABSOLUE lue en OCR, pas un pourcentage, cf. config/saryn.yaml)
#   health_below:N / health_above:N
# EN: Condition evaluation -- tokens separated by commas (AND), prefix "not "
# EN: for negation.
# EN:   last:X            -> state.last_cast == X
# EN:   hooks:X            -> state.hooks[X] is true (set by the pre/post-tick hooks)
# EN:   planner:X          -> state.planner[X] is true (set by the Planner, e.g. _update_tariq_planner)
# EN:   debuff:X           -> state.debuffs[X] is true (set by detection)
# EN:   buff:X             -> state.buffs[X] is true
# EN:   resource_full       -> state.resources["primary"] >= 0.999
# EN:   resource_above:N    -> state.resources["primary"]*100 >= N
# EN:   fury_above:N        -> state.resources["fury"]*100 >= N
# EN:   stormfall_above:N   -> state.resources["stormfall"]*100 >= N (Meiko, 2026-07-14 -- Storm sequences entry)
# EN:   earthfall_above:N   -> state.resources["earthfall"]*100 >= N (Meiko, 2026-07-14 -- Earth sequences entry)
# EN:   toughness_above:N   -> state.resources["toughness"]*100 >= N (Helena, 2026-07-18 -- gates Shields Up, cf. config/helena.yaml)
# EN:   energy_above:N      -> state.resources["energy"] >= N (Saryn/Warframe, 2026-07-20 -- ABSOLUTE VALUE read via OCR, not a percentage, cf. config/saryn.yaml)
# EN:   health_below:N / health_above:N
# =============================================================================
def _eval_token(token: str, state: CombatState) -> bool:
    token = token.strip()
    negate = token.startswith("not ")
    if negate:
        token = token[4:].strip()

    key, _, value = token.partition(":")

    if key == "last":
        result = state.last_cast == value
    elif key == "hooks":
        result = bool(state.hooks.get(value, False))
    elif key == "planner":
        result = bool(state.planner.get(value, False))
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
    elif key == "stormfall_above":
        result = state.resources.get("stormfall", 0.0) * 100 >= float(value)
    elif key == "earthfall_above":
        result = state.resources.get("earthfall", 0.0) * 100 >= float(value)
    elif key == "toughness_above":
        result = state.resources.get("toughness", 0.0) * 100 >= float(value)
    elif key == "energy_above":
        # Saryn/Warframe (2026-07-20) -- PAS *100 comme les autres tokens
        # _above : ceux-ci lisent un pourcentage de remplissage de barre
        # (0.0-1.0, cf. bar_reader.py), l'Énergie Warframe est lue en OCR
        # (read_digits, wardenbot/ocr.py) comme une VALEUR ABSOLUE (110, 200...)
        # sur une réserve dont le plafond varie avec les mods -- comparer
        # directement à la valeur brute, pas à un pourcentage d'un max qui
        # n'est même pas connu ici.
        # EN: Saryn/Warframe (2026-07-20) -- NOT *100 like the other _above
        # EN: tokens: those read a bar-fill percentage (0.0-1.0, cf.
        # EN: bar_reader.py), Warframe Energy is read via OCR (read_digits,
        # EN: wardenbot/ocr.py) as an ABSOLUTE VALUE (110, 200...) against a
        # EN: reserve whose cap varies with mods -- compare directly to the
        # EN: raw value, not to a percentage of a max that isn't even known here.
        result = state.resources.get("energy", 0.0) >= float(value)
    elif key == "health_below":
        result = state.health_pct < float(value)
    elif key == "health_above":
        result = state.health_pct > float(value)
    else:
        raise ValueError(f"Condition inconnue : {token!r}")  # EN: Unknown condition

    return (not result) if negate else result


def evaluate_condition(condition: str, state: CombatState) -> bool:
    if not condition:
        return True
    return all(_eval_token(t, state) for t in condition.split(","))


# =============================================================================
# Intent -- unité de négociation entre les sources de décision (pre_tick_hook,
# scan de priorité YAML, et à terme un Decision Planner scoré). Chaque source
# propose un Intent (quelle ability, quel poids, qui le propose) ;
# RotationEngine.tick() retient celui au poids le plus élevé, plutôt que de
# laisser une source court-circuiter les autres de façon absolue et invisible
# (l'ancien pre_tick_hook -> Optional[str], où tout nom non-None gagnait
# inconditionnellement contre le scan, cf. historique dans hooks.py).
#
# MIGRATION (2026-07-14) : pre_tick_hook propose systématiquement un poids de
# 90 (cf. hooks.py) -- reproduit fidèlement l'ancien "GOTO absolu" : 90 est
# construit pour rester strictement supérieur au poids de base du scan de
# priorité YAML (50, cf. _pick_ability). Tant que ces deux poids restent des
# CONSTANTES connues à l'avance, le résultat de la comparaison est déjà
# déterminé sans avoir besoin d'évaluer le scan -- cf. le court-circuit
# volontaire dans tick(). Les poids ne deviendront vraiment dynamiques (et le
# court-circuit invalide) qu'à une phase ultérieure (scoring réel par
# ability, Decision Planner).
#
# 3 NIVEAUX (2026-07-14, Phase 3 -- migration sequences) : un step de
# séquence YAML (cf. Sequence plus haut) se glisse à 80, ENTRE le hook (90)
# et le scan (50) -- tant qu'un pre_tick_hook Python gère encore le même
# combo, il garde systématiquement la main ; la séquence ne prend le relai
# que les ticks où le hook ne propose rien du tout.
# EN: Intent -- the negotiation unit between the decision sources
# EN: (pre_tick_hook, the YAML priority scan, and eventually a scored Decision
# EN: Planner). Each source proposes an Intent (which ability, what weight,
# EN: who proposes it); RotationEngine.tick() keeps the highest-weighted one,
# EN: rather than letting one source absolutely and invisibly short-circuit
# EN: the others (the former pre_tick_hook -> Optional[str], where any
# EN: non-None name won unconditionally against the scan, cf. history in
# EN: hooks.py).
# EN:
# EN: MIGRATION (2026-07-14): pre_tick_hook systematically proposes a weight
# EN: of 90 (cf. hooks.py) -- faithfully reproduces the former "absolute
# EN: GOTO": 90 is built to stay strictly above the YAML priority scan's base
# EN: weight (50, cf. _pick_ability). As long as these two weights remain
# EN: KNOWN CONSTANTS ahead of time, the comparison's result is already
# EN: determined without needing to evaluate the scan -- cf. the deliberate
# EN: short-circuit in tick(). Weights only become truly dynamic (and the
# EN: short-circuit invalid) at a later phase (real per-ability scoring,
# EN: Decision Planner).
# EN:
# EN: 3 TIERS (2026-07-14, Phase 3 -- sequences migration): a YAML sequence
# EN: step (cf. Sequence above) slots in at 80, BETWEEN the hook (90) and the
# EN: scan (50) -- as long as a Python pre_tick_hook still handles the same
# EN: combo, it systematically keeps the hand; the sequence only takes over
# EN: on ticks where the hook proposes nothing at all.
# =============================================================================
@dataclass
class Intent:
    ability: str
    weight: float
    source: str
    # target (2026-07-15, Vigour -- ciblage de groupe) : nom logique d'allié
    # ("player_2", etc.), None par défaut. AUCUN effet sur Meiko/Tariq, qui ne
    # le renseignent jamais -- cf. RotationEngine.tick(), le press de
    # ciblage n'est déclenché QUE si ce champ est renseigné.
    # EN: target (2026-07-15, Vigour -- party targeting): logical ally name
    # EN: ("player_2", etc.), None by default. NO effect on Meiko/Tariq, who
    # EN: never set it -- cf. RotationEngine.tick(), the target-select press
    # EN: only fires if this field is set.
    target: Optional[str] = None


# =============================================================================
# Tuning appris (Phase 4, 2026-07-14, cf. wardenbot/kb_engine.py +
# wardenbot/telemetry.py) -- offsets de poids PAR NOM D'ABILITY, appliqués à
# Intent.weight au moment de la comparaison (cf.
# RotationEngine._effective_weight), quelle que soit la source (hook,
# séquence, scan) qui a proposé cette ability.
#
# "Les 3 règles d'or" (cf. discussion) :
#   1. Human-in-the-loop : ce module ne lit QUE kb/combat_tuning.json, un
#      fichier que l'utilisateur approuve/édite lui-même -- rien dans ce projet
#      n'écrit CE fichier automatiquement pendant que le bot tourne.
#      kb_engine.py (offline, après combat) écrit une SUGGESTION séparée
#      (kb/combat_tuning.suggested.json) ; l'utilisateur décide de la copier/éditer
#      en kb/combat_tuning.json pour l'activer.
#   2. Borné : MAX_WEIGHT_MODIFIER clampe chaque offset au chargement, avant
#      même qu'il touche un seul Intent -- jamais 0 à l'infini.
#   3. Contextualisation (cf. kb_engine.py) : les offsets sont calculés à
#      partir de signaux RÉELS déjà capturés par ce projet (taux de presses
#      fantômes via press_confirm.log, cf. hooks.py::_resolve_press_confirmations),
#      jamais inventés à partir de détections qui n'existent pas encore
#      (mouvement, portée, erreurs OCR -- cf. kb_engine.py pour le détail de
#      ce qui est honnêtement mesurable aujourd'hui).
# EN: Learned tuning (Phase 4, 2026-07-14, cf. wardenbot/kb_engine.py +
# EN: wardenbot/telemetry.py) -- weight offsets PER ABILITY NAME, applied to
# EN: Intent.weight at comparison time (cf. RotationEngine._effective_weight),
# EN: regardless of which source (hook, sequence, scan) proposed that ability.
# EN:
# EN: "The 3 golden rules" (cf. discussion):
# EN:   1. Human-in-the-loop: this module ONLY reads kb/combat_tuning.json, a
# EN:      file the user approves/edits himself -- nothing in this project
# EN:      writes THIS file automatically while the bot runs. kb_engine.py
# EN:      (offline, after combat) writes a SEPARATE suggestion
# EN:      (kb/combat_tuning.suggested.json); the user decides whether to
# EN:      copy/edit it into kb/combat_tuning.json to activate it.
# EN:   2. Bounded: MAX_WEIGHT_MODIFIER clamps every offset at load time,
# EN:      before it even touches a single Intent -- never 0 to infinity.
# EN:   3. Contextualization (cf. kb_engine.py): offsets are computed from
# EN:      REAL signals already captured by this project (phantom-press rate
# EN:      via press_confirm.log, cf. hooks.py::_resolve_press_confirmations),
# EN:      never invented from detections that don't exist yet (movement,
# EN:      range, OCR errors -- cf. kb_engine.py for what's honestly
# EN:      measurable today).
# =============================================================================
MAX_WEIGHT_MODIFIER = 20.0

DEFAULT_WEIGHT_OVERRIDES_PATH = Path("kb/combat_tuning.json")


def load_weight_overrides(path: "str | Path" = DEFAULT_WEIGHT_OVERRIDES_PATH) -> "dict[str, float]":
    """Charge kb/combat_tuning.json -- {ability_name: offset}. Fichier absent
    -> {} (PAS une erreur : tant que rien n'a été approuvé, le moteur tourne
    identique à avant cette fonctionnalité). JSON malformé -> {} également --
    un fichier de tuning cassé ne doit jamais empêcher le bot de démarrer.

    Chaque offset est clampé à ±MAX_WEIGHT_MODIFIER ICI, à la lecture -- même
    si kb_engine.py (ou une édition manuelle) produit une valeur aberrante,
    elle ne peut jamais dépasser cette borne une fois chargée dans le moteur.

    EN: Loads kb/combat_tuning.json -- {ability_name: offset}. File absent ->
    EN: {} (NOT an error: as long as nothing has been approved, the engine
    EN: runs identically to before this feature). Malformed JSON -> {} as
    EN: well -- a broken tuning file must never prevent the bot from starting.
    EN:
    EN: Each offset is clamped to ±MAX_WEIGHT_MODIFIER HERE, at load time --
    EN: even if kb_engine.py (or a manual edit) produces an outlandish value,
    EN: it can never exceed this bound once loaded into the engine.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    overrides: "dict[str, float]" = {}
    for ability, entry in raw.get("overrides", {}).items():
        offset = entry.get("weight_offset") if isinstance(entry, dict) else entry
        if not isinstance(offset, (int, float)):
            continue
        overrides[ability] = max(-MAX_WEIGHT_MODIFIER, min(MAX_WEIGHT_MODIFIER, float(offset)))
    return overrides


# =============================================================================
# Le moteur lui-même.
# EN: The engine itself.
# =============================================================================
class RotationEngine:
    def __init__(
        self,
        abilities: list[Ability],
        state: CombatState,
        press_fn: Callable[[str], None],
        is_ready_fn: Callable[..., bool],
        gcd_ms: int = 1100,
        pre_tick_hook: Optional[Callable[[CombatState], list[Intent]]] = None,
        post_tick_hook: Optional[Callable[[CombatState, str], None]] = None,
        state_update_fn: Optional[Callable[[CombatState], None]] = None,
        off_gcd_hook_abilities: frozenset = frozenset(),
        sequences: "Optional[list[Sequence]]" = None,
        telemetry: Optional[TelemetryWorker] = None,
        weight_overrides: "Optional[dict[str, float]]" = None,
        shutdown_fn: Optional[Callable[[], None]] = None,
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
        pre_tick_hook(state) -> list[Intent]
            Appelé APRÈS state_update_fn, AVANT le scan de priorité (cf.
            Intent plus haut). Retourne une liste d'Intent (vide si rien à
            proposer ce tick) -- PAS un nom nu. Chaque proposition a un poids
            de 90 (cf. hooks.py) : strictement supérieur au poids de base du
            scan (50), donc le hook l'emporte à chaque fois qu'il propose
            quelque chose -- même résultat qu'avant cette migration, mais
            négocié plutôt que codé en dur. Sert à compléter un combo en
            cours (équivalent à meiko_combo_pre_tick, mais en clair).
        post_tick_hook(state, pressed_name)
            Appelé APRÈS qu'une touche a été pressée (par le hook ou par le
            scan) -- reçoit toujours un nom nu (str), jamais un Intent : le
            gagnant a déjà été tranché à ce stade. Sert à mettre à jour
            flags/timers (équivalent à meiko_combo_post_tick).
        off_gcd_hook_abilities
            Noms pressés par pre_tick_hook qui ne posent PAS de nouveau GCD
            (ex: le finisher, off-GCD par nature). Tout le reste pressé par
            le hook pose un GCD normal, comme une ability classique. Sert
            AUSSI au Sequence Tracker (cf. Sequence plus haut) : un step dont
            l'ability figure ici ne bloque pas non plus sur le GCD.
            Défaut frozenset() VIDE (2026-07-26, généricité du moteur -- ne
            plus assumer un nom d'ability réel d'un personnage précis dans le
            core) : chaque personnage doit désormais le préciser explicitement
            (cf. game_config.py::CHARACTERS["meiko"], qui passe
            frozenset({"fighting_techniques"}) -- comportement RÉEL identique
            à avant, juste rendu explicite plutôt qu'implicite dans le moteur).
        sequences (2026-07-14, Phase 3) : liste de Sequence (cf. plus haut,
            engine.py::parse_sequences) -- None/liste vide (défaut) = AUCUN
            changement de comportement, exactement comme avant leur
            introduction. Suivies par le Sequence Tracker (cf.
            _advance_sequence), qui propose un Intent de poids 80 par tick
            tant qu'une séquence est active.

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
        EN: pre_tick_hook(state) -> list[Intent]
        EN:     Called AFTER state_update_fn, BEFORE the priority scan (cf.
        EN:     Intent above). Returns a list of Intent (empty if nothing to
        EN:     propose this tick) -- NOT a bare name. Each proposal has a
        EN:     weight of 90 (cf. hooks.py): strictly above the scan's base
        EN:     weight (50), so the hook wins every time it proposes something
        EN:     -- same result as before this migration, but negotiated rather
        EN:     than hardcoded. Used to complete an in-progress combo
        EN:     (equivalent to meiko_combo_pre_tick, but in plain code).
        EN: post_tick_hook(state, pressed_name)
        EN:     Called AFTER a key has been pressed (by the hook or the scan)
        EN:     -- always receives a bare name (str), never an Intent: the
        EN:     winner has already been decided by this point. Used to update
        EN:     flags/timers (equivalent to meiko_combo_post_tick).
        EN: off_gcd_hook_abilities
        EN:     Names pressed by pre_tick_hook that do NOT set a new GCD (e.g.
        EN:     the finisher, off-GCD by nature). Everything else pressed by the
        EN:     hook sets a normal GCD, like a regular ability. ALSO used by the
        EN:     Sequence Tracker (cf. Sequence above): a step whose ability is
        EN:     listed here doesn't block on the GCD either.
        EN:     Defaults to an EMPTY frozenset() (2026-07-26, engine genericity
        EN:     -- the core should no longer assume a real ability name from one
        EN:     specific character): each character must now state it
        EN:     explicitly (cf. game_config.py::CHARACTERS["meiko"], which
        EN:     passes frozenset({"fighting_techniques"}) -- REAL behavior
        EN:     identical to before, just made explicit rather than implicit in
        EN:     the engine).
        EN: sequences (2026-07-14, Phase 3): list of Sequence (cf. above,
        EN:     engine.py::parse_sequences) -- None/empty list (default) = NO
        EN:     behavior change, exactly like before their introduction.
        EN:     Tracked by the Sequence Tracker (cf. _advance_sequence), which
        EN:     proposes a weight-80 Intent per tick while a sequence is active.
        telemetry (2026-07-14, Phase 4) : TelemetryWorker (cf.
            wardenbot/telemetry.py) déjà construit ET DÉMARRÉ par l'appelant --
            None (défaut) = AUCUNE journalisation, AUCUN changement de
            comportement. RotationEngine ne construit JAMAIS de
            TelemetryWorker lui-même (cf. docstring module telemetry.py) --
            sinon chaque RotationEngine() de test ouvrirait un fichier et
            démarrerait un thread.
        weight_overrides (2026-07-14, Phase 4) : dict {ability: offset} (cf.
            load_weight_overrides) -- None/{} (défaut) = AUCUN changement de
            comportement. Offsets déjà clampés à ±MAX_WEIGHT_MODIFIER par
            load_weight_overrides -- RotationEngine ne les re-clampe pas, il
            fait confiance à l'appelant.

        EN: telemetry (2026-07-14, Phase 4): TelemetryWorker (cf.
        EN:     wardenbot/telemetry.py) already built AND STARTED by the
        EN:     caller -- None (default) = NO logging, NO behavior change.
        EN:     RotationEngine NEVER builds a TelemetryWorker itself (cf.
        EN:     telemetry.py's module docstring) -- otherwise every test's
        EN:     RotationEngine() would open a file and start a thread.
        EN: weight_overrides (2026-07-14, Phase 4): dict {ability: offset}
        EN:     (cf. load_weight_overrides) -- None/{} (default) = NO behavior
        EN:     change. Offsets already clamped to ±MAX_WEIGHT_MODIFIER by
        EN:     load_weight_overrides -- RotationEngine doesn't re-clamp them,
        EN:     it trusts the caller.
        shutdown_fn (2026-07-18, audit architecture -- fuite confirmée : les
            threads de détection (state_update.py::_run_meiko_detection_worker/
            _run_detection_worker) et l'InputWorker (input_sim.py) survivaient
            indéfiniment à un changement de personnage côté app.py::
            BotController.set_profile, faute de handle exposé pour les
            arrêter) : callable optionnel, opaque pour RotationEngine -- il ne
            connaît PAS le détail de ce qu'il arrête (input worker, stop_event
            d'un thread de détection...), juste qu'il doit appeler ceci à
            shutdown(). Construit par build_engine (main.py) à partir de ce
            qu'IL a lui-même créé. None (défaut) = shutdown() ne fait rien --
            comportement inchangé pour tout appelant qui n'en a pas besoin
            (tous les RotationEngine() de test notamment).

        EN: shutdown_fn (2026-07-18, architecture audit -- confirmed leak: the
        EN:     detection threads (state_update.py::_run_meiko_detection_worker/
        EN:     _run_detection_worker) and the InputWorker (input_sim.py) used
        EN:     to survive indefinitely across a character switch on app.py::
        EN:     BotController.set_profile's side, for lack of an exposed handle
        EN:     to stop them): optional callable, opaque to RotationEngine -- it
        EN:     does NOT know the detail of what it stops (input worker, a
        EN:     detection thread's stop_event...), just that it must call this
        EN:     on shutdown(). Built by build_engine (main.py) from whatever IT
        EN:     itself created. None (default) = shutdown() does nothing --
        EN:     unchanged behavior for any caller that doesn't need it
        EN:     (notably every test's RotationEngine()).
        """
        self.abilities = abilities
        self.state = state
        self.press_fn = press_fn
        self.is_ready_fn = is_ready_fn
        self.gcd_ms = gcd_ms
        self.pre_tick_hook = pre_tick_hook
        self.post_tick_hook = post_tick_hook
        self.state_update_fn = state_update_fn
        self.telemetry = telemetry
        self.weight_overrides = weight_overrides or {}
        self._shutdown_fn = shutdown_fn
        self.off_gcd_hook_abilities = off_gcd_hook_abilities
        self.sequences = sequences or []
        # Lookup nom -> Sequence -- même principe que _ability_by_name
        # ci-dessous, pour retrouver l'objet complet une fois son nom lu dans
        # state.sequences["active_sequence"] (cf. _advance_sequence).
        # EN: name -> Sequence lookup -- same principle as _ability_by_name
        # EN: below, to retrieve the full object once its name is read from
        # EN: state.sequences["active_sequence"] (cf. _advance_sequence).
        self._sequence_by_name = {s.name: s for s in self.sequences}
        # Lookup nom -> Ability : le scan de priorité (_pick_ability) retourne
        # désormais un Intent (juste un nom + poids), plus l'objet Ability
        # complet -- ce dict permet à tick() de retrouver cooldown_ms/off_gcd
        # une fois le gagnant tranché, sans que _pick_ability ait besoin de
        # transporter l'objet entier dans l'Intent.
        # EN: name -> Ability lookup: the priority scan (_pick_ability) now
        # EN: returns an Intent (just a name + weight), not the full Ability
        # EN: object anymore -- this dict lets tick() retrieve cooldown_ms/
        # EN: off_gcd once the winner is decided, without _pick_ability having
        # EN: to carry the whole object inside the Intent.
        self._ability_by_name = {a.name: a for a in abilities}

        # PROTOCOLE DE CONFIRMATION INPUT (audit architecture, 2026-07-18,
        # point 1 : "une action mise en file est immédiatement considérée
        # comme réussie"). tick() presse en enregistrant AUSSITÔT cooldown_ms/
        # GCD sur `now` (la décision), pas sur l'instant réel où InputWorker
        # exécute la touche (queue.py, cf. input_sim.py) -- nécessaire pour ne
        # PAS bloquer la boucle de décision sur la vraie durée d'une presse
        # (35-150ms, cf. HUMANIZE_*). Mais si la presse échoue RÉELLEMENT
        # (exception pydirectinput) ou est abandonnée (queue pleine, cf.
        # MAX_QUEUE_SIZE), l'ability restait quand même "verrouillée" par un
        # cooldown/GCD qui ne s'est jamais produit en jeu -- perte d'une
        # action complète sans qu'aucun symptôme ne le révèle.
        #
        # report_press_outcome(label, success, executed_at) est appelée par
        # InputWorker (thread séparé -- cf. input_sim.py::on_press_result)
        # dès qu'une presse aboutit ou échoue vraiment. self._press_outcomes
        # (queue.Queue, thread-safe SANS verrou manuel nécessaire) est le
        # SEUL point de contact entre ce thread et celui de la décision --
        # RotationEngine.tick() la draine en tout début de tick (cf.
        # _reconcile_press_outcomes) avant de décider quoi que ce soit,
        # jamais l'inverse (InputWorker n'écrit JAMAIS directement dans
        # state.cooldown_until/gcd_until -- ce serait une course de données
        # sur un dict Python ordinaire, non protégé).
        #
        # Portée VOLONTAIREMENT limitée à l'échec/l'abandon (success=True ->
        # no-op, cf. _reconcile_press_outcomes) : corriger aussi le cas
        # succès (petit ajustement de cooldown_until vers l'instant réel
        # d'exécution, généralement <150ms d'écart, cf. données réelles
        # input_timing.log) apporterait peu pour un risque de régression
        # disproportionné -- ce protocole répond spécifiquement au risque
        # identifié par l'audit (agir comme si une action avait réussi alors
        # qu'elle a échoué), pas à un raffinement de précision temporelle.
        #
        # _pending_receipts : "reçus" posés PAR ce même thread (tick()) juste
        # après record_cast/gcd_until (cf. _record_press_receipt), une file
        # FIFO PAR ability -- comparés à la valeur ACTUELLE au moment de la
        # réconciliation (compare-and-clear) avant de rien annuler : si une
        # presse PLUS RÉCENTE de la même ability a déjà redéfini
        # cooldown_until/gcd_until entre-temps, l'échec tardif d'une VIEILLE
        # presse ne doit PAS écraser un verrou légitime et plus récent (cf.
        # _reconcile_press_outcomes pour le détail -- une seule valeur par nom
        # ne suffisait PAS, trouvé via un test de non-régression).
        # EN: INPUT CONFIRMATION PROTOCOL (architecture audit, 2026-07-18,
        # EN: point 1: "a queued action is immediately considered
        # EN: successful"). tick() presses while recording cooldown_ms/GCD
        # EN: IMMEDIATELY on `now` (decision time), not on the real instant
        # EN: InputWorker actually executes the key (cf. input_sim.py) --
        # EN: necessary so the decision loop does NOT block on a press's real
        # EN: duration (35-150ms, cf. HUMANIZE_*). But if the press REALLY
        # EN: fails (pydirectinput exception) or gets dropped (queue full, cf.
        # EN: MAX_QUEUE_SIZE), the ability stayed "locked" by a cooldown/GCD
        # EN: that never actually happened in-game -- a whole action lost with
        # EN: no symptom revealing it.
        # EN:
        # EN: report_press_outcome(label, success, executed_at) is called by
        # EN: InputWorker (a separate thread -- cf. input_sim.py::
        # EN: on_press_result) as soon as a press truly succeeds or fails.
        # EN: self._press_outcomes (queue.Queue, thread-safe with no manual
        # EN: lock needed) is the ONLY contact point between that thread and
        # EN: the decision one -- RotationEngine.tick() drains it at the very
        # EN: start of each tick (cf. _reconcile_press_outcomes) before
        # EN: deciding anything, never the other way around (InputWorker NEVER
        # EN: writes directly into state.cooldown_until/gcd_until -- that would
        # EN: be a data race on a plain, unprotected Python dict).
        # EN:
        # EN: Scope DELIBERATELY limited to failure/drop (success=True ->
        # EN: no-op, cf. _reconcile_press_outcomes): also correcting the
        # EN: success case (a small cooldown_until adjustment toward the real
        # EN: execution instant, generally <150ms off, cf. real
        # EN: input_timing.log data) would bring little value for a
        # EN: disproportionate regression risk -- this protocol specifically
        # EN: answers the risk the audit identified (acting as if an action
        # EN: succeeded when it failed), not a timing-precision refinement.
        # EN:
        # EN: _pending_receipts: "receipts" set BY this same thread (tick())
        # EN: right after record_cast/gcd_until (cf. _record_press_receipt), a
        # EN: FIFO queue PER ability -- compared against the CURRENT value at
        # EN: reconciliation time (compare-and-clear) before undoing anything:
        # EN: if a MORE RECENT press of the same ability already redefined
        # EN: cooldown_until/gcd_until in the meantime, a late failure from an
        # EN: OLD press must NOT clobber a legitimate, more
        # EN: recent lock.
        self._press_outcomes: "queue.Queue[tuple[str, bool, float]]" = queue.Queue()
        # PAR ABILITY, PAS UNE SEULE VALEUR (bug trouvé en écrivant le test de
        # non-régression ci-dessous, cf. test_press_confirmation_failure_
        # does_not_clobber_a_newer_press) : une seule valeur par nom écrasée à
        # chaque presse ne peut PAS distinguer "cette confirmation concerne
        # la presse la plus récente" de "elle concerne une presse plus
        # ancienne, déjà remplacée" quand la MÊME ability est pressée deux
        # fois avant que la 1re confirmation n'arrive (rare mais réel --
        # off_gcd, ou GCD neutralisé entre deux ticks). Une file (deque) PAR
        # ability restaure le bon ordre : chaque presse empile son reçu,
        # chaque confirmation dépile le PLUS ANCIEN reçu de CETTE ability
        # (popleft) -- FIFO garanti car InputWorker traite sa queue dans
        # l'ordre d'arrivée, jamais en parallèle. Le compare-and-clear final
        # se fait quand même contre la valeur ACTUELLE de cooldown_until/
        # gcd_until (pas seulement l'ordre) : gère correctement le cas
        # croisé entre 2 abilities DIFFÉRENTES qui partagent gcd_until (cf.
        # plus bas, _reconcile_press_outcomes).
        # EN: PER ABILITY, NOT A SINGLE VALUE (bug found while writing the
        # EN: regression test below, cf. test_press_confirmation_failure_
        # EN: does_not_clobber_a_newer_press): a single value per name
        # EN: overwritten on every press CANNOT distinguish "this confirmation
        # EN: is about the most recent press" from "it's about an older one,
        # EN: already superseded" when the SAME ability is pressed twice
        # EN: before the 1st confirmation arrives (rare but real -- off_gcd,
        # EN: or GCD neutralized between two ticks). A queue (deque) PER
        # EN: ability restores the right order: each press pushes its
        # EN: receipt, each confirmation pops the OLDEST receipt for THAT
        # EN: ability (popleft) -- FIFO guaranteed since InputWorker processes
        # EN: its queue in arrival order, never in parallel. The final
        # EN: compare-and-clear still happens against the CURRENT value of
        # EN: cooldown_until/gcd_until (not just ordering): correctly handles
        # EN: the cross case between 2 DIFFERENT abilities sharing gcd_until
        # EN: (cf. below, _reconcile_press_outcomes).
        self._pending_receipts: "dict[str, deque[tuple[Optional[float], Optional[float]]]]" = defaultdict(deque)

    def report_press_outcome(self, label: str, success: bool, executed_at: float) -> None:
        """Point d'entrée THREAD-SAFE pour InputWorker (cf. input_sim.py::
        on_press_result) -- appelable depuis N'IMPORTE QUEL thread, y compris
        celui d'input. Ne fait qu'empiler dans self._press_outcomes
        (queue.Queue.put(), thread-safe sans verrou) -- la réconciliation
        elle-même n'a lieu que sur le thread de décision, cf.
        _reconcile_press_outcomes.

        EN: THREAD-SAFE entry point for InputWorker (cf. input_sim.py::
        EN: on_press_result) -- callable from ANY thread, including the input
        EN: one. Only stacks onto self._press_outcomes (queue.Queue.put(),
        EN: thread-safe with no lock) -- the reconciliation itself only
        EN: happens on the decision thread, cf. _reconcile_press_outcomes.
        """
        self._press_outcomes.put((label, success, executed_at))

    def _record_press_receipt(self, pressed: str, cooldown_ms: int, gcd_was_set: bool) -> None:
        """Appelée juste après record_cast()/la mise à jour de gcd_until,
        dans les deux branches de tick() qui pressent réellement -- empile un
        reçu (cooldown_until assigné ou None, gcd_until assigné ou None) dans
        self._pending_receipts[pressed] pour que _reconcile_press_outcomes
        puisse dépiler le PLUS ANCIEN reçu de CETTE ability à la confirmation
        (FIFO, cf. docstring de __init__ plus haut -- pas juste "le dernier
        connu")."""
        assigned_cooldown = self.state.cooldown_until.get(pressed) if cooldown_ms > 0 else None
        assigned_gcd = self.state.gcd_until if gcd_was_set else None
        if assigned_cooldown is not None or assigned_gcd is not None:
            self._pending_receipts[pressed].append((assigned_cooldown, assigned_gcd))

    def _reconcile_press_outcomes(self, now: float) -> None:
        """Draine self._press_outcomes (non-bloquant -- get_nowait() jusqu'à
        Empty) EN TOUT DÉBUT de tick(), avant toute décision. Dépile TOUJOURS
        le reçu le plus ancien de state._pending_receipts[label] (FIFO, cf.
        docstring de __init__) qu'il y ait succès ou échec -- sinon le reçu
        d'une presse réussie resterait empilé indéfiniment (fuite mémoire sur
        une longue session, cf. audit).

        success=True -> reçu simplement consommé, rien d'autre à faire (cf.
        portée du protocole, docstring __init__). success=False ->
        compare-and-clear : n'annule cooldown_until[label]/gcd_until QUE si
        chacun vaut ENCORE exactement ce que CE reçu précis avait assigné --
        si une presse plus récente (même ability pour le cooldown, N'IMPORTE
        QUELLE ability pour le GCD partagé) les a déjà redéfinis entre-temps,
        on laisse ce verrou plus récent et légitime tranquille.

        EN: Drains self._press_outcomes (non-blocking -- get_nowait() until
        EN: Empty) at the very START of tick(), before any decision. ALWAYS
        EN: pops the oldest receipt from state._pending_receipts[label]
        EN: (FIFO, cf. __init__'s docstring) whether success or failure --
        EN: otherwise a successful press's receipt would stay stacked forever
        EN: (a memory leak over a long session, cf. the audit).
        EN:
        EN: success=True -> receipt simply consumed, nothing else to do (cf.
        EN: the protocol's scope, __init__'s docstring). success=False ->
        EN: compare-and-clear: only undoes cooldown_until[label]/gcd_until IF
        EN: each STILL equals exactly what THIS specific receipt assigned --
        EN: if a more recent press (same ability for the cooldown, ANY
        EN: ability for the shared GCD) already redefined them in the
        EN: meantime, that more recent, legitimate lock is left alone.
        """
        while True:
            try:
                label, success, executed_at = self._press_outcomes.get_nowait()
            except queue.Empty:
                return

            receipts = self._pending_receipts.get(label)
            if not receipts:
                continue   # rien à réconcilier pour cette ability (ex: cooldown_ms=0 ET off_gcd -- aucun reçu n'a jamais été empilé)
            assigned_cooldown, assigned_gcd = receipts.popleft()
            if not receipts:
                self._pending_receipts.pop(label, None)   # évite d'accumuler des deques vides pour chaque ability jamais rejouée

            if success:
                continue

            if assigned_cooldown is not None and self.state.cooldown_until.get(label) == assigned_cooldown:
                self.state.cooldown_until.pop(label, None)

            if assigned_gcd is not None and self.state.gcd_until == assigned_gcd:
                self.state.gcd_until = executed_at   # libère le GCD -- la presse n'a jamais eu lieu · EN: frees the GCD -- the press never happened

    def shutdown(self) -> None:
        """Arrête proprement les ressources externes liées à ce moteur (cf.
        shutdown_fn ci-dessus) -- à appeler explicitement par l'appelant
        (build_engine ne le fait PAS automatiquement, ni run_forever à sa
        sortie : un appelant qui tourne en dry-run/test, ou qui veut réutiliser
        le même InputWorker pour un futur moteur, ne doit pas se le faire
        couper sous le pied). Idempotent seulement si shutdown_fn lui-même
        l'est (cf. InputWorker.stop()/threading.Event.set(), tous deux
        rappelables sans effet de bord).

        EN: Cleanly stops the external resources tied to this engine (cf.
        EN: shutdown_fn above) -- must be called explicitly by the caller
        EN: (build_engine does NOT do it automatically, nor does run_forever on
        EN: exit: a caller running in dry-run/test, or one that wants to reuse
        EN: the same InputWorker for a future engine, shouldn't have it pulled
        EN: out from under them). Idempotent only if shutdown_fn itself is (cf.
        EN: InputWorker.stop()/threading.Event.set(), both safely re-callable).
        """
        if self._shutdown_fn is not None:
            self._shutdown_fn()

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

    def _effective_weight(self, intent: Intent) -> float:
        """Poids RÉEL utilisé pour comparer les Intent entre eux -- poids de
        base (cf. Intent.weight, fixé par la source : 90/80/50) + l'offset
        appris pour CETTE ability (cf. self.weight_overrides,
        load_weight_overrides), déjà clampé à ±MAX_WEIGHT_MODIFIER au
        chargement. weight_overrides vide (défaut) -> effective_weight ==
        intent.weight, comportement identique à avant cette fonctionnalité.

        EN: The REAL weight used to compare Intents against each other --
        EN: base weight (cf. Intent.weight, fixed by the source: 90/80/50) +
        EN: the learned offset for THIS ability (cf. self.weight_overrides,
        EN: load_weight_overrides), already clamped to ±MAX_WEIGHT_MODIFIER at
        EN: load time. Empty weight_overrides (default) -> effective_weight ==
        EN: intent.weight, identical behavior to before this feature.
        """
        return intent.weight + self.weight_overrides.get(intent.ability, 0.0)

    def _pick_ability(self, now: float) -> Optional[Intent]:
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
            # Poids fixe de 50 (cf. Intent) -- la "priorité de base" du YAML.
            # `ab` reste consultable via self._ability_by_name[ab.name] une
            # fois le gagnant tranché dans tick() (cooldown_ms/off_gcd).
            # EN: Fixed weight of 50 (cf. Intent) -- the YAML's "base priority".
            # EN: `ab` remains reachable via self._ability_by_name[ab.name] once
            # EN: the winner is decided in tick() (cooldown_ms/off_gcd).
            return Intent(ability=ab.name, weight=50, source="priority_scan")
        return None

    def _advance_sequence(self, now: float) -> Optional[Intent]:
        """Sequence Tracker (cf. Sequence/parse_sequences plus haut). Lit/écrit
        UNIQUEMENT state.sequences (jamais state.hooks -- cf. CombatState :
        c'est le moteur, pas hooks.py, qui possède cette mémoire).

        Ne fait AVANCER sequence_step que lorsque le step qu'elle propose a
        RÉELLEMENT gagné la comparaison de poids dans tick() (cf. l'appel à
        confirm si `winner is sequence_intent`) -- pas au simple moment de la
        proposition. Tant qu'un pre_tick_hook Python gère encore le même
        combo (poids 90 > 80), cette méthode peut retourner un Intent qui
        perd systématiquement sans jamais avancer : la séquence reste
        "garée" sur le même step, prête à reprendre la main dès que le hook
        ne propose plus rien pour ce combo précis.

        Si la condition du step courant devient fausse (fenêtre de proc
        ratée, état qui a dérivé pendant que le hook gérait autre chose), la
        séquence est purgée plutôt que de rester bloquée à retenter
        indéfiniment sur un step devenu incohérent -- elle repartira de zéro
        la prochaine fois que sa condition d'ENTRÉE redeviendra vraie.

        EN: Sequence Tracker (cf. Sequence/parse_sequences above). Reads/writes
        EN: ONLY state.sequences (never state.hooks -- cf. CombatState: it's
        EN: the engine, not hooks.py, that owns this memory).

        EN: Only ADVANCES sequence_step when the step it proposes has
        EN: ACTUALLY won the weight comparison in tick() (cf. the call to
        EN: confirm if `winner is sequence_intent`) -- not at the mere moment
        EN: of proposing it. As long as a Python pre_tick_hook still handles
        EN: the same combo (weight 90 > 80), this method can return an Intent
        EN: that systematically loses without ever advancing: the sequence
        EN: stays "parked" on the same step, ready to take over the moment the
        EN: hook stops proposing anything for this specific combo.

        EN: If the current step's condition becomes false (a missed proc
        EN: window, state that drifted while the hook handled something
        EN: else), the sequence is purged rather than staying stuck retrying
        EN: an now-inconsistent step forever -- it will start fresh next time
        EN: its ENTRY condition becomes true again.
        """
        if not self.sequences:
            return None

        seq_state = self.state.sequences
        active_name = seq_state.get("active_sequence")

        if active_name is None:
            for seq in self.sequences:
                if evaluate_condition(seq.condition, self.state):
                    active_name = seq.name
                    seq_state["active_sequence"] = active_name
                    seq_state["sequence_step"] = 0
                    break
            else:
                return None

        sequence = self._sequence_by_name.get(active_name)
        step_index = seq_state.get("sequence_step", 0)

        if sequence is None or step_index >= len(sequence.steps):
            # Séquence inconnue (ne devrait pas arriver) ou déjà terminée --
            # purge et laisse la place aux autres sources ce tick.
            # EN: Unknown sequence (shouldn't happen) or already finished --
            # EN: purge and make room for the other sources this tick.
            seq_state.pop("active_sequence", None)
            seq_state.pop("sequence_step", None)
            return None

        step = sequence.steps[step_index]
        if step.condition and not evaluate_condition(step.condition, self.state):
            seq_state.pop("active_sequence", None)
            seq_state.pop("sequence_step", None)
            return None

        off_gcd_step = step.ability in self.off_gcd_hook_abilities
        if not off_gcd_step and now < self.state.gcd_until:
            return None   # pas prêt ce tick (GCD) -- retente au prochain, sequence_step inchangé · EN: not ready this tick (GCD) -- retries next tick, sequence_step unchanged

        return Intent(ability=step.ability, weight=80, source=f"sequence:{sequence.name}")

    def _confirm_sequence_step(self) -> None:
        """Appelée par tick() UNIQUEMENT quand l'Intent d'une séquence a
        réellement gagné la comparaison de poids ce tick -- avance
        sequence_step d'une unité. La toute prochaine fois que
        _advance_sequence est appelée, elle relira ce compteur incrémenté et
        proposera donc le step SUIVANT (ou purgera la séquence si c'était le
        dernier).

        EN: Called by tick() ONLY when a sequence's Intent has actually won
        EN: the weight comparison this tick -- advances sequence_step by one.
        EN: The very next time _advance_sequence is called, it will read this
        EN: incremented counter and thus propose the NEXT step (or purge the
        EN: sequence if that was the last one).
        """
        self.state.sequences["sequence_step"] = self.state.sequences.get("sequence_step", 0) + 1

    def tick(self) -> Optional[str]:
        """Une itération du moteur. Retourne le nom pressé, ou None.

        EN: One engine iteration. Returns the pressed name, or None.
        """
        now = time.monotonic()

        # Réconciliation des presses (audit architecture, 2026-07-18, cf.
        # docstring de __init__) -- EN TOUT PREMIER, avant même
        # state_update_fn : une presse dont l'échec vient d'être confirmé par
        # InputWorker doit libérer son cooldown/GCD AVANT que ce tick ne
        # décide quoi que ce soit, sans quoi la correction arriverait un tick
        # trop tard.
        # EN: Press reconciliation (architecture audit, 2026-07-18, cf.
        # EN: __init__'s docstring) -- FIRST THING, even before
        # EN: state_update_fn: a press whose failure InputWorker just
        # EN: confirmed must free its cooldown/GCD BEFORE this tick decides
        # EN: anything, or the correction would land one tick too late.
        self._reconcile_press_outcomes(now)

        if self.state_update_fn is not None:
            self.state_update_fn(self.state)

        hook_intents: list[Intent] = []
        if self.pre_tick_hook is not None:
            # Tourne MÊME sous hard_lock -- c'est lui qui détecte la fin de
            # la canalisation et lève le verrou (cf. tariq_pre_tick). Le
            # verrou n'empêche que la PRESSE, pas la détection.
            # EN: Runs EVEN under hard_lock -- it's the hook that detects the end
            # EN: of the channel and releases the lock (cf. tariq_pre_tick). The
            # EN: lock only prevents the PRESS, not detection.
            hook_intents = self.pre_tick_hook(self.state)

        # Sequence Tracker (cf. _advance_sequence) : comme le hook, tourne
        # MÊME sous hard_lock (même raison -- ne bloque que la PRESSE plus
        # bas) et est TOUJOURS évalué, jamais court-circuité par la présence
        # d'un hook_intents -- contrairement au scan (_pick_ability), ce
        # n'est pas un appel coûteux (pas de capture d'écran), rien à gagner
        # à l'éviter par optimisation.
        # EN: Sequence Tracker (cf. _advance_sequence): like the hook, runs
        # EN: EVEN under hard_lock (same reason -- only blocks the PRESS
        # EN: below) and is ALWAYS evaluated, never short-circuited by the
        # EN: presence of hook_intents -- unlike the scan (_pick_ability),
        # EN: this isn't an expensive call (no screen capture), nothing to
        # EN: gain by skipping it as an optimization.
        sequence_intent = self._advance_sequence(now)

        if now < self.state.hard_lock_until:
            # Canalisation en cours (ex. Hammer Storm) -- bloque TOUTE presse
            # ce tick, même une ability off_gcd ou une proposition du hook/
            # d'une séquence.
            # EN: Channel in progress (e.g. Hammer Storm) -- blocks EVERY press
            # EN: this tick, even an off_gcd ability or a hook/sequence proposal.
            hook_intents = []
            sequence_intent = None

        candidate_intents = list(hook_intents)
        if sequence_intent is not None:
            candidate_intents.append(sequence_intent)

        # Le poids EFFECTIF du hook (base 90) ET celui d'un step de séquence
        # (base 80, cf. Intent) restent construits pour dominer le poids de
        # base du scan (50, cf. _pick_ability) TANT QU'AUCUN offset appris
        # n'est en jeu (cf. weight_overrides, Phase 4) -- le résultat de la
        # comparaison face au scan est alors déjà connu SANS avoir besoin de
        # l'évaluer.
        #
        # COURT-CIRCUIT VOLONTAIRE (pas juste par flemme) : évaluer le scan
        # ici déclencherait des détections pour rien (ex. chain_lightning
        # côté Tariq, seule ability du kit encore sans skip_detection -- cf.
        # config/tariq.yaml) dont le résultat ne serait de toute façon jamais
        # retenu. Le projet a déjà payé cher ce genre d'appel superflu sur le
        # thread de décision (cf. CLAUDE.md, plusieurs bugs "bot à la
        # ramasse" dus à des captures d'écran/YOLO non throttlées).
        #
        # GARDE-FOU (2026-07-14, Phase 4 -- tuning appris) : un offset appris
        # (weight_overrides, ±MAX_WEIGHT_MODIFIER) peut désormais faire
        # varier CHAQUE poids de ±20. Un hook au pire (90-20=70) reste
        # TOUJOURS >= le scan au mieux (50+20=70, égalité jamais dépassée) --
        # mais un STEP DE SÉQUENCE au pire (80-20=60) peut tomber SOUS le
        # scan au mieux (70) : le court-circuit d'origine deviendrait alors
        # incorrect. On ne court-circuite donc QUE si le meilleur candidat
        # "pas cher" (hook/séquence) a déjà un poids effectif au moins égal
        # au PIRE poids possible du scan -- sinon, on évalue le scan pour de
        # vrai, par sécurité, avant de trancher.
        # EN: The hook's EFFECTIVE weight (base 90) AND a sequence step's
        # EN: (base 80, cf. Intent) stay built to dominate the scan's base
        # EN: weight (50, cf. _pick_ability) AS LONG AS NO learned offset is in
        # EN: play (cf. weight_overrides, Phase 4) -- the result of the
        # EN: comparison against the scan is then already known WITHOUT
        # EN: needing to evaluate it.
        # EN:
        # EN: DELIBERATE SHORT-CIRCUIT (not just laziness): evaluating the
        # EN: scan here would trigger detections for nothing (e.g.
        # EN: chain_lightning on the Tariq side, the only ability in the kit
        # EN: still without skip_detection -- cf. config/tariq.yaml) whose
        # EN: result would never be kept anyway. The project has already paid
        # EN: dearly for this kind of superfluous call on the decision thread
        # EN: (cf. CLAUDE.md, several "bot lags" bugs caused by un-throttled
        # EN: screen captures/YOLO).
        # EN:
        # EN: GUARDRAIL (2026-07-14, Phase 4 -- learned tuning): a learned
        # EN: offset (weight_overrides, ±MAX_WEIGHT_MODIFIER) can now shift
        # EN: EACH weight by ±20. A hook at worst (90-20=70) ALWAYS stays >=
        # EN: the scan at best (50+20=70, a tie never exceeded) -- but a
        # EN: SEQUENCE STEP at worst (80-20=60) can drop BELOW the scan at
        # EN: best (70): the original short-circuit would then become
        # EN: incorrect. So we only short-circuit if the best "cheap"
        # EN: candidate (hook/sequence) already has an effective weight at
        # EN: least equal to the scan's WORST possible weight -- otherwise, we
        # EN: evaluate the scan for real, for safety, before deciding.
        scan_worst_case_weight = 50 + MAX_WEIGHT_MODIFIER
        best_cheap_intent = max(candidate_intents, key=self._effective_weight, default=None)
        best_cheap_weight = self._effective_weight(best_cheap_intent) if best_cheap_intent is not None else float("-inf")

        scan_intent: Optional[Intent] = None
        if best_cheap_weight >= scan_worst_case_weight:
            winner = best_cheap_intent
        else:
            scan_intent = self._pick_ability(now)
            if scan_intent is not None:
                candidate_intents.append(scan_intent)
            winner = max(candidate_intents, key=self._effective_weight, default=None)

        pressed: Optional[str] = None
        gcd_slack_ms = (now - self.state.gcd_until) * 1000.0   # capturé AVANT toute mise à jour de gcd_until ci-dessous -- cf. telemetry plus bas · EN: captured BEFORE any gcd_until update below -- cf. telemetry further down

        # Ciblage de groupe (2026-07-15, Vigour) : si l'Intent gagnant vise un
        # allié précis, presse la touche de ciblage AVANT le sort lui-même,
        # dans le MÊME tick -- toujours, sans condition (pas de "déjà
        # sélectionné ?" trackée : une croyance interne peut désynchroniser du
        # jeu réel -- ex. le joueur clique un autre allié -- et rien n'est
        # lisible à l'écran pour la confirmer, cf. discussion architecture
        # Vigour). Hors du système Ability/YAML : ni cooldown, ni GCD, ni
        # record_cast -- juste une touche de plus (profiles/*/config.json
        # ::keybinds, jamais dans abilities:). Zéro effet pour Meiko/Tariq,
        # qui ne renseignent jamais Intent.target.
        # EN: Party targeting (2026-07-15, Vigour): if the winning Intent
        # EN: targets a specific ally, press the target-select key BEFORE the
        # EN: spell itself, in the SAME tick -- always, unconditionally (no
        # EN: "already selected?" tracking: an internal belief can desync from
        # EN: the real game -- e.g. the player clicks another ally -- and
        # EN: nothing on screen can confirm it, cf. Vigour architecture
        # EN: discussion). Outside the Ability/YAML system: no cooldown, no
        # EN: GCD, no record_cast -- just one more key (profiles/*/config.json
        # EN: ::keybinds, never in abilities:). Zero effect for Meiko/Tariq,
        # EN: who never set Intent.target.
        if winner is not None and winner.target is not None:
            target_key_name = f"target_{winner.target}"
            self.press_fn(target_key_name)
            if self.telemetry is not None:
                self.telemetry.log_decision(
                    self.state,
                    Intent(ability=target_key_name, weight=winner.weight, source="target_resolver", target=winner.target),
                    extra={"resolved_for": winner.ability},
                )

        if winner is not None and winner is scan_intent:
            # Le scan a gagné -- soit il n'y avait aucun candidat "pas cher"
            # (hook/séquence vides, comme avant Phase 3), soit un offset
            # appris l'a fait passer devant un hook/séquence affaibli (cf.
            # garde-fou ci-dessus). debug_choice_reason est déjà posé par
            # _pick_ability lui-même, pas besoin de le refaire ici.
            # EN: The scan won -- either there was no "cheap" candidate at all
            # EN: (empty hook/sequence, like before Phase 3), or a learned
            # EN: offset pushed it ahead of a weakened hook/sequence (cf.
            # EN: guardrail above). debug_choice_reason is already set by
            # EN: _pick_ability itself, no need to redo it here.
            ability = self._ability_by_name[scan_intent.ability]
            pressed = ability.name
            cooldown_ms = ability.cooldown_ms
            self.press_fn(pressed)
            self.state.record_cast(pressed, now, cooldown_ms)
            gcd_will_be_set = not ability.off_gcd
            if gcd_will_be_set:
                self.state.gcd_until = now + self.gcd_ms / 1000.0
            self._record_press_receipt(pressed, cooldown_ms, gcd_will_be_set)
        elif winner is not None:
            pressed = winner.ability
            self.press_fn(pressed)
            # BUG CONFIRMÉ (audit architecture, 2026-07-17) : cooldown_ms=0 codé
            # en dur ici depuis toujours -- une ability gagnée par un hook/une
            # séquence n'enregistrait donc JAMAIS son vrai cooldown (record_cast
            # posait cooldown_until=now, déjà expiré au tick suivant). Sans
            # effet visible pour Meiko (spirit_palm/*_2/fighting_techniques ne
            # sont pas déclarées dans abilities:, gérées par GCD/FINISHER_DELAY_S
            # côté hooks.py, pas par cooldown_until) mais CRITIQUE pour Vigour :
            # TOUTES ses abilities (dawnbreaker_orb 9.97s, luminous_barrier
            # 19.94s, circle_of_light 14.95s, remove_magic 6s...) sont proposées
            # par le hook (jamais par le scan, skip_detection:true) -- leur
            # is_on_cooldown(...) côté hooks.py (cf. vigour_pre_tick) lisait donc
            # TOUJOURS False, quel que soit le vrai cooldown. Fix : même lookup
            # que la branche scan_intent ci-dessus -- si `pressed` est une
            # ability déclarée (abilities: du YAML), on utilise son VRAI
            # cooldown_ms ; sinon (combo pieces Meiko, touches de ciblage
            # jamais déclarées) le comportement d'origine (0) est inchangé.
            # EN: CONFIRMED BUG (architecture audit, 2026-07-17): cooldown_ms=0
            # EN: hardcoded here from the start -- an ability won by a hook/
            # EN: sequence therefore NEVER recorded its real cooldown
            # EN: (record_cast set cooldown_until=now, already expired by the
            # EN: next tick). No visible effect for Meiko (spirit_palm/*_2/
            # EN: fighting_techniques aren't declared in abilities:, gated by
            # EN: GCD/FINISHER_DELAY_S in hooks.py, not by cooldown_until) but
            # EN: CRITICAL for Vigour: ALL its abilities (dawnbreaker_orb 9.97s,
            # EN: luminous_barrier 19.94s, circle_of_light 14.95s, remove_magic
            # EN: 6s...) are proposed by the hook (never the scan,
            # EN: skip_detection:true) -- their is_on_cooldown(...) in hooks.py
            # EN: (cf. vigour_pre_tick) therefore ALWAYS read False, regardless
            # EN: of the real cooldown. Fix: same lookup as the scan_intent
            # EN: branch above -- if `pressed` is a declared ability (YAML
            # EN: abilities:), use its REAL cooldown_ms; otherwise (Meiko combo
            # EN: pieces, never-declared target-select keys) the original
            # EN: behavior (0) is unchanged.
            hook_ability = self._ability_by_name.get(pressed)
            hook_cooldown_ms = hook_ability.cooldown_ms if hook_ability is not None else 0
            self.state.record_cast(pressed, now, hook_cooldown_ms)
            self.state.debug_choice_reason = (
                f"décidé par {winner.source} (poids={winner.weight}, effectif={self._effective_weight(winner):.1f}, "  # EN: decided by <source> (weight=<weight>, effective=<effective_weight>,
                f"court-circuite le scan de priorité)"  # EN: short-circuits the priority scan)
            )
            gcd_will_be_set = pressed not in self.off_gcd_hook_abilities
            if gcd_will_be_set:
                self.state.gcd_until = now + self.gcd_ms / 1000.0
            self._record_press_receipt(pressed, hook_cooldown_ms, gcd_will_be_set)
            if winner is sequence_intent:
                # Le step proposé par la séquence a RÉELLEMENT gagné ce tick
                # (pas juste le hook qui aurait proposé la même ability par
                # coïncidence -- `is`, comparaison d'identité, pas d'égalité)
                # -- avance sequence_step. cf. _confirm_sequence_step.
                # EN: The step proposed by the sequence ACTUALLY won this tick
                # EN: (not just the hook coincidentally proposing the same
                # EN: ability -- `is`, identity comparison, not equality) --
                # EN: advances sequence_step. cf. _confirm_sequence_step.
                self._confirm_sequence_step()

        if pressed is not None and self.telemetry is not None:
            # Cf. wardenbot/telemetry.py : self.telemetry est None par défaut
            # (opt-in, jamais construit par RotationEngine lui-même) -- aucun
            # comportement/coût pour un appelant qui n'a rien demandé. Le
            # queue.put() interne est une opération mémoire, jamais d'I/O
            # disque sur CE thread.
            # EN: Cf. wardenbot/telemetry.py: self.telemetry is None by default
            # EN: (opt-in, never built by RotationEngine itself) -- no
            # EN: behavior/cost for a caller who didn't ask for it. The
            # EN: internal queue.put() is a memory operation, never disk I/O on
            # EN: THIS thread.
            self.telemetry.log_decision(
                self.state,
                winner,
                extra={
                    "effective_weight": self._effective_weight(winner),
                    "gcd_slack_ms": gcd_slack_ms,
                    "choice_reason": self.state.debug_choice_reason,
                },
            )

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
