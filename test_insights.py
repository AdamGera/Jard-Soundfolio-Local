import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from insights import Insights, ObsoleteGeneration, note_identity, anchored_topics, markdown_summary, obj, STRING, validate_translation, validate_language, ground_quantities
from local_qwen import LocalQwen
from title_words import word_ends, compact_title
from session_notes import passages
from session_store import SessionStore


class InsightsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        files = tempfile.TemporaryDirectory()
        self.addCleanup(files.cleanup)
        root = Path(files.name)
        self.store = SessionStore(root)
        source = root / "fixture"
        source.write_bytes(b"fixture")
        self.key, _ = self.store.create(source, "example", "audio/wav", {"diarization": False, "recording": False}, 4)
        self.store.append(self.key, {"text": "The launch is Friday.", "language": "English", "segments": [],
                                     "words": []}, 4)
        self.store.finish(self.key)
        self.insights = object.__new__(Insights)
        self.insights.store = self.store
        self.insights.model = AsyncMock()
        self.insights.model.tokens.return_value = 42
        with self.store.connection() as db:
            self.rows = passages(db, self.key)
            self.expected = note_identity(db, self.key)

    async def test_retry_after_title_uses_saved_brief_and_title(self):
        model = self.insights.model
        brief = {"understanding": "Launch review", "writing_brief": "Explain the decision to move the launch."}
        title = {"title": "Launch moved to Friday"}
        summary = {"lead": "The launch is Friday.", "sections": [{"heading": "Date", "points": ["Friday."]}]}
        timeline = {"topics": [{"id": 1, "label": "Launch", "detail": "They agreed to launch on Friday."}]}
        model.structured.side_effect = [brief, title, RuntimeError("temporary error")]
        with self.assertRaises(RuntimeError):
            await self.insights.write(self.key, self.rows, "source evidence", "English", self.expected)
        self.assertEqual(model.structured.call_args_list[2].kwargs["context"], brief)
        model.structured.reset_mock()
        model.structured.side_effect = [summary, timeline]
        result = await self.insights.write(self.key, self.rows, "source evidence", "English", self.expected)
        self.assertEqual(model.structured.call_count, 2)
        self.assertEqual(result["markdown"], "## Date\n\n- Friday.")
        self.assertEqual(result["topics"][0]["detail"], "They agreed to launch on Friday.")
        self.assertEqual(result["title"], title["title"])

    async def test_localization_keeps_exact_anchor_and_order(self):
        original = {"title": "Friday", "summary": "The launch is Friday.", "markdown": "## Date\n\n- Friday.",
                    "topics": [{"id": 1, "start": 2.125, "label": "Launch", "detail": "Friday."}]}
        self.insights.model.structured.side_effect = [
            {"translatedTitle": "جمعه", "translatedLead": "جمعه", "translatedMarkdown": "## جمعه"}, {"chapter1": {"label": "روز", "detail": "جمعه"}}]
        translated = await self.insights.localize(original, "Persian", self.rows)
        self.assertEqual([(t["id"], t["start"]) for t in translated["topics"]], [(1, 2.125)])
        self.assertEqual(translated["topics"][0]["detail"], "جمعه")

    async def test_stream_preserves_split_citations_and_discards_invalid_ids(self):
        async def output(*args, **kwargs):
            for token in ["Friday [", "P", "1", "].", " [P8]"]:
                yield token
        self.insights.model.generate = output
        answer = "".join([s async for s in self.insights.chat_tokens(self.key, "When?", [])])
        self.assertIn("⟦0⟧", answer)
        self.assertNotIn("[P", answer)

    async def test_refinalization_during_work_discards_stale_checkpoint(self):
        async def generate():
            with self.store.connection() as db:
                db.execute("UPDATE notes SET source_digest='different' WHERE id=?", (self.key,))
            return {"title": "Old title"}
        with self.assertRaises(ObsoleteGeneration):
            await self.insights.checkpoint(self.key, -2, generate, self.expected)
        with self.store.connection() as db:
            self.assertIsNone(db.execute("SELECT data FROM window_notes WHERE id=?", (self.key,)).fetchone())

    def test_chapters_validate_ids_and_preserve_chronology(self):
        rows = [{"id": 1, "start": 1.5}, {"id": 2, "start": 3.0}]
        topics = [dict(id=i, label="Topic", detail="Detail") for i in (2, 99, 1, 2)]
        self.assertEqual([c["start"] for c in anchored_topics(topics, rows)], [1.5, 3.0])

    def test_quote_corrects_model_that_numbers_its_chapters(self):
        rows = [{"id": 1, "start": 0, "text": "The design is in review."},
                {"id": 2, "start": 25, "text": "The external reviewer is traveling."},
                {"id": 3, "start": 50, "text": "We approved the printing budget for March."}]
        topics = [{"id": 2, "quote": "approved the printing budget", "label": "Budget", "detail": "Approved."}]
        self.assertEqual(anchored_topics(topics, rows)[0]["start"], 50)

    async def test_retries_overlong_titles_and_caps_on_word_boundaries(self):
        long = "A very long and excessively verbose transcript title"
        self.insights.model.structured.return_value = {"shortTitle": "Museum budget goals and priorities for the upcoming exhibition"}
        title = await self.insights.short_title(long, "English")
        self.insights.model.structured.assert_awaited_once()
        self.assertEqual(title, "Museum budget goals")
        self.assertEqual(compact_title("Title: “Map budget”", "en"), "Map budget")
        chinese = "博物馆展览开幕日期预算讨论"
        shortened = compact_title(chinese, "zh")
        self.assertTrue(chinese.startswith(shortened))
        self.assertLessEqual(len(word_ends(shortened, "zh")), 5)

    async def test_semantically_invalid_translation_never_poisons_retry(self):
        gen = object.__new__(LocalQwen)
        source = "The team will open the gallery on Friday."
        outputs = iter([source, "سيفتح الفريق المعرض يوم الجمعة."])

        async def generate(*args, **kwargs):
            import json
            yield json.dumps({"translation": next(outputs)})
        gen.generate = generate
        with patch("local_qwen.writing_cache.recall", return_value={"translation": source}), \
             patch("local_qwen.writing_cache.forget") as forget, patch("local_qwen.writing_cache.remember") as remember:
            result = await gen.structured("Translate", source, obj(translation=STRING),
                validate=lambda item: validate_translation([source], [item["translation"]], "ar"))
        forget.assert_called_once()
        remember.assert_called_once()
        self.assertIn("الفريق", result["translation"])

    async def test_timeline_keeps_late_decision_with_late_audio(self):
        rows = [{"id": 1, "start": 1, "text": "We have not decided the opening date."},
                {"id": 2, "start": 10776, "text": "We chose Friday at noon."}]
        self.insights.model.structured.return_value = {
            "section1": {"label": "Planning", "detail": "The date was uncertain."},
            "section2": {"label": "Final decision", "detail": "Friday at noon."}}
        chapters = await self.insights.timeline(rows, "evidence", "English", "Opening", "Summary")
        self.assertEqual(chapters[1]["start"], 10776)
        self.assertIn("Friday", chapters[1]["detail"])

    async def test_short_note_preserves_the_exact_decision_and_deduplicates_support(self):
        rows = [{"id": 1, "text": "Speaker A: Wednesday was proposed."},
                {"id": 2, "text": "Speaker B: We will open at 12:30 on Friday."}]
        self.insights.model.structured.return_value = {"mainPassage": 2, "heading": "Planning", "supportingPassages": [1, 1, 2]}
        note = await self.insights.short_summary(rows, "source", "English", {}, "en")
        self.assertEqual(note["lead"], "We will open at 12:30 on Friday.")
        self.assertEqual(note["sections"][0]["points"], ["Wednesday was proposed."])

    def test_native_language_and_source_quantities(self):
        with self.assertRaises(ValueError):
            validate_language(["Museum opening on Friday at noon."], "ar")
        validate_language(["يفتتح المعرض يوم الجمعة."], "ar")
        rows = [{"text": "Speaker A: 20 printed maps are reserved for volunteers."},
                {"text": "Speaker B: Audio guides are available in two languages."}]
        self.assertEqual(ground_quantities("Two large print maps are reserved for volunteers.", rows, "en"),
                         "20 printed maps are reserved for volunteers.")
        self.assertEqual(ground_quantities("Two accessible rooms were discussed.", rows, "en"),
                         "Two accessible rooms were discussed.")

    def test_single_fact_has_no_duplicate_body(self):
        result = markdown_summary({"lead": "A single thought.", "sections": []})
        self.assertEqual(result, {"summary": "A single thought.", "markdown": ""})
        self.assertEqual(markdown_summary({"lead": "Maps [P3].", "sections": []})["summary"], "Maps.")

    async def test_chat_reuses_prepared_note_and_original_passages_after_reopen(self):
        import json
        import chat_context
        import writing_cache
        from session_notes import publish
        version = {"title": "Cobalt Library", "language": "en", "summary": "Friday.", "markdown": "## Details\n- Noon.",
                   "topics": [{"id": 1, "start": 0, "label": "Opening", "detail": "The date was confirmed."}]}
        with self.store.connection() as db:
            publish(db, self.key, "en", version)
            state = chat_context.snapshot(db, self.key)
        prompts = []
        async def generate(messages, **kwargs):
            prompts.append(messages)
            yield "Based on [P1]."
        self.insights.model.generate = generate
        async def ask():
            with writing_cache.scope(self.insights.store, self.key, "chat"):
                return "".join([s async for s in self.insights.chat_tokens(self.key, "What was approved?", [], state)])
        first = await ask()
        self.insights.store = SessionStore(self.store.root)
        with patch("chat_context.passages", side_effect=AssertionError("cached passages were re-extracted")):
            self.assertEqual(await ask(), first)
        self.assertEqual(prompts[0], prompts[1])
        data = json.loads(prompts[0][1]["content"].partition("\n")[2])
        self.assertEqual(data["displayedNote"]["topics"], version["topics"])
        self.assertEqual(data["displayedNote"]["markdown"], version["markdown"])
        self.assertIn("The launch is Friday.", data["originalTranscriptPassages"])
        self.assertIn("authoritative", prompts[0][0]["content"])

    async def test_long_chat_does_not_wait_for_a_missing_semantic_index(self):
        rows = [{"id": n+1, "start": float(n), "text": "The cobalt audit was approved." if n == 47 else "General discussion."}
                for n in range(90)]
        prompts = []
        async def generate(messages, **kwargs):
            prompts.append(messages)
            yield "The cobalt audit was approved [P48]."
        self.insights.model.generate = generate
        with patch("chat_context.prepared_source", return_value=(rows, [rows[:64], rows[64:]])), \
             patch.object(self.insights, "outline", side_effect=AssertionError("chat must not build the index")):
            result = "".join([s async for s in self.insights.chat_tokens(self.key, "Was the cobalt audit approved?", [])])
        self.assertIn("⟦47.0⟧", result)
        self.assertIn("The cobalt audit was approved.", prompts[0][1]["content"])
        self.insights.model.structured.assert_not_awaited()

    async def test_richer_chapters_preserve_labels_and_use_only_their_source_range(self):
        rows = [{"id": i, "start": i*10, "text": "The archive investigated the leak and arranged repairs."}
                for i in range(1, 150)]
        rows[-2]["text"] = "The curator signed the repair order."
        rows[-1]["text"] = "The separate gallery closes on Sunday."
        original = [{"id": 1, "start": 10, "label": "Archive repairs", "detail": "The team discussed repairs."},
                    {"id": 149, "start": 1490, "label": "Gallery hours", "detail": "The gallery closes on Sunday."}]
        self.insights.model.tokens.side_effect = lambda text: len(text)//3
        self.insights.model.structured.return_value = {"chapter1": "The archive investigated a leak. The curator signed the repair order."}
        result = await self.insights.chapter_details(original, rows, "en")
        data = self.insights.model.structured.call_args.args[1]
        self.assertIn("[P1]", data["chapter1"]["passages"])
        self.assertIn("curator signed", data["chapter1"]["passages"])
        self.assertNotIn("Sunday", data["chapter1"]["passages"])
        self.assertNotIn("chapter149", data)
        self.assertLessEqual(len(data["chapter1"]["passages"])//3, 950)
        self.assertEqual([(c["id"], c["start"], c["label"]) for c in result],
                         [(1, 10, "Archive repairs"), (149, 1490, "Gallery hours")])
        self.assertEqual(result[1]["detail"], "The gallery closes on Sunday.")

    def test_free_markdown_removes_repeat_sentences_and_dangling_headings(self):
        value = {"lead": "افتتح المعرض يوم الجمعة.",
                 "markdown": "## الافتتاح\n\n- افتتح المعرض يوم الجمعة!\n\n## الخرائط\n\n- وصلت الخرائط. وصلت الخرائط!\n- رُوجعت التفاصيل.\n- روجعت التفاصيل.\n\n## الختام\n- وصلت الخرائط."}
        result = markdown_summary(value)
        self.assertEqual(result["summary"], value["lead"])
        self.assertEqual(result["markdown"], "## الخرائط\n\n- وصلت الخرائط.\n- رُوجعت التفاصيل.")

    def test_repeated_sentence_filter_keeps_different_quantities(self):
        value = {"lead": "Bids received.", "markdown": "- The bid was 1.5 million.\n- The bid was 15 million."}
        self.assertEqual(markdown_summary(value)["markdown"], value["markdown"])

    async def test_localization_does_not_request_filler_for_a_single_fact(self):
        original = {"language": "en", "title": "Opening", "summary": "The library opens on Friday.", "markdown": "", "topics": []}
        self.insights.model.structured.return_value = {"translatedTitle": "Apertura", "translatedLead": "La biblioteca abre el viernes."}
        translated = await self.insights.localize(original, "Spanish", self.rows)
        self.assertEqual(translated["markdown"], "")
        self.assertNotIn("translatedMarkdown", self.insights.model.structured.call_args.args[2]["properties"])
