"""
input_sim.py — simulation de touches clavier.

pydirectinput plutôt que pyautogui : beaucoup de jeux (et notamment les jeux
DirectX plein écran) ignorent les événements SendInput classiques de
pyautogui mais réagissent à pydirectinput (scan codes bas niveau). Si tu
constates des touches "avalées" en jeu mais qui marchent en dry-run, c'est
typiquement ce genre de souci — teste d'abord en jeu réel avant de creuser le
reste du moteur.

NOTE (2026-07-12) : pydirectinput utilise DÉJÀ SendInput (avec scan codes)
en interne -- ce n'est pas pyautogui/SendMessage. Rien à changer de ce
côté-là. Ce qui manquait vraiment : tous les délais (durée de presse,
espacement des modificateurs) étaient des constantes FIXES (time.sleep(0.05)
partout, durée de presse par défaut de pydirectinput identique à chaque
appel) -- un motif parfaitement régulier, contrairement à une vraie presse
humaine. HUMANIZE_JITTER_* introduit une variation aléatoire (durée de
presse, espacement modificateur/touche) sur chaque appel -- répond aussi à
un vrai souci de fiabilité déjà rencontré : le bug Shift+3 (modificateur
perdu, cf. CLAUDE.md/historique) venait de transitions keyDown/keyUp trop
rapprochées et parfaitement déterministes.
"""
from __future__ import annotations

import queue
import random
import threading
import time
from typing import Callable, Optional

try:
    import pydirectinput as _input
    _input.PAUSE = 0  # on gère notre propre timing (gcd_ms), pas celui de la lib
except ImportError:
    _input = None

# Fourchettes de jitter (secondes) -- valeurs plausibles pour une vraie presse
# humaine (ni trop courtes/robotiques, ni assez longues pour rater un GCD).
HUMANIZE_HOLD_S = (0.035, 0.09)      # durée keyDown -> keyUp de la touche principale
HUMANIZE_MOD_GAP_S = (0.04, 0.09)    # espacement modificateur -> touche principale
HUMANIZE_MOD_RELEASE_S = (0.03, 0.07)  # espacement avant de relâcher les modificateurs

# Profil allégé (2026-07-12) -- pour les abilities qui spamment beaucoup
# (wild_swing en filler, skull_crusher en dump de Fury) : la variance reste
# réelle (toujours randomisée, pas de motif fixe) mais sur une fourchette
# plus courte -- moins de temps perdu cumulé sur des dizaines de presses/min,
# tout en gardant l'essentiel (jamais deux presses identiques au ms près).
# Réservé aux abilities à faible enjeu -- PAS touché pour les procs/cooldowns
# importants, où la marge de sécurité contre un GCD raté compte plus que
# quelques ms économisés.
HUMANIZE_HOLD_S_LIGHT = (0.015, 0.03)


class InputSimulator:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run or _input is None
        if self.dry_run and _input is None:
            print("[input_sim] pydirectinput non installé -> mode dry-run forcé.")

    def press(self, key: str, label: str | None = None, light: bool = False) -> tuple[float, float]:
        """
        key : soit une touche seule ("3"), soit une combinaison avec
        modificateur(s) séparés par "+" ("shift+3", "ctrl+alt+x"...) --
        maintient les modificateurs enfoncés (keyDown) pendant la presse de
        la touche principale, puis les relâche (keyUp) dans l'ordre inverse.
        Tous les délais sont randomisés (cf. HUMANIZE_* ci-dessus) plutôt que
        fixes -- pas de motif de timing identique d'une presse à l'autre.

        light=True : utilise HUMANIZE_HOLD_S_LIGHT au lieu de HUMANIZE_HOLD_S
        pour la touche principale -- réservé aux fillers/spam (cf. wild_swing
        dans main.py), jamais aux combos à modificateur (l'espacement
        modificateur reste HUMANIZE_MOD_GAP_S/RELEASE_S dans tous les cas,
        c'est lui qui protège contre le bug Shift+3).

        Retourne (keydown_ts, keyup_ts) de la touche PRINCIPALE (pas des
        modificateurs) -- utilisé par InputWorker pour l'instrumentation
        ciblée de hammer_storm_proc (cf. diagnostics.py::log_input_timing,
        2026-07-13 : distinguer une frappe trop brève/chevauchée -- même
        famille que le bug Shift+3 -- d'une frappe qui part normalement mais
        que le jeu ignore quand même).
        """
        if self.dry_run:
            tag = f"{label} -> " if label else ""
            now = time.time()
            print(f"[DRY RUN] {tag}press: {key}  (t={now:.3f})")
            return (now, now)
        hold_range = HUMANIZE_HOLD_S_LIGHT if light else HUMANIZE_HOLD_S
        if "+" in key:
            # Petits délais entre chaque étape -- des appels keyDown/press/
            # keyUp dos-à-dos sans pause peuvent faire perdre l'état du
            # modificateur au jeu (le press() de la touche principale part
            # avant que le jeu ait enregistré le keyDown de shift), et le
            # jeu reçoit alors la touche SEULE au lieu de la combinaison
            # (ex. "3" au lieu de "shift+3" -- suspecté sur Culling Strike).
            *modifiers, base_key = key.split("+")
            for mod in modifiers:
                _input.keyDown(mod.strip().lower())
                time.sleep(random.uniform(*HUMANIZE_MOD_GAP_S))
            base_key = base_key.strip()
            keydown_ts = time.time()
            _input.keyDown(base_key)
            time.sleep(random.uniform(*hold_range))
            _input.keyUp(base_key)
            keyup_ts = time.time()
            time.sleep(random.uniform(*HUMANIZE_MOD_RELEASE_S))
            for mod in reversed(modifiers):
                _input.keyUp(mod.strip().lower())
        else:
            keydown_ts = time.time()
            _input.keyDown(key)
            time.sleep(random.uniform(*hold_range))
            _input.keyUp(key)
            keyup_ts = time.time()
        return (keydown_ts, keyup_ts)


class InputWorker:
    """Sépare la boucle de décision (RotationEngine.tick()) de l'exécution
    physique des touches -- discuté le 2026-07-13, en attente des résultats
    du diagnostic de focus avant de s'y lancer.

    AVANT : RotationEngine.tick() appelait press_fn() -> InputSimulator.press()
    directement et de façon BLOQUANTE -- keyDown/sleep(30-90ms, plus pour un
    combo modificateur)/keyUp tournaient sur le MÊME thread que la décision.
    À 60Hz (budget 16.67ms/tick pour Tariq), chaque presse explosait donc le
    budget du tick en cours, gelant la boucle de décision pendant tout le
    hold -- même principe de goulot que celui déjà résolu côté détection
    (cf. wardenbot/state_update.py) mais jamais traité côté input.

    APRÈS : request_press() ne fait qu'empiler la demande dans une queue et
    revient immédiatement (quasi zéro coût pour le tick appelant) -- un
    thread dédié dépile et exécute les presses, dans l'ordre, sur son propre
    fil d'exécution. La logique de presse elle-même (humanisation,
    keyDown/keyUp, gestion des modificateurs) ne change pas du tout --
    seul QUI l'appelle et QUAND change.

    on_before_press(label), optionnel : appelé juste avant la vraie presse,
    DANS le thread d'input -- plus précis que de l'appeler au moment de
    l'enqueue (decision time), puisque la queue peut (rarement) accumuler un
    léger retard entre décision et exécution réelle. Utilisé pour
    check_foreground_window (cf. main.py) : le diagnostic de focus doit
    checker l'état AU MOMENT de la vraie presse, pas au moment où la
    décision a été prise.
    """

    # Instrumentation input (2026-07-13). Élargie à TOUTES les abilities
    # (TRACKED_LABELS=None) suite au retour de l'utilisateur : le symptôme visuel
    # ("l'icône flash sans effet") apparaît sur plusieurs abilities au
    # hasard, pas seulement hammer_storm_proc -- limiter le tracking à une
    # seule ability n'aurait donc plus de sens, le suspect (decision ->
    # InputWorker.enqueue() -> OS/pydirectinput -> jeu) est générique à
    # toutes les presses, pas spécifique à Hammer Storm.
    #
    # ATTENTION -- angle mort qui reste MÊME avec ce tracking élargi : ce
    # log prouve seulement que la touche est bien partie proprement côté
    # InputWorker (keydown/keyup, pas de chevauchement, pas de retard de
    # queue). Il ne prouve PAS que le jeu a réellement réagi -- hammer_storm_proc
    # est la SEULE ability avec une vraie confirmation côté jeu (la cast bar,
    # hammer_storm_casting) ; heavy_strike/skull_crusher/face_breaker/
    # chain_lightning/wild_swing/etc. n'ont aucun signal de retour, le bot
    # suppose juste que la presse a marché. Si ce log montre "tout est
    # normal" (cas C) pour une ability SANS confirmation dédiée, ça ne
    # blanchit pas InputWorker pour autant -- ça veut juste dire qu'on ne
    # peut rien conclure de plus sans construire une confirmation par
    # ability, un chantier plus lourd, pas encore fait.
    TRACKED_LABELS: Optional[frozenset] = None  # None = toutes les abilities

    # QUEUE NON BORNÉE (audit architecture, 2026-07-18) : queue.Queue() sans
    # maxsize -- si le worker se bloquait durablement (deadlock, exception en
    # boucle malgré le try/except plus bas...), rien n'empêchait
    # request_press() d'empiler indéfiniment des presses de plus en plus
    # périmées, chacune exécutée bien après que la décision qui l'a produite
    # ait cessé d'être pertinente. MAX_QUEUE_SIZE=32 fixé à partir de données
    # RÉELLES (input_timing.log, une vraie session Tariq) plutôt qu'inventé :
    # sur 34546 presses journalisées, queue_depth_enqueue vaut 0 dans 99.3%
    # des cas, 1 dans 0.7%, 2 dans 0.03%, JAMAIS plus -- 32 reste donc à des
    # années-lumière de tout fonctionnement normal observé, tout en bornant
    # le cas pathologique. cf. request_press() pour le comportement à queue
    # pleine (drop + log, jamais de blocage).
    # EN: UNBOUNDED QUEUE (architecture audit, 2026-07-18): queue.Queue() with
    # EN: no maxsize -- if the worker got durably stuck (deadlock, an
    # EN: exception looping despite the try/except below...), nothing stopped
    # EN: request_press() from stacking presses indefinitely, each growing
    # EN: more stale, executed long after the decision that produced it
    # EN: stopped being relevant. MAX_QUEUE_SIZE=32 set from REAL data
    # EN: (input_timing.log, an actual Tariq session) rather than invented: out
    # EN: of 34546 logged presses, queue_depth_enqueue is 0 in 99.3% of cases,
    # EN: 1 in 0.7%, 2 in 0.03%, NEVER more -- 32 is therefore light-years away
    # EN: from any normal operation observed, while still bounding the
    # EN: pathological case. cf. request_press() for full-queue behavior (drop
    # EN: + log, never blocking).
    MAX_QUEUE_SIZE = 32

    def __init__(
        self,
        input_sim: InputSimulator,
        on_before_press: Optional[Callable[[str], None]] = None,
        on_press_result: Optional[Callable[[str, bool, float], None]] = None,
    ):
        """on_press_result(label, success, executed_at) -- PROTOCOLE DE
        CONFIRMATION (audit architecture, 2026-07-18, cf. engine.py::
        RotationEngine.report_press_outcome pour le consommateur). Appelé
        DEPUIS ce thread d'input (jamais celui de la décision) juste après
        chaque tentative de presse RÉELLE (succès ou échec, cf. _run) ET
        depuis request_press() elle-même quand une demande est abandonnée
        faute de place (cf. MAX_QUEUE_SIZE). executed_at : time.monotonic(),
        PAS time.time() -- comparable directement à state.cooldown_until/
        gcd_until côté RotationEngine (cf. principe du projet : jamais
        time.time() pour un calcul de durée). None (défaut) = aucun
        changement de comportement pour un appelant qui ne le branche pas
        (ex. tous les tests de ce fichier).

        EN: on_press_result(label, success, executed_at) -- CONFIRMATION
        EN: PROTOCOL (architecture audit, 2026-07-18, cf. engine.py::
        EN: RotationEngine.report_press_outcome for the consumer). Called
        EN: FROM this input thread (never the decision one) right after every
        EN: REAL press attempt (success or failure, cf. _run) AND from
        EN: request_press() itself when a request gets dropped for lack of
        EN: room (cf. MAX_QUEUE_SIZE). executed_at: time.monotonic(), NOT
        EN: time.time() -- directly comparable to state.cooldown_until/
        EN: gcd_until on RotationEngine's side (cf. the project's principle:
        EN: never time.time() for a duration computation). None (default) =
        EN: no behavior change for a caller that doesn't wire it (e.g. every
        EN: test in this file).
        """
        self._input_sim = input_sim
        self._on_before_press = on_before_press
        self.on_press_result = on_press_result
        self._queue: "queue.Queue[tuple[str, str, bool, float, int]]" = queue.Queue(maxsize=self.MAX_QUEUE_SIZE)
        self._stop_event = threading.Event()
        self._last_press_end_ts: Optional[float] = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="input-worker")
        self._thread.start()

    def _run(self) -> None:
        from wardenbot.diagnostics import log_error, log_input_timing  # import tardif -- évite un cycle d'import au chargement du module

        while not self._stop_event.is_set():
            try:
                key, label, light, enqueue_ts, queue_depth_at_enqueue = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            dequeue_ts = time.time()
            queue_depth_at_execution = self._queue.qsize()
            # Combien de temps le worker était encore occupé par la presse
            # PRÉCÉDENTE après que celle-ci ait été enqueue -- si > 0, le
            # worker était déjà en train d'exécuter autre chose au moment où
            # cette demande est arrivée (cas A : la queue retarde la frappe).
            worker_busy_before_s = (
                max(0.0, self._last_press_end_ts - enqueue_ts) if self._last_press_end_ts is not None else 0.0
            )
            track = self.TRACKED_LABELS is None or label in self.TRACKED_LABELS
            keydown_ts = keyup_ts = None
            success = False
            try:
                if self._on_before_press is not None:
                    self._on_before_press(label)
                keydown_ts, keyup_ts = self._input_sim.press(key, label=label, light=light)
                success = True
            except Exception as exc:
                # ROBUSTESSE (même principe que le thread de détection et
                # RotationEngine.run_forever) : une exception ici ne doit
                # jamais tuer silencieusement le thread d'input -- ce serait
                # un bot qui continue de "décider" sans plus jamais rien
                # presser, sans aucune trace visible en jeu.
                log_error(f"input_worker.press({label!r})", exc)
            finally:
                self._last_press_end_ts = time.time()
            if self.on_press_result is not None:
                # Protocole de confirmation (cf. __init__) -- success=False
                # ici couvre l'exception ci-dessus (rien n'a réellement été
                # pressé, malgré on_before_press/le dequeue qui, eux, ont bien
                # eu lieu).
                # EN: Confirmation protocol (cf. __init__) -- success=False
                # EN: here covers the exception above (nothing was actually
                # EN: pressed, despite on_before_press/the dequeue, which DID
                # EN: happen).
                self.on_press_result(label, success, time.monotonic())
            if track:
                log_input_timing(
                    label=label,
                    enqueue_ts=enqueue_ts,
                    dequeue_ts=dequeue_ts,
                    keydown_ts=keydown_ts,
                    keyup_ts=keyup_ts,
                    queue_depth_at_enqueue=queue_depth_at_enqueue,
                    queue_depth_at_execution=queue_depth_at_execution,
                    worker_busy_duration_before_press_s=worker_busy_before_s,
                )

    def request_press(self, key: str, label: str, light: bool = False) -> None:
        """Non-bloquant -- empile la demande, le thread d'input l'exécute
        dès qu'il est libre (généralement en quelques ms, jamais plus que la
        durée de la presse précédente).

        Queue PLEINE (cf. MAX_QUEUE_SIZE) : DROP la nouvelle demande plutôt
        que de bloquer ce thread (celui de la décision, RotationEngine.tick())
        ou d'évincer une entrée déjà en attente -- put_nowait() lève
        queue.Full immédiatement, jamais d'attente. Journalisé via log_error
        (jamais silencieux) : à ce niveau de profondeur (32, cf. commentaire
        de classe -- jamais observé au-delà de 2 en jeu réel), la queue pleine
        signale déjà quelque chose de cassé (worker mort/bloqué) -- perdre
        UNE presse de plus n'aggrave pas la situation, la faire disparaître
        du log la cacherait. on_press_result(label, False, ...) est APPELÉ
        ICI MÊME, SUR CE thread (celui de la décision, pas celui d'input --
        seul cas où le protocole de confirmation ne vient pas du thread
        d'input) : la demande n'a même pas atteint la queue, il n'y a pas de
        thread d'input à attendre pour le savoir.
        """
        enqueue_ts = time.time()
        queue_depth_at_enqueue = self._queue.qsize()
        try:
            self._queue.put_nowait((key, label, light, enqueue_ts, queue_depth_at_enqueue))
        except queue.Full:
            from wardenbot.diagnostics import log_error  # import tardif -- même raison que _run()
            log_error(
                f"input_worker.request_press({label!r})",
                queue.Full(f"queue pleine (maxsize={self.MAX_QUEUE_SIZE}) -- presse abandonnée, worker probablement bloqué"),
            )
            if self.on_press_result is not None:
                self.on_press_result(label, False, time.monotonic())

    def stop(self) -> None:
        self._stop_event.set()