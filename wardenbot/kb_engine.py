"""
kb_engine.py — analyse HORS LIGNE (après combat, jamais pendant, cf.
discussion 2026-07-14) du journal de décisions (telemetry.jsonl,
wardenbot/telemetry.py) pour produire une SUGGESTION d'ajustement de poids
(kb/combat_tuning.suggested.json), que l'utilisateur approuve manuellement avant
qu'elle ne devienne active (kb/combat_tuning.json, lu par
wardenbot/engine.py::load_weight_overrides).

CE QUI EST HONNÊTEMENT MESURABLE AUJOURD'HUI, ET CE QUI NE L'EST PAS ENCORE
---------------------------------------------------------------------------
La discussion d'origine proposait des signaux de récompense (mouvement,
portée, messages d'erreur OCR) qui n'existent PAS dans ce projet -- aucune
détection de "is_moving", "in_range" ou "game_error" nulle part dans
wardenbot/state_update.py ni wardenbot/hooks.py. Les inventer ici reviendrait
à violer le principe central de ce projet (cf. CLAUDE.md : "ne jamais
estimer un état qui peut être détecté directement à l'écran" -- le même
principe s'applique à un signal de récompense : ne jamais en fabriquer un
qui n'est pas réellement détecté). Ce module se limite donc à ce qui est
RÉELLEMENT capturé aujourd'hui :

  1. Taux de "presse fantôme" (Tariq UNIQUEMENT) -- press_confirm.log
     (wardenbot/diagnostics.py::log_press_confirmation) journalise déjà
     SUCCESS/PHANTOM_PRESS par ability, via le voile GCD (accusé de réception
     visuel du jeu, cf. hooks.py). Une ability dont la presse échoue souvent
     à réellement partir est un signal de récompense négatif RÉEL, pas
     inventé.
  2. Fréquence/cadence des décisions par ability+source (telemetry.jsonl
     seul) -- utile pour repérer une ability qui part beaucoup plus/moins
     souvent qu'attendu, mais PAS encore transformé en offset ici (aucun
     seuil "trop souvent" n'est défini nulle part dans le projet -- inventer
     un seuil arbitraire serait la même erreur que fabriquer is_moving).

Le mouvement, la portée et les erreurs de jeu deviendront des signaux
exploitables SEULEMENT après qu'une vraie détection existe pour eux (nouvelle
région + template/OCR, cf. wardenbot/state_update.py) -- pas avant. Ce
module est écrit pour absorber ces signaux plus tard SANS réécriture
(cf. _score_ability), pas pour les simuler aujourd'hui.

LES 3 RÈGLES D'OR (cf. discussion, appliquées ici)
---------------------------------------------------------------------------
1. Human-in-the-loop : ce script n'écrit JAMAIS kb/combat_tuning.json (le
   fichier ACTIF, lu par le moteur) -- seulement
   kb/combat_tuning.suggested.json. l'utilisateur décide de copier/éditer ce
   fichier pour l'activer.
2. Borné : les offsets suggérés ici restent volontairement petits (cf.
   PHANTOM_PRESS_WEIGHT_OFFSET) -- de toute façon reclampés à
   ±MAX_WEIGHT_MODIFIER par load_weight_overrides (engine.py) même si ce
   script produisait une valeur aberrante.
3. Contextualisation stricte : chaque offset est calculé PAR ABILITY, à
   partir de ses propres presses journalisées -- jamais une moyenne globale
   "le bot joue mal en général".

USAGE
---------------------------------------------------------------------------
    python -m wardenbot.kb_engine
    python -m wardenbot.kb_engine --telemetry telemetry.jsonl --press-confirm press_confirm.log

EN: kb_engine.py — OFFLINE analysis (after combat, never during, cf. the
EN: 2026-07-14 discussion) of the decision journal (telemetry.jsonl,
EN: wardenbot/telemetry.py) to produce a weight-adjustment SUGGESTION
EN: (kb/combat_tuning.suggested.json), which the user approves manually before
EN: it becomes active (kb/combat_tuning.json, read by
EN: wardenbot/engine.py::load_weight_overrides).
EN:
EN: WHAT'S HONESTLY MEASURABLE TODAY, AND WHAT ISN'T YET
EN: ---------------------------------------------------------------------
EN: The original discussion proposed reward signals (movement, range, OCR
EN: error messages) that do NOT exist in this project -- no detection of
EN: "is_moving", "in_range", or "game_error" anywhere in
EN: wardenbot/state_update.py or wardenbot/hooks.py. Inventing them here
EN: would violate this project's central principle (cf. CLAUDE.md: "never
EN: estimate a state that can be directly detected on screen" -- the same
EN: principle applies to a reward signal: never fabricate one that isn't
EN: actually detected). This module thus limits itself to what's REALLY
EN: captured today:
EN:
EN:   1. "Phantom press" rate (Tariq ONLY) -- press_confirm.log
EN:      (wardenbot/diagnostics.py::log_press_confirmation) already logs
EN:      SUCCESS/PHANTOM_PRESS per ability, via the GCD veil (a visual
EN:      acknowledgement from the game itself, cf. hooks.py). An ability
EN:      whose press often fails to actually fire is a REAL negative reward
EN:      signal, not an invented one.
EN:   2. Decision frequency/cadence per ability+source (telemetry.jsonl
EN:      alone) -- useful to spot an ability firing much more/less often than
EN:      expected, but NOT YET turned into an offset here (no "too often"
EN:      threshold is defined anywhere in the project -- inventing an
EN:      arbitrary one would be the same mistake as fabricating is_moving).
EN:
EN: Movement, range, and game errors will become usable signals ONLY once a
EN: real detection exists for them (new region + template/OCR, cf.
EN: wardenbot/state_update.py) -- not before. This module is written to
EN: absorb those signals later WITHOUT a rewrite (cf. _score_ability), not to
EN: simulate them today.
EN:
EN: THE 3 GOLDEN RULES (cf. discussion, applied here)
EN: ---------------------------------------------------------------------
EN: 1. Human-in-the-loop: this script NEVER writes kb/combat_tuning.json
EN:    (the ACTIVE file, read by the engine) -- only
EN:    kb/combat_tuning.suggested.json. the user decides whether to copy/edit
EN:    that file to activate it.
EN: 2. Bounded: the offsets suggested here stay deliberately small (cf.
EN:    PHANTOM_PRESS_WEIGHT_OFFSET) -- re-clamped to ±MAX_WEIGHT_MODIFIER by
EN:    load_weight_overrides (engine.py) anyway even if this script produced
EN:    an outlandish value.
EN: 3. Strict contextualization: each offset is computed PER ABILITY, from
EN:    its own logged presses -- never a global average "the bot plays badly
EN:    in general".
EN:
EN: USAGE
EN: ---------------------------------------------------------------------
EN:     python -m wardenbot.kb_engine
EN:     python -m wardenbot.kb_engine --telemetry telemetry.jsonl --press-confirm press_confirm.log
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

DEFAULT_TELEMETRY_PATH = Path("telemetry.jsonl")
DEFAULT_PRESS_CONFIRM_PATH = Path("press_confirm.log")
DEFAULT_SUGGESTED_TUNING_PATH = Path("kb/combat_tuning.suggested.json")

# Seuils volontairement conservateurs (cf. règle "borné") -- un taux de
# presses fantômes élevé mais mesuré sur trop peu d'échantillons est du
# bruit, pas un signal ; MIN_SAMPLES protège contre ça.
# EN: Deliberately conservative thresholds (cf. the "bounded" rule) -- a high
# EN: phantom-press rate measured on too few samples is noise, not a signal;
# EN: MIN_SAMPLES guards against that.
PHANTOM_PRESS_RATE_THRESHOLD = 0.20
PHANTOM_PRESS_MIN_SAMPLES = 10
PHANTOM_PRESS_WEIGHT_OFFSET = -5.0

_PRESS_CONFIRM_LINE_RE = re.compile(
    r"\[PRESS_CONFIRM\]\s+(?P<ability>\S+)\s+requested_at=\S+\s+gcd_detected_at=\S+\s+result=(?P<result>\S+)"
)

_PRESS_CONFIRM_RESULTS = frozenset({"SUCCESS", "PHANTOM_PRESS"})


@dataclass
class AbilityStats:
    press_count: int = 0
    sources: "dict[str, int]" = field(default_factory=lambda: defaultdict(int))
    success_count: int = 0
    phantom_count: int = 0

    @property
    def confirmable_count(self) -> int:
        return self.success_count + self.phantom_count

    @property
    def phantom_rate(self) -> Optional[float]:
        if self.confirmable_count == 0:
            return None
        return self.phantom_count / self.confirmable_count


def read_telemetry(path: "str | Path" = DEFAULT_TELEMETRY_PATH) -> "list[dict]":
    """Lit telemetry.jsonl -- une ligne malformée est ignorée (journalisée
    sur stderr) plutôt que de faire planter toute l'analyse d'une session
    par ailleurs valide. Fichier absent -> [] (pas d'erreur : rien à
    analyser tant qu'aucune session n'a été enregistrée).

    EN: Reads telemetry.jsonl -- a malformed line is skipped (logged to
    EN: stderr) rather than crashing the whole analysis over an otherwise
    EN: valid session. File absent -> [] (not an error: nothing to analyze
    EN: as long as no session has been recorded).
    """
    path = Path(path)
    if not path.exists():
        return []
    entries = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"[kb_engine] {path}:{line_no} -- ligne JSON invalide, ignorée")  # EN: invalid JSON line, skipped
    return entries


def read_press_confirmations(path: "str | Path" = DEFAULT_PRESS_CONFIRM_PATH) -> "dict[str, dict[str, int]]":
    """Lit press_confirm.log (Tariq uniquement, cf. hooks.py::GCD_VEIL_ABILITIES/
    OFF_GCD_VEIL_ABILITY_BUFF_KEYS) -- retourne {ability: {"SUCCESS": n,
    "PHANTOM_PRESS": n}}. Fichier absent -> {} (Meiko n'a pas ce mécanisme,
    ou la session n'a pas encore été jouée avec cette instrumentation).

    EN: Reads press_confirm.log (Tariq only, cf. hooks.py::GCD_VEIL_ABILITIES/
    EN: OFF_GCD_VEIL_ABILITY_BUFF_KEYS) -- returns {ability: {"SUCCESS": n,
    EN: "PHANTOM_PRESS": n}}. File absent -> {} (Meiko doesn't have this
    EN: mechanism, or the session hasn't been played with this
    EN: instrumentation yet).
    """
    path = Path(path)
    if not path.exists():
        return {}
    counts: "dict[str, dict[str, int]]" = defaultdict(lambda: defaultdict(int))
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        match = _PRESS_CONFIRM_LINE_RE.search(line)
        if not match:
            continue
        result = match.group("result")
        if result not in _PRESS_CONFIRM_RESULTS:
            continue
        counts[match.group("ability")][result] += 1
    return {ability: dict(results) for ability, results in counts.items()}


def compute_ability_stats(
    telemetry_entries: "list[dict]", press_confirmations: "dict[str, dict[str, int]]"
) -> "dict[str, AbilityStats]":
    """Fusionne les deux sources RÉELLES (telemetry.jsonl + press_confirm.log)
    en un dict {ability: AbilityStats} -- aucune donnée fabriquée, seulement
    ce que ces deux fichiers contiennent réellement.

    EN: Merges the two REAL sources (telemetry.jsonl + press_confirm.log)
    EN: into a dict {ability: AbilityStats} -- no fabricated data, only what
    EN: these two files actually contain.
    """
    stats: "dict[str, AbilityStats]" = defaultdict(AbilityStats)
    for entry in telemetry_entries:
        decision = entry.get("decision", {})
        ability = decision.get("ability")
        if not ability:
            continue
        s = stats[ability]
        s.press_count += 1
        s.sources[decision.get("source", "?")] += 1

    for ability, results in press_confirmations.items():
        s = stats[ability]
        s.success_count += results.get("SUCCESS", 0)
        s.phantom_count += results.get("PHANTOM_PRESS", 0)

    return dict(stats)


def _score_ability(ability: str, s: AbilityStats) -> "Optional[float]":
    """Calcule l'offset suggéré pour UNE ability, à partir de SES PROPRES
    stats -- jamais une moyenne globale (cf. règle "contextualisation
    stricte"). Retourne None si aucun signal exploitable (pas assez
    d'échantillons, ou taux de presses fantômes sous le seuil) : PAS
    d'offset plutôt qu'un offset inventé.

    Point d'extension pour de futurs signaux (mouvement, portée, erreurs
    OCR) une fois qu'une vraie détection existera pour eux -- cf. docstring
    module. Ajouter un signal ici NE DEVRAIT PAS nécessiter de toucher au
    reste du fichier (lecture des logs, fusion des stats, écriture du JSON).

    EN: Computes the suggested offset for ONE ability, from ITS OWN stats --
    EN: never a global average (cf. the "strict contextualization" rule).
    EN: Returns None if there's no usable signal (not enough samples, or
    EN: phantom-press rate below the threshold): NO offset rather than a
    EN: fabricated one.
    EN:
    EN: Extension point for future signals (movement, range, OCR errors) once
    EN: a real detection exists for them -- cf. module docstring. Adding a
    EN: signal here SHOULDN'T require touching the rest of the file (log
    EN: reading, stats merging, JSON writing).
    """
    if s.confirmable_count >= PHANTOM_PRESS_MIN_SAMPLES:
        rate = s.phantom_rate
        if rate is not None and rate > PHANTOM_PRESS_RATE_THRESHOLD:
            return PHANTOM_PRESS_WEIGHT_OFFSET
    return None


def build_suggested_overrides(stats: "dict[str, AbilityStats]") -> dict:
    overrides = {}
    for ability, s in stats.items():
        offset = _score_ability(ability, s)
        if offset is not None:
            overrides[ability] = {"weight_offset": offset}
    return {"overrides": overrides}


def print_summary(stats: "dict[str, AbilityStats]") -> None:
    if not stats:
        print("[kb_engine] Aucune décision journalisée -- rien à analyser (telemetry.jsonl absent/vide).")
        return
    print(f"[kb_engine] {len(stats)} ability(-ies) distincte(s) journalisée(s) :")
    for ability, s in sorted(stats.items(), key=lambda kv: kv[1].press_count, reverse=True):
        sources = ", ".join(f"{src}={n}" for src, n in sorted(s.sources.items()))
        phantom_note = ""
        if s.confirmable_count > 0:
            phantom_note = f", phantom_rate={s.phantom_rate:.0%} (n={s.confirmable_count})"
        print(f"  - {ability}: {s.press_count} presse(s) [{sources}]{phantom_note}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--telemetry", default=str(DEFAULT_TELEMETRY_PATH))
    parser.add_argument("--press-confirm", default=str(DEFAULT_PRESS_CONFIRM_PATH))
    parser.add_argument("--out", default=str(DEFAULT_SUGGESTED_TUNING_PATH))
    args = parser.parse_args()

    telemetry_entries = read_telemetry(args.telemetry)
    press_confirmations = read_press_confirmations(args.press_confirm)
    stats = compute_ability_stats(telemetry_entries, press_confirmations)

    print_summary(stats)

    suggested = build_suggested_overrides(stats)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(suggested, indent=2, ensure_ascii=False), encoding="utf-8")

    if suggested["overrides"]:
        print(f"[kb_engine] {len(suggested['overrides'])} suggestion(s) écrite(s) dans {out_path} -- "
              f"SUGGESTION SEULEMENT, pas active (cf. human-in-the-loop). Pour l'activer, copie/édite "
              f"vers kb/combat_tuning.json APRÈS relecture.")
    else:
        print(f"[kb_engine] Aucun signal assez fort pour suggérer un offset -- {out_path} écrit vide.")


if __name__ == "__main__":
    main()
