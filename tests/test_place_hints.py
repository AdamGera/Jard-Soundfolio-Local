import unittest

from place_hints import place_hints


class PlaceHintTests(unittest.TestCase):
    def test_independent_proposals_keep_original_spelling(self):
        cases = [
            ("We could visit Madrd next month.", "Madrd", "Madrid"),
            ("قد نزور بيرلين في الصيف", "بيرلين", "Berlin"),
            ("אנחנו חושבים על טיסה ללונדן בשבוע הבא", "ללונדן", "London"),
        ]
        for text, heard, place in cases:
            with self.subTest(text=text):
                row = {"id": 7, "text": text}
                self.assertEqual(place_hints([row]), {7: {"asHeard": heard, "possiblePlace": place}})
                self.assertEqual(row["text"], text)

    def test_requires_destination_context_and_an_unambiguous_candidate(self):
        for text in ["The code word is Madrd.", "The trip was great. Madrd is the project name.",
                     "A nice trip to the coast", "We could travel to london to see a friend.",
                     "We may visit Madrid and Berlin.", "بيرلين اسم المشروع"]:
            with self.subTest(text=text):
                self.assertEqual(place_hints([{"id": 1, "text": text}]), {})
