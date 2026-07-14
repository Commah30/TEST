"""
freshness.py — mesurer l'âge d'une donnée perçue et la classer, sans jamais
décider quoi en faire (cf. discussion 2026-07-14, points 3-5 : "le gate ne
devrait pas savoir qu'on veut agir, il doit juste répondre : cette donnée
est-elle exploitable ?").

Module volontairement SANS dépendance lourde (ni mss, ni cv2, ni yaml) --
contrairement à wardenbot/state_update.py (qui, lui, produit les timestamps
que ce module classe), celui-ci ne fait qu'arithmétique + enum, testable
sans écran ni jeu, au même titre que wardenbot/engine.py.

Freshness (enum) remplace un booléen "frais/pas frais" par 3 états
(FRESH/STALE/EXPIRED) -- un futur Decision Planner (scoring réel, pas encore
construit) pourra baisser un score sur STALE plutôt que bloquer
inconditionnellement, un signal graduel étant plus riche qu'un simple True/
False. AUCUN scoring n'est implémenté ici : ce module s'arrête à la
classification, exprès (cf. discussion) -- ce qu'on FAIT de FRESH/STALE/
EXPIRED reste une décision de l'appelant (hooks.py, ou plus tard un
Planner), jamais de ce module.

FreshnessPolicy est PAR TYPE DE DONNÉE, pas globale -- un buff YOLO et une
lecture OCR ne vieillissent pas à la même vitesse (cf. discussion point 4) :
chaque source (cf. wardenbot/state_update.py, MEIKO_YOLO_FRESHNESS_POLICY/
MEIKO_OCR_FRESHNESS_POLICY/etc.) définit sa propre tolérance plutôt qu'un
seuil unique appliqué à tout.

EN: freshness.py — measure the age of a perceived piece of data and classify
EN: it, without ever deciding what to do about it (cf. the 2026-07-14
EN: discussion, points 3-5: "the gate shouldn't know we want to act, it should
EN: just answer: is this data usable?").

EN: Module deliberately WITHOUT a heavy dependency (no mss, no cv2, no yaml)
EN: -- unlike wardenbot/state_update.py (which produces the timestamps this
EN: module classifies), this one is just arithmetic + enum, testable without
EN: a screen or the game, same as wardenbot/engine.py.

EN: Freshness (enum) replaces a "fresh/not fresh" boolean with 3 states
EN: (FRESH/STALE/EXPIRED) -- a future Decision Planner (real scoring, not
EN: built yet) will be able to lower a score on STALE rather than block
EN: unconditionally, a graduated signal being richer than a plain True/False.
EN: NO scoring is implemented here: this module stops at classification, on
EN: purpose (cf. discussion) -- what to DO with FRESH/STALE/EXPIRED remains a
EN: decision for the caller (hooks.py, or later a Planner), never this
EN: module's.

EN: FreshnessPolicy is PER DATA TYPE, not global -- a YOLO buff and an OCR
EN: reading don't age at the same speed (cf. discussion point 4): each source
EN: (cf. wardenbot/state_update.py, MEIKO_YOLO_FRESHNESS_POLICY/
EN: MEIKO_OCR_FRESHNESS_POLICY/etc.) defines its own tolerance rather than a
EN: single threshold applied to everything.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Freshness(Enum):
    FRESH = "fresh"
    STALE = "stale"
    EXPIRED = "expired"


@dataclass(frozen=True)
class FreshnessPolicy:
    """fresh_ms/stale_ms EXPRIMÉS EN MILLISECONDES (comme le reste de la
    télémétrie du projet, cf. gcd_slack_ms) -- pas en secondes, pour éviter
    la confusion avec time.monotonic() qui, lui, reste en secondes.

    age_ms <= fresh_ms  -> FRESH   (aussi bon qu'une lecture à l'instant)
    age_ms <= stale_ms  -> STALE   (encore exploitable, mais dégradée)
    age_ms >  stale_ms  -> EXPIRED (à ne plus considérer comme fiable)

    EN: fresh_ms/stale_ms EXPRESSED IN MILLISECONDS (like the rest of the
    EN: project's telemetry, cf. gcd_slack_ms) -- not seconds, to avoid
    EN: confusion with time.monotonic() which stays in seconds.

    EN: age_ms <= fresh_ms  -> FRESH   (as good as a reading taken right now)
    EN: age_ms <= stale_ms  -> STALE   (still usable, but degraded)
    EN: age_ms >  stale_ms  -> EXPIRED (no longer to be trusted)
    """
    fresh_ms: float
    stale_ms: float

    def classify(self, age_ms: float) -> Freshness:
        if age_ms <= self.fresh_ms:
            return Freshness.FRESH
        if age_ms <= self.stale_ms:
            return Freshness.STALE
        return Freshness.EXPIRED


@dataclass(frozen=True)
class DetectionMeta:
    """Un seul horodatage (created_at) par source de détection -- PAS
    l'objet Snapshot entier (cf. wardenbot/state_update.py::MeikoDetectionSnapshot/
    TariqDetectionSnapshot) qui, lui, reste un dataclass MUTABLE construit
    incrémentalement champ par champ pendant tout un cycle de détection avant
    publication atomique -- le figer (frozen=True) casserait ce pattern de
    construction. DetectionMeta, à l'inverse, est un petit objet-valeur
    construit d'un coup (created_at connu dès l'instant de sa création),
    jamais muté après -- frozen=True est le bon choix ICI, pas sur les gros
    Snapshot.

    created_at : time.monotonic(), JAMAIS time.time() -- comme gcd_until/
    hard_lock_until/tout le reste du projet qui mesure des DURÉES (cf.
    engine.py). time.time() reste réservé à l'horodatage HUMAIN dans les
    logs (cf. press_confirm.log), jamais à un calcul d'âge.

    EN: A single timestamp (created_at) per detection source -- NOT the whole
    EN: Snapshot object (cf. wardenbot/state_update.py::MeikoDetectionSnapshot/
    EN: TariqDetectionSnapshot) which stays a MUTABLE dataclass built
    EN: incrementally field by field throughout a whole detection cycle before
    EN: atomic publication -- freezing it (frozen=True) would break that
    EN: construction pattern. DetectionMeta, conversely, is a small value
    EN: object built all at once (created_at known the instant it's created),
    EN: never mutated afterward -- frozen=True is the right choice HERE, not
    EN: on the big Snapshots.

    EN: created_at: time.monotonic(), NEVER time.time() -- like gcd_until/
    EN: hard_lock_until/everything else in the project that measures
    EN: DURATIONS (cf. engine.py). time.time() stays reserved for HUMAN
    EN: timestamps in logs (cf. press_confirm.log), never for an age
    EN: computation.
    """
    created_at: float

    def age_ms(self, now: Optional[float] = None) -> float:
        """now=None (défaut) -> time.monotonic() au moment de l'appel.
        Paramètre explicite surtout pour les tests (cf. discussion point 7 :
        'snapshot.created_at = now - 4s -> assert ... == EXPIRED', sans
        dépendre du temps réel qui s'écoule pendant que le test tourne).

        EN: now=None (default) -> time.monotonic() at call time. Explicit
        EN: parameter mainly for tests (cf. discussion point 7: 'snapshot.
        EN: created_at = now - 4s -> assert ... == EXPIRED', without depending
        EN: on real time elapsing while the test runs).
        """
        current = now if now is not None else time.monotonic()
        return (current - self.created_at) * 1000.0
