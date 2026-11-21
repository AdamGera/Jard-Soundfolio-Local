import asyncio
from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from annotation_rollback import undo_annotation
from annotation_routing import (correct_annotation, destinations, discover, request, resolve_automatic_rule,
                                send_annotation, set_destination_rule, withdraw_annotation)
from desktop.mac_bridge import MacBridge
import jardctl
from managed_preferences import (ANNOTATION_MANAGED, MEETINGS_MANAGED, OPENCODE_MANAGED, ManagedFeatureError,
                                 annotate_managed, forced_boolean, meetings_managed, opencode_managed)


class ManagedPreferencesTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS preference store")
    def test_personal_preferences_cannot_disable_features(self):
        domain = f"local.jardaudio.test.{uuid4()}"
        self.addCleanup(subprocess.run, ["defaults", "delete", domain])
        for key in ("DisableAnnotate", "DisableOpenCodeProcessing", "DisableMeetings"):
            subprocess.run(["defaults", "write", domain, key, "-bool", "true"], check=True)
            written = subprocess.check_output(["defaults", "read", domain, key], text=True)
            self.assertEqual(written.strip(), "1")
            self.assertFalse(forced_boolean(key, domain))


class ManagedRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        opencode_managed.cache_clear()
        self.addCleanup(opencode_managed.cache_clear)
        self.forced = self.enterContext(patch("managed_preferences.forced_boolean", return_value=True))

    async def test_refuses_remote_work_before_opening_transport_or_receipts(self):
        with (patch("annotation_routing.make_client", side_effect=AssertionError("opened transport")),
              patch("annotation_routing.AnnotationDeliveries", side_effect=AssertionError("opened receipts")),
              patch("annotation_rollback.read_receipt", side_effect=AssertionError("opened undo receipt"))):
            for entry, args in [
                (request, (None, None, "GET", "/api/info")),
                (discover, (None, None, None)),
                (destinations, ()),
                (set_destination_rule, (None, None, None, None, None)),
                (resolve_automatic_rule, ({},)),
                (send_annotation, (None, None, None)),
                (withdraw_annotation, (None, None)),
                (undo_annotation, (None, None)),
                (correct_annotation, (None, None)),
            ]:
                with self.subTest(operation=entry.__name__), self.assertRaisesRegex(ManagedFeatureError, OPENCODE_MANAGED):
                    await entry(*args, **({"seconds": 1} if entry is request else {}))

        self.forced.return_value = False
        self.assertTrue(opencode_managed())
        self.forced.assert_called_once_with("DisableOpenCodeProcessing")
        opencode_managed.cache_clear()
        self.assertFalse(opencode_managed())

    async def test_local_control_reports_managed_without_cancelling_pending_delivery(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"RUMBLE_DATA_DIR": folder}):
            root = Path(folder)
            (root / ".native-token").write_text("fixture-token")
            async with httpx.AsyncClient(headers={"X-Jard-Token": "fixture-token"}) as client:
                bridge = MacBridge(root, client)
                bridge.connected = True
                self.assertEqual(await bridge.dispatch("jard.annotationRoutingPolicy", {}), {"managed": True})
                capture = str(uuid4())
                store = bridge.automatic_annotations.receipts
                destination = {"id": "ses_fixture", "title": "Fixture"}
                delivery = store.begin(capture, "fixture-service", destination, False)
                receipt = store.change(delivery, "queued", "msg_fixture")
                self.assertEqual(await bridge.dispatch("jard.annotationDeliveries", {"id": capture}),
                                 {"deliveries": [receipt]})
                await bridge.start_control()
                try:
                    # Drive the real CLI parsing and socket on another thread so the bridge can respond.
                    with patch.object(bridge.automatic_annotations, "cancel") as cancel:
                        for action, options in [
                            ("destinations", {}), ("remember", {}), ("send", {}),
                            ("undo", {}), ("correct", {}),
                        ]:
                            with self.subTest(action=action), self.assertRaisesRegex(ManagedFeatureError, OPENCODE_MANAGED):
                                await asyncio.to_thread(jardctl.desktop, root, f"annotation-{action}", **options)
                        cancel.assert_not_called()
                    errors = io.StringIO()
                    with patch.object(sys, "argv", ["jardctl", "--json", "annotate", "destinations"]), redirect_stderr(errors):
                        with self.assertRaises(SystemExit) as exit_call:
                            await asyncio.to_thread(jardctl.main)
                    self.assertEqual(exit_call.exception.code, 1)
                    self.assertEqual(json.loads(errors.getvalue()), {"code": "managed", "error": OPENCODE_MANAGED})
                finally:
                    await bridge.close_control()


class ManagedCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_meeting_record_and_recovery_refuse_before_contacting_engine(self):
        meetings_managed.cache_clear()
        self.addCleanup(meetings_managed.cache_clear)
        with (tempfile.TemporaryDirectory() as folder,
              patch.dict(os.environ, {"RUMBLE_DATA_DIR": folder}),
              patch("managed_preferences.forced_boolean", side_effect=lambda key: key == "DisableMeetings")):
            root = Path(folder)
            (root / ".native-token").write_text("fixture-token")
            async with httpx.AsyncClient(headers={"X-Jard-Token": "fixture-token"}) as client:
                bridge = MacBridge(root, client)
                bridge.connected = True
                await bridge.start_control()
                try:
                    for options in ({}, {"title": "Planning"}):
                        with self.assertRaisesRegex(ManagedFeatureError, MEETINGS_MANAGED):
                            await asyncio.to_thread(jardctl.desktop, root, "meeting-record", **options)
                    with patch.object(bridge, "api", new_callable=AsyncMock, side_effect=AssertionError("engine contacted")):
                        self.assertEqual(await bridge.dispatch("jard.meetingDrafts", {}), [])
                        with self.assertRaisesRegex(ManagedFeatureError, MEETINGS_MANAGED):
                            await bridge.dispatch("jard.record", {"meeting": str(uuid4())})
                        with self.assertRaisesRegex(ManagedFeatureError, MEETINGS_MANAGED):
                            await bridge.dispatch("jard.recoverMeeting", {"id": str(uuid4())})
                finally:
                    await bridge.close_control()

    async def test_cli_and_native_lease_decline_managed_capture_without_side_effects(self):
        annotate_managed.cache_clear()
        self.addCleanup(annotate_managed.cache_clear)
        with (tempfile.TemporaryDirectory() as folder,
              patch.dict(os.environ, {"RUMBLE_DATA_DIR": folder}),
              patch("managed_preferences.forced_boolean", side_effect=lambda key: key == "DisableAnnotate")):
            root = Path(folder)
            (root / ".native-token").write_text("fixture-token")
            async with httpx.AsyncClient(headers={"X-Jard-Token": "fixture-token"}) as client:
                bridge = MacBridge(root, client)
                bridge.connected = True
                with patch.object(bridge, "close_warm", new_callable=AsyncMock) as close_warm:
                    with self.assertRaisesRegex(ManagedFeatureError, ANNOTATION_MANAGED):
                        await bridge.dispatch("jard.annotationLease", {"id": str(uuid4()), "active": True})
                    with self.assertRaisesRegex(ManagedFeatureError, ANNOTATION_MANAGED):
                        await bridge.dispatch("jard.importAnnotation", {})
                    close_warm.assert_not_awaited()
                    self.assertIsNone(bridge.annotation)
                await bridge.start_control()
                try:
                    for source in ("display", "window", "area", "phone"):
                        with self.subTest(source=source), self.assertRaisesRegex(ManagedFeatureError, ANNOTATION_MANAGED):
                            await asyncio.to_thread(jardctl.desktop, root, "annotation-start", source=source)
                finally:
                    await bridge.close_control()
