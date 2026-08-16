#!/usr/bin/env python3
"""Tests for the one piece of this module that can lie to a person.

CallMeBot answers HTTP 200 for success, for an unauthorised recipient and for a rate
limit alike, with the difference buried in prose inside an HTML page. So the classifier
is the only thing standing between a call that did not happen and an app telling someone
that help is on the way.

Every string below is a REAL captured response, not a guess at what their API might say.
That matters: the first implementation of this classifier was written against imagined
output, searched the page for loose keywords like "ok" and "error", and misread an
actually-successful call as a failure.
"""
import unittest

from service import classify_callmebot, blocked

# Captured 2026-08-16 against the live API.
NOT_AUTHORISED = ("Checking Authorization for @zz_not_a_real_user_9931... Using bot: "
                  "@CallMeBot_API Authorization for user @zz_not_a_real_user_9931 is not "
                  "received. Warning! User not authorized. Click here to authorize")
RATE_LIMITED = ("ERROR: Two calls to the same user (@demo_contact) within 65 seconds is not "
                "allowed. This is to prevent the bot to overload the resources and colapse.")
SUCCESS = ("Checking Authorization for @demo_contact... Using bot: @CallMeBot_API16 "
           "Autorization OK User:@demo_contact Text to speech:Test call from Gozalti Safe "
           "Walk. Adi is checking the hackathon alert system. Nothing is wrong.")


class TestClassifier(unittest.TestCase):
    def test_success_is_recognised(self):
        """The regression that prompted this file: a real call reported as failed."""
        ok, why = classify_callmebot(SUCCESS)
        self.assertTrue(ok, f"successful call misread as failure: {why}")

    def test_success_tolerates_their_typo(self):
        """They spell it 'Autorization'. Matching only the correct spelling fails 100%
        of successful calls."""
        self.assertTrue(classify_callmebot("Autorization OK User:@x")[0])
        self.assertTrue(classify_callmebot("Authorization OK User:@x")[0])

    def test_unauthorised_fails_with_the_fix_in_the_message(self):
        ok, why = classify_callmebot(NOT_AUTHORISED)
        self.assertFalse(ok)
        self.assertIn("/start", why)

    def test_rate_limit_fails_and_names_the_window(self):
        ok, why = classify_callmebot(RATE_LIMITED)
        self.assertFalse(ok)
        self.assertIn("65", why)

    def test_unrecognised_is_a_failure_not_a_success(self):
        """An unknown response must never be optimistic. The person on the other end has
        just been told someone is being called."""
        ok, why = classify_callmebot("Some page we have never seen before.")
        self.assertFalse(ok)
        self.assertIn("unrecognised", why)

    def test_empty_is_a_failure(self):
        self.assertFalse(classify_callmebot("")[0])
        self.assertFalse(classify_callmebot(None)[0])

    def test_marketing_boilerplate_does_not_flip_the_verdict(self):
        """The old heuristic searched the whole page for 'ok' and 'error'. Both words
        appear in ordinary page furniture."""
        noisy = "Home Blog FAQ Error codes Contact OK " + SUCCESS
        self.assertTrue(classify_callmebot(noisy)[0])


class TestEmergencyRefusal(unittest.TestCase):
    """offpath-911 binding rule 1, enforced in code."""

    def test_emergency_numbers_refused(self):
        for n in ("911", "+1911", "999", "112", "988", "1-800-273-8255"):
            self.assertTrue(blocked(n), f"{n} must be refused")

    def test_ordinary_numbers_allowed(self):
        for n in ("+15551234567", "+14255550911", "5559110000", ""):
            self.assertFalse(blocked(n), f"{n} must not be refused")


if __name__ == "__main__":
    unittest.main(verbosity=2)
