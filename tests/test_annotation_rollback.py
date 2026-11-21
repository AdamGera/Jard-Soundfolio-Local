import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import httpx

from annotation_deliveries import AnnotationDeliveries
from annotation_rollback import undo_annotation
from annotation_routing import Registration, RoutingError


class AnnotationRollbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        home = TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.root = Path(home.name)
        self.receipts = AnnotationDeliveries(self.root)
        self.service = Registration("http://127.0.0.1", "127.0.0.1", 80, 1, "2.2.6", None, "service.json")
        self.session = {"id": "ses_project", "title": "Project"}
        self.delivery = self.receipts.begin("capture", self.service.key, self.session, False)
        self.receipts.change(self.delivery, "queued", "msg_jard")
        self.rows = [{"type": "idle", "id": "msg_reply"}, {"type": "user", "id": "msg_jard"},
                     {"type": "idle", "id": "msg_previous"}, {"type": "user", "id": "msg_prior"}]
        self.inbox = []
        self.stage = None
        self.writes = []
        self.add_after = None
        self.fail_after = None
        self.replacement_stage = None
        self.input_at_commit = False
        self.external_prompt_after_stage = False

    def respond(self, req):
        path = req.url.path.removeprefix("/api/session/ses_project")
        if req.method == "GET":
            if path == "/message":
                return httpx.Response(200, json={"data": self.rows, "cursor": {"next": None}})
            if path == "/inbox":
                if self.input_at_commit and self.receipts.get(self.delivery)["status"] == "rollback-commit-unconfirmed":
                    self.inbox = [{"id": "msg_concurrent"}]
                return httpx.Response(200, json={"data": self.inbox})
            if not path:
                return httpx.Response(200, json={"data": {**self.session, "revert": self.stage}})
        body = json.loads(req.content) if req.content else None
        self.writes.append((req.method, path, body))
        self.assertIn(self.receipts.get(self.delivery)["status"], (
            "withdrawal-unconfirmed", "rollback-unconfirmed", "rollback-staged", "rollback-commit-unconfirmed"))
        if path == self.add_after:
            self.inbox.append({"id": "msg_new", "type": "user"})
        if path == "/inbox/msg_jard":
            self.inbox = [row for row in self.inbox if row["id"] != "msg_jard"]
            return httpx.Response(204)
        if path == "/interrupt":
            self.assertEqual(req.url.params["resume"], "false")
            return httpx.Response(200, json={"interrupted": True})
        if path == "/revert/stage":
            acknowledged = {"messageID": body["messageID"], "files": ["note.md"] if body["files"] else []}
            self.stage = self.replacement_stage or acknowledged
            if self.external_prompt_after_stage:
                self.stage = None
                self.rows = self.rows[2:]
                self.inbox = [{"id": "msg_new", "type": "user"}]
            if self.fail_after == path:
                raise httpx.ReadError("Dropped stage acknowledgment", request=req)
            return httpx.Response(200, json={"data": acknowledged})
        if path == "/revert/commit":
            message = self.stage["messageID"]
            self.stage = None
            index = next(index for index, row in enumerate(self.rows) if row["id"] == message)
            self.rows = self.rows[index + 1:]
            if self.fail_after == path:
                raise httpx.ReadError("Dropped commit acknowledgment", request=req)
            return httpx.Response(204)
        if req.method == "DELETE" and path == "/revert":
            self.stage = None
            return httpx.Response(204)
        raise AssertionError((req.method, path))

    async def undo(self, **options):
        with (patch("annotation_rollback.make_client",
                    side_effect=lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.respond))),
              patch("annotation_rollback.discover", return_value=self.service),
              patch("annotation_rollback.owns_listener", return_value=True),
              patch("annotation_routing.make_client",
                    side_effect=lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.respond))),
              patch("annotation_routing.discover", return_value=self.service),
              patch("annotation_routing.owns_listener", return_value=True)):
            return await undo_annotation(self.root, self.delivery, **options)

    def paths(self):
        return [path for _, path, _ in self.writes]

    async def test_withdraws_the_pending_item_before_considering_a_turn(self):
        self.rows = self.rows[2:]
        self.inbox = [{"id": "msg_jard"}, {"id": "msg_other"}]
        self.assertEqual((await self.undo())["status"], "withdrawn")
        self.assertEqual(self.paths(), ["/inbox/msg_jard"])
        self.assertEqual(self.inbox, [{"id": "msg_other"}])

    async def test_rolls_back_only_the_original_turn_and_stores_result(self):
        receipt = await self.undo()
        self.assertEqual(receipt["status"], "reverted")
        self.assertEqual(self.writes, [
            ("POST", "/interrupt", None),
            ("POST", "/revert/stage", {"messageID": "msg_jard", "files": False}),
            ("POST", "/revert/commit", None),
        ])
        self.assertEqual([row["id"] for row in self.rows], ["msg_previous", "msg_prior"])
        self.assertEqual(await self.undo(), receipt)
        self.assertEqual(len(self.writes), 3)

    async def test_waits_for_confirmation_before_restoring_files(self):
        self.rows.insert(1, {"type": "assistant", "id": "msg_assistant", "content": [
            {"type": "tool", "name": "apply_patch", "state": {"status": "completed"}}]})
        self.assertEqual((await self.undo())["status"], "rollback-files")
        self.assertEqual(self.writes, [])
        self.assertEqual((await self.undo(restore_files=True))["status"], "reverted")
        self.assertEqual(self.writes[1][2], {"messageID": "msg_jard", "files": True})

    async def test_keeps_followup_or_joined_turn_and_an_external_revert(self):
        self.rows.insert(1, {"type": "synthetic", "id": "msg_new"})
        self.assertEqual((await self.undo())["status"], "received")
        self.rows.pop(1)
        boundary = self.rows.pop(2)
        self.assertEqual((await self.undo())["status"], "received")
        self.rows.insert(2, boundary)
        self.stage = {"messageID": "msg_prior", "files": []}
        with self.assertRaisesRegex(RoutingError, "staged revert"):
            await self.undo()
        self.assertEqual(self.stage["messageID"], "msg_prior")
        self.assertEqual(self.writes, [])

    async def test_failed_edit_never_requests_file_restoration(self):
        self.rows.insert(1, {"type": "assistant", "id": "msg_assistant", "content": [
            {"type": "tool", "name": "write", "state": {"status": "error"}}]})
        self.assertEqual((await self.undo(restore_files=True))["status"], "reverted")
        self.assertFalse(self.writes[1][2]["files"])

    async def test_later_input_after_interrupt_prevents_staging(self):
        self.add_after = "/interrupt"
        self.assertEqual((await self.undo())["status"], "received")
        self.assertEqual(self.paths(), ["/interrupt"])
        self.assertEqual(self.inbox, [{"id": "msg_new", "type": "user"}])

    async def test_later_input_after_stage_clears_only_the_acknowledged_stage(self):
        self.add_after = "/revert/stage"
        self.assertEqual((await self.undo())["status"], "received")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage", "/revert"])
        self.assertIsNone(self.stage)
        self.assertEqual(self.inbox, [{"id": "msg_new", "type": "user"}])

    async def test_replaced_stage_is_preserved(self):
        self.replacement_stage = {"messageID": "msg_prior", "files": []}
        self.assertEqual((await self.undo())["status"], "rollback-staged")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage"])
        self.assertEqual(self.stage, self.replacement_stage)

    async def test_checks_for_input_after_storing_the_commit_claim(self):
        self.input_at_commit = True
        self.assertEqual((await self.undo())["status"], "received")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage", "/revert"])
        self.assertEqual(self.inbox, [{"id": "msg_concurrent"}])

    async def test_new_prompt_can_commit_the_acknowledged_stage_before_jard(self):
        self.external_prompt_after_stage = True
        self.assertEqual((await self.undo())["status"], "rollback-staged")
        self.inbox = []
        self.rows.insert(0, {"type": "user", "id": "msg_new"})
        self.assertEqual((await self.undo())["status"], "reverted")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage"])
        self.assertEqual([row["id"] for row in self.rows], ["msg_new", "msg_previous", "msg_prior"])

    async def test_lost_stage_acknowledgment_requires_review_without_more_writes(self):
        self.fail_after = "/revert/stage"
        self.assertEqual((await self.undo())["status"], "rollback-unconfirmed")
        self.assertEqual((await self.undo())["status"], "rollback-staged")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage"])
        self.assertEqual(self.stage["messageID"], "msg_jard")

    async def test_lost_commit_acknowledgment_reconciles_without_retry(self):
        self.fail_after = "/revert/commit"
        receipt = await self.undo()
        self.assertEqual(receipt["status"], "rollback-commit-unconfirmed")
        self.assertFalse(receipt["canCorrect"])
        self.assertEqual(AnnotationDeliveries(self.root).get(self.delivery), receipt)
        self.assertEqual((await self.undo())["status"], "reverted")
        self.assertEqual(self.paths(), ["/interrupt", "/revert/stage", "/revert/commit"])
