#!/usr/bin/env python3
"""Tests for the confirmation dialogue. Stdlib unittest, no deps, runs anywhere in ms.

This is safety-critical logic: it decides whether an emergency escalation is reported.
The tests are written as the safety properties themselves, so a failure names the
property that broke rather than an implementation detail.

  ./test_dialogue.py
"""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dialogue import (Dialogue, State, Intent, classify, MAX_REPROMPTS,
                      ANSWER_TIMEOUT_S, MIN_CONFIDENCE)


class TestClassify(unittest.TestCase):
    def test_plain_answers(self):
        for text in ("yes", "Yeah", "yep", "go ahead", "please do", "affirmative"):
            self.assertEqual(classify(text), Intent.YES, text)
        for text in ("no", "nope", "nah", "I'm not okay", "not good"):
            self.assertEqual(classify(text), Intent.NO, text)
        for text in ("cancel", "stop", "never mind", "false alarm", "disregard"):
            self.assertEqual(classify(text), Intent.CANCEL, text)
        for text in ("help", "emergency", "call 911", "I'm in danger"):
            self.assertEqual(classify(text), Intent.HELP, text)

    def test_unrecognised_is_unknown_not_a_guess(self):
        for text in ("", "   ", "uhh", "what", "the weather is nice", "banana"):
            self.assertEqual(classify(text), Intent.UNKNOWN, repr(text))

    def test_low_confidence_is_unknown_however_clear_the_words(self):
        """Mishearing must not decide. 'no' at 0.4 confidence is not a no."""
        self.assertEqual(classify("no", confidence=MIN_CONFIDENCE - 0.01), Intent.UNKNOWN)
        self.assertEqual(classify("yes", confidence=0.1), Intent.UNKNOWN)
        self.assertEqual(classify("yes", confidence=MIN_CONFIDENCE + 0.01), Intent.YES)

    def test_contradictory_transcript_refuses_to_guess(self):
        """'no, yeah I'm fine' contains both. We cannot tell, so we re-ask."""
        self.assertEqual(classify("no yeah I'm fine"), Intent.UNKNOWN)
        self.assertEqual(classify("yes no"), Intent.UNKNOWN)

    def test_cancel_dominates_a_mixed_sentence(self):
        self.assertEqual(classify("yes, actually cancel that"), Intent.CANCEL)

    def test_substring_does_not_match(self):
        """'nope' inside 'nopetheless' or 'no' inside 'now' must not fire."""
        self.assertEqual(classify("now"), Intent.UNKNOWN)
        self.assertEqual(classify("nothing"), Intent.UNKNOWN)
        self.assertEqual(classify("yesterday"), Intent.UNKNOWN)


class TestSafetyProperties(unittest.TestCase):
    """One test per binding rule in modules/offpath-911/SPEC.md."""

    def test_PROPERTY_escalation_requires_two_explicit_answers(self):
        d = Dialogue(); d.start()
        self.assertEqual(d.state, State.ASKED_WELLBEING)
        d.hear("no")                                   # not okay
        self.assertEqual(d.state, State.ASKED_ESCALATE,
                         "a single 'no' must NOT escalate — it must ask")
        d.hear("yes")                                  # confirm escalation
        self.assertEqual(d.state, State.ESCALATE)

    def test_PROPERTY_silence_never_escalates(self):
        d = Dialogue(); t = 1000.0
        d.start(now=t)
        for _ in range(MAX_REPROMPTS + 3):
            t += ANSWER_TIMEOUT_S + 1
            d.tick(now=t)
            self.assertNotEqual(d.state, State.ESCALATE,
                                "silence must never reach ESCALATE")
        self.assertEqual(d.state, State.NEEDS_ATTENTION)

    def test_PROPERTY_unknown_answers_never_escalate(self):
        d = Dialogue(); d.start()
        d.hear("no")
        for _ in range(MAX_REPROMPTS + 2):
            d.hear("mumble mumble")
            self.assertNotEqual(d.state, State.ESCALATE)
        self.assertEqual(d.state, State.NEEDS_ATTENTION)

    def test_PROPERTY_low_confidence_yes_does_not_escalate(self):
        d = Dialogue(); d.start()
        d.hear("no", confidence=1.0)
        d.hear("yes", confidence=0.2)
        self.assertNotEqual(d.state, State.ESCALATE,
                            "a barely-heard 'yes' must not trigger an emergency")

    def test_PROPERTY_cancel_works_in_every_nonterminal_state(self):
        for setup in (lambda d: None,
                      lambda d: d.hear("no"),
                      lambda d: (d.hear("no"), d.hear("what"))):
            d = Dialogue(); d.start(); setup(d)
            self.assertNotIn(d.state, Dialogue.TERMINAL)
            d.hear("cancel")
            self.assertEqual(d.state, State.CANCELLED)

    def test_PROPERTY_cancel_button_also_works(self):
        d = Dialogue(); d.start(); d.hear("no")
        d.cancel()
        self.assertEqual(d.state, State.CANCELLED)

    def test_PROPERTY_cancel_after_escalation_still_registers(self):
        """ESCALATE is terminal for the dialogue, but the user must still be able to
        abort the action — offpath-911 reads CANCELLED and stops."""
        d = Dialogue(); d.start(); d.hear("no"); d.hear("yes")
        self.assertEqual(d.state, State.ESCALATE)
        d.cancel()
        self.assertEqual(d.state, State.CANCELLED)

    def test_PROPERTY_explicit_help_still_asks_for_confirmation(self):
        """'help' is the strongest word we accept and it STILL does not skip the gate,
        because a single misheard word must not summon responders."""
        d = Dialogue(); d.start()
        d.hear("help")
        self.assertEqual(d.state, State.ASKED_ESCALATE)
        self.assertNotEqual(d.state, State.ESCALATE)

    def test_PROPERTY_user_is_fine_resolves_without_escalating(self):
        d = Dialogue(); d.start()
        d.hear("yeah I'm fine")
        self.assertEqual(d.state, State.RESOLVED_OK)

    def test_PROPERTY_declining_escalation_resolves(self):
        d = Dialogue(); d.start()
        d.hear("no")                       # not okay
        d.hear("no")                       # but don't call
        self.assertEqual(d.state, State.RESOLVED_OK)


class TestFlowAndLogging(unittest.TestCase):
    def test_canonical_spec_flow(self):
        """The exact exchange in modules/audio-lm/SPEC.md."""
        d = Dialogue()
        self.assertIn("off path", d.start(trigger="offpath"))
        self.assertIn("emergency services", d.hear("No."))
        said = d.hear("Yes.")
        self.assertEqual(d.state, State.ESCALATE)
        self.assertIn("Contacting", said)

    def test_terminal_states_ignore_further_input(self):
        d = Dialogue(); d.start(); d.hear("yeah I'm fine")
        self.assertEqual(d.hear("yes"), "")
        self.assertEqual(d.state, State.RESOLVED_OK)

    def test_transcript_log_replays_the_decision(self):
        d = Dialogue(); d.start(); d.hear("no", 0.9); d.hear("yes", 0.95)
        log = d.transcript_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[-1]["to"], "escalate")
        self.assertEqual(log[-1]["intent"], "yes")
        self.assertEqual(log[-1]["confidence"], 0.95)
        for row in log:                       # every hop is auditable
            self.assertIn("from", row); self.assertIn("said", row)

    def test_reprompt_then_recover(self):
        d = Dialogue(); d.start()
        d.hear("uhh")
        self.assertEqual(d.state, State.ASKED_WELLBEING)
        self.assertEqual(d.reprompts, 1)
        d.hear("no")
        self.assertEqual(d.state, State.ASKED_ESCALATE)
        self.assertEqual(d.reprompts, 0, "reprompt counter resets on a good answer")

    def test_tick_before_timeout_is_silent(self):
        d = Dialogue(); t = 500.0
        d.start(now=t)
        self.assertEqual(d.tick(now=t + ANSWER_TIMEOUT_S - 1), "")
        self.assertEqual(d.state, State.ASKED_WELLBEING)


if __name__ == "__main__":
    unittest.main(verbosity=2)
