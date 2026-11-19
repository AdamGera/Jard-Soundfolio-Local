import asyncio
import unittest
from unittest.mock import patch

from local_qwen import LocalQwen


TITLE = {"type": "object", "properties": {"title": {"type": "string"}}}


class StructuredProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_unfinished_strings_stream_and_only_valid_results_are_cached(self):
        snapshots = []
        model = object.__new__(LocalQwen)
        with patch("writing_cache.recall", return_value=None), patch("writing_cache.remember") as save:
            async def generate(*args, **kwargs):
                yield '{"title": "مع'
                self.assertEqual(snapshots, [{"title": "مع"}])
                save.assert_not_called()
                await asyncio.sleep(0.07)
                yield 'رض'
                self.assertEqual(snapshots[-1], {"title": "معرض"})
                save.assert_not_called()
                yield ' الميناء"}'

            model.generate = generate
            result = await model.structured("Title", {}, TITLE, on_partial=snapshots.append)
            self.assertEqual(result, {"title": "معرض الميناء"})
            self.assertEqual(result, snapshots[-1])
            self.assertEqual(save.call_args.args[1], result)
            save.assert_called_once()

    async def test_retry_clears_previous_partial_and_hits_deliver_only_the_valid_result(self):
        model = object.__new__(LocalQwen)
        attempts = iter(['{"title":"Wrong', '{"title":"Correct"}'])
        snapshots = []

        async def generate(*args, **kwargs):
            yield next(attempts)

        model.generate = generate
        with patch("writing_cache.recall", return_value=None), patch("writing_cache.remember") as save:
            result = await model.structured("Title", {}, TITLE, on_partial=snapshots.append)
            self.assertEqual(snapshots[:2], [{"title": "Wrong"}, {}])
            self.assertEqual(snapshots[-1], {"title": "Correct"})
            save.assert_called_once()

        snapshots.clear()
        with patch("writing_cache.recall", return_value=result), patch("writing_cache.remember") as save:
            self.assertEqual(await model.structured("Title", {}, TITLE, on_partial=snapshots.append), result)
            self.assertEqual(snapshots, [result])
            save.assert_not_called()

    async def test_terminal_error_clears_draft_without_caching(self):
        async def generate(*args, **kwargs):
            yield '{"title":"unfinished'

        model = object.__new__(LocalQwen)
        model.generate = generate
        snapshots = []
        with patch("writing_cache.recall", return_value=None), patch("writing_cache.remember") as save:
            with self.assertRaises(ValueError):
                await model.structured("Title", {}, TITLE, on_partial=snapshots.append)
            self.assertEqual(snapshots[-1], {})
            save.assert_not_called()
