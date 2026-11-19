import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from insights import excerpts, Insights, ObsoleteGeneration, note_identity, anchored_topics, markdown_summary, obj, STRING, validate_translation, validate_language, ground_quantities
from local_qwen import LocalQwen, source_messages
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

    async def test_writer_receives_complete_transcript_and_guide_then_reuses_both_on_retry(self):
        rows = [{"id": i, "start": i, "text": f"Passage {i} with a different detail."} for i in range(1, 801)]
        transcript = excerpts(rows)
        guide = {"anchor1": 1, "anchor2": 200, "anchor3": 350, "anchor4": 500, "anchor5": 650, "anchor6": 800, "writerPrompt": "Lead with the revised launch date, then explain the distinct risks."}
        summary = {"anchor1": "Initial topic.", "anchor2": "", "anchor3": "", "anchor4": "", "anchor5": "", "anchor6": "One risk is unresolved."}
        with self.store.connection() as db:
            activity = [{"speaker": 1, "seconds": 3, "share": .75}, {"speaker": 2, "seconds": 1, "share": .25}]
            db.execute("UPDATE sessions SET transcript=json_set(transcript,'$.speakerActivity',json(?)) WHERE id=?",
                       (json.dumps(activity), self.key))
        model = self.insights.model
        model.structured.side_effect = [guide, summary, RuntimeError("title failed")]
        with self.assertRaisesRegex(RuntimeError, "title failed"):
            await self.insights.write(self.key, rows, "English", self.expected)
        planner, writer, title_call = model.structured.call_args_list
        self.assertEqual(planner.args[1], transcript)
        self.assertEqual(writer.args[1], transcript)
        self.assertEqual(planner.kwargs["context"]["speakerAnalytics"], {
            "speakerCount": 2, "speakers": [{"label": "Speaker A", "seconds": 3, "sharePercent": 75},
                                           {"label": "Speaker B", "seconds": 1, "sharePercent": 25}]})
        self.assertIn("[P800]", writer.kwargs["context"]["topicEvidence"]["anchor6"])
        self.assertEqual(len(planner.kwargs["context"]["passageOptions"]), 6)
        self.assertIn(guide["writerPrompt"], writer.args[0])
        self.assertEqual(planner.kwargs["policy"], writer.kwargs["policy"])
        self.assertTrue(planner.kwargs["full_context"] and writer.kwargs["full_context"])
        self.assertIn("transcript is the authority", writer.kwargs["policy"])
        self.assertEqual(title_call.args[1]["summary"], summary["anchor1"])
        model.structured.reset_mock()
        model.structured.side_effect = [{"title": "Friday launch"}]
        self.insights.timeline = AsyncMock(return_value=[{"id": 1, "start": 0, "label": "Launch", "detail": "Friday."}])
        result = await self.insights.write(self.key, rows, "English", self.expected)
        self.assertEqual(model.structured.call_count, 1)
        self.assertEqual(result["markdown"], "- One risk is unresolved.")

    async def test_short_suggestion_keeps_qualifier_in_writer_context_and_uses_only_a_lead(self):
        rows = [{"id": 1, "start": 0, "text": "Speaker A: Maybe Wednesday, if the venue is free."},
                {"id": 2, "start": 2, "text": "Speaker B: Let's check first."}]
        self.insights.model.structured.side_effect = [
            {"anchor1": 1, "writerPrompt": "Explain the options."},
            {"lead": "Wednesday was suggested, pending venue availability."}]
        result = await self.insights.write_overview(self.key, rows, "English", self.expected)
        planner = self.insights.model.structured.call_args_list[0]
        self.assertIsNone(planner.kwargs["context"]["speakerAnalytics"])
        writer = self.insights.model.structured.call_args_list[1]
        self.assertIn(rows[0]["text"], writer.kwargs["context"]["topicEvidence"]["anchor1"])
        self.assertIn(rows[1]["text"], writer.args[1])
        self.assertEqual(list(writer.args[2]["properties"]), ["lead"])
        self.assertEqual(result["markdown"], "")
        self.assertEqual(self.insights.model.structured.await_count, 2)

    async def test_echoed_question_keeps_actual_acceptance_in_short_exchange(self):
        rows = [{"id": 1, "start": 0, "text": "Speaker A: Should we book Wednesday?"},
                {"id": 2, "start": 2, "text": "Speaker B: Yes, let's book it."}]
        self.insights.model.structured.side_effect = [
            {"anchor1": 1, "writerPrompt": "Explain the outcome."}, {"lead": "Should we book Wednesday?"}]
        result = await self.insights.write_overview(self.key, rows, "English", self.expected)
        self.assertEqual(result["summary"], "Should we book Wednesday? Yes, let's book it.")

    async def test_localization_keeps_exact_anchor_and_order(self):
        original = {"title": "Friday", "summary": "The launch is Friday.", "markdown": "## Date\n\n- Friday.",
                    "topics": [{"id": 1, "start": 2.125, "label": "Launch", "detail": "Friday."}]}
        self.insights.model.structured.side_effect = [
            {"translatedTitle": "جمعه"}, {"chapter1": {"label": "روز", "detail": "جمعه"}}]
        translated = await self.insights.localize_metadata(original, "Persian")
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

    def test_full_context_preserves_real_passage_boundaries(self):
        source = excerpts([{"id": 1, "start": 0, "text": "Wednesday?"},
                           {"id": 2, "start": 1, "text": "Only if the venue confirms."}])
        self.assertEqual(source_messages(source, plain_transcript=True)[1]["content"].splitlines()[1:],
                         source.splitlines())

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

    async def test_timeline_uses_full_transcript_when_it_fits_even_with_many_passages(self):
        rows = [{"id": i, "start": i, "text": f"Detail {i}."} for i in range(1, 90)]
        source = await self.insights.timeline_source(self.key, rows, self.expected)
        for row in rows:
            self.assertIn(row["text"], source)
        self.insights.model.structured.assert_not_called()

    async def test_timeline_prefers_original_window_evidence_over_paraphrases(self):
        rows = [{"id": i, "start": i, "text": f"Original passage {i}."} for i in range(1, 401)]
        notes = [{"first": i, "last": i + 99, "topics": [{"id": i + 40}], "summary": "An inference"}
                 for i in range(1, 401, 100)]
        self.insights.outline = AsyncMock(return_value=([], notes))
        self.insights.model.tokens.side_effect = len
        source = await self.insights.timeline_source(self.key, rows, self.expected)
        self.assertNotIn("coverageNotes", source)
        for note in notes:
            for anchor in (note["first"], note["last"], note["topics"][0]["id"]):
                self.assertIn(f"Original passage {anchor}.", source["transcriptExcerpts"])

    async def test_long_timeline_combines_window_coverage_with_original_passages(self):
        from insights import TIMELINE_SOURCE_TOKENS
        rows = [{"id": i, "start": i, "text": f"Passage {i}. " + "source " * 60} for i in range(1, 91)]
        notes = [{"first": i, "last": i + 9, "topics": [{"id": i + 4}], "summary": f"Window {i}"}
                 for i in range(1, 91, 10)]
        self.insights.outline = AsyncMock(return_value=([], notes))
        self.insights.model.tokens.side_effect = len
        source = await self.insights.timeline_source(self.key, rows, self.expected)
        self.assertEqual(source["coverageNotes"], notes)
        for row in (rows[0], rows[-1]):
            self.assertIn(row["text"], source["transcriptExcerpts"])
        self.assertLessEqual(len(source_messages(source)[1]["content"]), TIMELINE_SOURCE_TOKENS)

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

    def test_generated_asr_stutter_is_collapsed_without_changing_numbers(self):
        value = {"lead": "ناقشوا نقل نقل نقل التكنولوجيا.", "markdown": "- الرقم 22 مختلف عن 2."}
        self.assertEqual(markdown_summary(value), {"summary": "ناقشوا نقل التكنولوجيا.", "markdown": value["markdown"]})

    def test_repeated_sentence_filter_keeps_different_quantities(self):
        value = {"lead": "Bids received.", "markdown": "- The bid was 1.5 million.\n- The bid was 15 million."}
        self.assertEqual(markdown_summary(value)["markdown"], value["markdown"])

    async def test_selected_language_writes_from_full_transcript_and_persists_separately(self):
        from session_notes import publish, selection

        original = {"language": "en", "title": "Opening", "summary": "An old one-line overview.",
                    "markdown": "", "topics": []}
        generated = {"summary": "Se habló de la fecha de apertura.",
                     "markdown": "- La conversación mencionó el viernes."}
        with self.store.connection() as db:
            publish(db, self.key, "en", original)
            selection(db, self.key, "es", regenerate=True)
            expected = (*note_identity(db, self.key), "es", 1)
        self.insights.write_overview = AsyncMock(return_value=generated)
        self.insights.model.structured.return_value = {"translatedTitle": "Apertura"}
        await self.insights.overview(self.key, "es", "en", expected)
        self.insights.write_overview.assert_awaited_once_with(
            self.key, self.rows, "Spanish", expected, checkpoint_stages=False)
        self.assertEqual(self.insights.model.structured.await_count, 1)
        self.assertEqual(self.insights.model.structured.call_args.args[1], original["title"])
        with self.store.connection() as db:
            target = selection(db, self.key)["version"]
            self.assertEqual(target, {**generated, "language": "es", "title": "Apertura", "topics": []})
            self.assertEqual(selection(db, self.key, "en")["version"], original)
