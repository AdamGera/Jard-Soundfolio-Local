import unittest

from overview_exchanges import find_exchanges, wording


def passages(*utterances):
    return [{"id": index + 1, "start": 4.0 * index, "text": text}
            for index, text in enumerate(utterances)]


class OverviewExchangeTests(unittest.TestCase):
    def test_contrast_keeps_both_positions_without_attributing_consensus(self):
        opening = "Don't archive the field notebooks. We still need the originals."
        reply = "The field notebooks should be archived once the scans are verified."
        found = find_exchanges(passages(f"Speaker A: {opening}", f"Speaker B: {reply}"))
        self.assertEqual([exchange["kind"] for exchange in found], ["contrast"])
        self.assertEqual(found[0]["statements"], [opening, reply])
        for option in wording(found[0], "en"):
            self.assertIn("Don't archive the field notebooks", option)
            self.assertIn(reply, option)
            self.assertNotIn("agreed", option.casefold())

    def test_conditional_visit_keeps_condition_before_destination(self):
        statement = "إذا وصل التمويل ممكن نزور مدريد لمدة أسبوع حسب توفر القاعة."
        found = find_exchanges(passages(f"Speaker A: {statement}"))
        self.assertEqual([exchange["kind"] for exchange in found], ["conditional"])
        self.assertEqual(found[0]["place"]["possiblePlace"], "Madrid")
        for option in wording(found[0], "ar"):
            self.assertIn("إذا وصل التمويل", option)
            self.assertIn("مدريد", option)
            self.assertIn("حسب توفر القاعة", option)

    def test_answer_confirming_a_condition_is_in_every_reading(self):
        proposal = "If the venue is free, we could hold the workshop for two days."
        response = "The venue is free. Yes, let's book the workshop."
        found = find_exchanges(passages(f"Speaker A: {proposal}", f"Speaker B: {response}"))
        self.assertEqual(len(found), 1)
        self.assertIn(response, found[0]["statements"])
        for option in wording(found[0], "en"):
            self.assertIn(proposal, option)
            self.assertIn(response, option)

    def test_unrelated_affirmative_is_not_confirmation_of_the_proposal(self):
        proposal = "If the office is closed, we could work from home for two days."
        found = find_exchanges(passages(f"Speaker A: {proposal}", "Speaker B: Yes, order more coffee."))
        self.assertEqual([item["statements"] for item in found], [[proposal]])

    def test_bare_repetition_of_a_subject_does_not_establish_a_counterargument(self):
        found = find_exchanges(passages(
            "Speaker A: We don't keep receipts in the blue folder.",
            "Speaker B: The blue folder is on the shelf.",
        ))
        self.assertFalse(any(item["kind"] == "contrast" for item in found))

    def test_clear_conditional_action_needs_no_place_or_repeated_phrase(self):
        proposal = "If the design review slips, we could move the release by two weeks."
        found = find_exchanges(passages(f"Speaker A: {proposal}"))
        self.assertEqual([exchange["kind"] for exchange in found], ["conditional"])
        self.assertIn(proposal, wording(found[0], "en")[0])


if __name__ == "__main__":
    unittest.main()
