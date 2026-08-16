#!/usr/bin/env python3
"""The confirmation dialogue — a deterministic state machine, not a language model.

WHY THIS IS NOT AN LLM
----------------------
This code decides whether to escalate a possible emergency. A language model in that
path buys nothing and costs three things that matter:

  * non-determinism — the same words must always produce the same decision, and must be
    reproducible afterwards when someone asks why it escalated
  * latency — an LLM turn is seconds; a person who has just said "no" is waiting
  * hallucination — an LLM can emit "Calling now" when nothing was called, which is the
    single worst failure this feature has

Apple's crash detection works the same way: detect, prompt, count down, act unless
dismissed. No model interprets the user's intent. We match that shape.

An LLM may still phrase the *prompts* (offline, ahead of time) — it must never make the
*decision*.

SAFETY PROPERTIES, each covered by a test in test_dialogue.py
------------------------------------------------------------
  1. ESCALATE is reachable only through an explicit affirmative to the escalation
     question. Never from silence, never from a low-confidence transcript, never from
     the first question alone.
  2. Anything unrecognised re-asks. After MAX_REPROMPTS it lands on NEEDS_ATTENTION —
     a visible banner — and stops. Silence never escalates.
  3. CANCEL is accepted in every non-terminal state and always wins.
  4. A negative answer to "everything good?" does NOT escalate; it asks the escalation
     question. Two separate affirmatives are required to reach ESCALATE.
  5. Low-confidence transcripts are treated as unrecognised, not as their best guess.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    IDLE = "idle"                      # nothing happening
    ASKED_WELLBEING = "asked_wellbeing"    # "everything good?"
    ASKED_ESCALATE = "asked_escalate"      # "should I contact ...?"
    RESOLVED_OK = "resolved_ok"            # user said they are fine — terminal
    ESCALATE = "escalate"                  # confirmed — terminal, hands off to offpath-911
    NEEDS_ATTENTION = "needs_attention"    # no usable answer — banner, terminal, NO escalation
    CANCELLED = "cancelled"                # user aborted — terminal


class Intent(str, Enum):
    YES = "yes"
    NO = "no"
    CANCEL = "cancel"
    HELP = "help"                      # explicit distress word — still confirmation-gated
    UNKNOWN = "unknown"


# Matched against a lowercased transcript, whole words only, longest-intent-first.
# Deliberately small: every phrase here is one a person actually says under stress, and
# a short list is auditable. Anything not listed is UNKNOWN, which is the safe default.
_PATTERNS: list[tuple[Intent, re.Pattern]] = [
    (Intent.CANCEL, re.compile(r"\b(cancel|stop|never ?mind|nevermind|abort|forget it|"
                               r"false alarm|disregard)\b")),
    (Intent.HELP, re.compile(r"\b(help|emergency|call 9 ?1 ?1|nine one one|"
                             r"i'?m in danger|somebody help)\b")),
    (Intent.NO, re.compile(r"\b(no|nope|nah|negative|not (ok|okay|good|fine|really)|"
                           r"i'?m not (ok|okay|good|fine))\b")),
    (Intent.YES, re.compile(r"\b(yes|yeah|yep|yup|correct|affirmative|do it|please do|"
                            r"go ahead|i'?m (ok|okay|good|fine)|all good)\b")),
]

# Below this ASR confidence a transcript is treated as if it were not heard at all.
# Mishearing "no" as "go" must not decide anything.
MIN_CONFIDENCE = 0.55
MAX_REPROMPTS = 2
ANSWER_TIMEOUT_S = 60.0


def classify(transcript: str, confidence: float = 1.0) -> Intent:
    """Transcript -> Intent. Unrecognised and low-confidence both yield UNKNOWN.

    Ambiguity is resolved by *refusing*, not by guessing: a transcript containing both a
    yes-word and a no-word ("no, yeah, I'm fine") is UNKNOWN and gets re-asked, because
    we cannot tell which one the person meant and the cost of choosing wrong is a false
    emergency call or a missed one.
    """
    if confidence < MIN_CONFIDENCE:
        return Intent.UNKNOWN
    text = (transcript or "").lower().strip()
    if not text:
        return Intent.UNKNOWN
    hits = {intent for intent, pat in _PATTERNS if pat.search(text)}
    if not hits:
        return Intent.UNKNOWN
    # Cancel and explicit distress dominate; they are unambiguous in intent even if the
    # sentence also contains other words.
    if Intent.CANCEL in hits:
        return Intent.CANCEL
    if Intent.HELP in hits:
        return Intent.HELP
    if hits == {Intent.YES}:
        return Intent.YES
    if hits == {Intent.NO}:
        return Intent.NO
    return Intent.UNKNOWN          # both yes and no present -> refuse to guess


PROMPTS = {
    State.ASKED_WELLBEING: "Hey, I noticed you went off path. Everything good?",
    State.ASKED_ESCALATE: "Should I contact emergency services with your exact location?",
    State.RESOLVED_OK: "Okay. I'll keep watching the route.",
    State.ESCALATE: "Contacting now. You can say cancel to stop.",
    State.NEEDS_ATTENTION: "I couldn't tell. I've left an alert on your screen.",
    State.CANCELLED: "Cancelled. Nothing was sent.",
}
REPROMPTS = {
    State.ASKED_WELLBEING: "Sorry, I didn't catch that. Are you okay? Yes or no.",
    State.ASKED_ESCALATE: "I need a clear answer. Should I contact emergency services? "
                          "Yes or no.",
}


@dataclass
class Turn:
    """One exchange, kept so the whole decision can be replayed afterwards."""
    at: float
    state_before: State
    transcript: str
    confidence: float
    intent: Intent
    state_after: State
    said: str


@dataclass
class Dialogue:
    """One confirmation conversation. Create on trigger, feed transcripts, read state.

    Never performs an action itself — reaching State.ESCALATE is a *report* that the
    user confirmed. modules/offpath-911 owns what happens next, including the hard rule
    that real emergency services are never dialed.
    """
    state: State = State.IDLE
    reprompts: int = 0
    started_at: float = field(default_factory=time.time)
    last_prompt_at: float = 0.0
    history: list[Turn] = field(default_factory=list)
    trigger: str = ""                  # "offpath" | "keyword" | "manual"

    TERMINAL = {State.RESOLVED_OK, State.ESCALATE, State.NEEDS_ATTENTION, State.CANCELLED}

    @property
    def done(self) -> bool:
        return self.state in self.TERMINAL

    def start(self, trigger: str = "offpath", now: float | None = None) -> str:
        """Begin the dialogue. Returns what to say."""
        now = time.time() if now is None else now
        self.state = State.ASKED_WELLBEING
        self.trigger = trigger
        self.started_at = now
        self.last_prompt_at = now
        self.reprompts = 0
        return PROMPTS[State.ASKED_WELLBEING]

    def hear(self, transcript: str, confidence: float = 1.0,
             now: float | None = None) -> str:
        """Feed one heard utterance. Returns what to say back."""
        now = time.time() if now is None else now
        before = self.state
        intent = classify(transcript, confidence)

        if self.done:
            return ""

        # Cancel wins everywhere, including mid-escalation.
        if intent is Intent.CANCEL:
            self.state = State.CANCELLED
            return self._record(now, before, transcript, confidence, intent,
                                PROMPTS[State.CANCELLED])

        if self.state is State.ASKED_WELLBEING:
            if intent is Intent.YES:            # "I'm fine"
                self.state = State.RESOLVED_OK
            elif intent in (Intent.NO, Intent.HELP):
                # Explicit distress does NOT skip the gate. It still asks, because the
                # confirmation is what protects against a misheard word.
                self.state = State.ASKED_ESCALATE
                self.reprompts = 0
            else:
                return self._reprompt(now, before, transcript, confidence, intent)

        elif self.state is State.ASKED_ESCALATE:
            if intent is Intent.YES or intent is Intent.HELP:
                self.state = State.ESCALATE
            elif intent is Intent.NO:
                self.state = State.RESOLVED_OK
            else:
                return self._reprompt(now, before, transcript, confidence, intent)

        self.last_prompt_at = now
        return self._record(now, before, transcript, confidence, intent,
                            PROMPTS[self.state])

    def tick(self, now: float | None = None) -> str:
        """Call periodically. Handles the silence case. NEVER escalates."""
        now = time.time() if now is None else now
        if self.done or self.state is State.IDLE:
            return ""
        if now - self.last_prompt_at < ANSWER_TIMEOUT_S:
            return ""
        before = self.state
        if self.reprompts >= MAX_REPROMPTS:
            self.state = State.NEEDS_ATTENTION
            return self._record(now, before, "", 0.0, Intent.UNKNOWN,
                                PROMPTS[State.NEEDS_ATTENTION])
        self.reprompts += 1
        self.last_prompt_at = now
        return self._record(now, before, "", 0.0, Intent.UNKNOWN, REPROMPTS[before])

    def cancel(self, now: float | None = None) -> str:
        """The always-available CANCEL (a button, not only a spoken word)."""
        now = time.time() if now is None else now
        before = self.state
        self.state = State.CANCELLED
        return self._record(now, before, "[cancel button]", 1.0, Intent.CANCEL,
                            PROMPTS[State.CANCELLED])

    def _reprompt(self, now, before, transcript, confidence, intent) -> str:
        if self.reprompts >= MAX_REPROMPTS:
            self.state = State.NEEDS_ATTENTION
            return self._record(now, before, transcript, confidence, intent,
                                PROMPTS[State.NEEDS_ATTENTION])
        self.reprompts += 1
        self.last_prompt_at = now
        return self._record(now, before, transcript, confidence, intent, REPROMPTS[before])

    def _record(self, now, before, transcript, confidence, intent, said) -> str:
        self.history.append(Turn(at=round(now, 3), state_before=before,
                                 transcript=transcript, confidence=confidence,
                                 intent=intent, state_after=self.state, said=said))
        return said

    def transcript_log(self) -> list[dict]:
        """The full exchange, for the demo debrief and for answering 'why did it do that'."""
        return [{"at": t.at, "from": t.state_before.value, "heard": t.transcript,
                 "confidence": t.confidence, "intent": t.intent.value,
                 "to": t.state_after.value, "said": t.said} for t in self.history]
