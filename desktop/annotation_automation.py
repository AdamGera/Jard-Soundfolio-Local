"""Coordinate automatic annotation offers with the visible macOS countdown."""

import asyncio
from dataclasses import dataclass, field
import fcntl
import os
from pathlib import Path
import stat
from time import time

from annotation_deliveries import AnnotationDeliveries, ROUTE_LIFETIME
from annotation_routing import RouteUnavailable, RoutingError, resolve_automatic_rule, route_scopes
from managed_preferences import OPENCODE_MANAGED, opencode_managed

COUNTDOWN = 3.0
OFFER_TIMEOUT = 10.0
HOLD_TIMEOUT = 120.0
MAX_ROUTES = 16
RETRY_INTERVAL = 15.0


@dataclass
class Offer:
    capture: str
    origin: dict
    rule: dict
    title: str
    phase: str = "transcribing"
    message: str = ""
    remaining: float = COUNTDOWN
    deadline: float = 0
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None


class AnnotationAutomation:
    def __init__(self, root, load_capture, deliver, notify):
        self.receipts = AnnotationDeliveries(root)
        self.load_capture = load_capture
        self.deliver = deliver
        self.notify = notify
        self.routes = {}
        self.panel = asyncio.Lock()
        self.lifecycle = asyncio.Lock()
        self.lock_path = Path(root) / ".annotation-outbox.lock"
        self.runner = None

    def acquire_runner(self):
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise OSError("The automatic annotation runner is unavailable.")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        except OSError:
            os.close(descriptor)
            raise
        return descriptor

    async def resume(self):
        if opencode_managed():
            return
        async with self.lifecycle:
            if self.runner is not None:
                return
            self.runner = self.acquire_runner()
            if self.runner is None:
                return
            try:
                pending = await asyncio.to_thread(self.receipts.recover_routes)
            except BaseException:
                os.close(self.runner)
                self.runner = None
                raise
            for row in pending:
                self.start(row["capture"], row["origin"], row["rule"])


    async def enqueue(self, capture, observed_origin, started):
        if opencode_managed() or not observed_origin or not started:
            return
        scopes = route_scopes({"origin": observed_origin})
        rule = await asyncio.to_thread(self.receipts.preferred_rule, scopes)
        if not rule or not rule["automatic"] or rule["updated"] > started or capture in self.routes:
            return
        await self.resume()
        async with self.lifecycle:
            if self.runner is not None and await asyncio.to_thread(
                self.receipts.enqueue_route, capture, observed_origin, rule, MAX_ROUTES
            ):
                self.start(capture, observed_origin, rule)

    def start(self, capture, origin, rule):
        completed = [key for key, offer in self.routes.items() if offer.task.done()]
        for key in completed[:max(0, len(self.routes) - MAX_ROUTES + 1)]:
            del self.routes[key]
        offer = Offer(capture, origin, rule, rule["title"])
        self.routes[capture] = offer
        offer.task = asyncio.create_task(self.run(offer))
        self.report(offer, "transcribing", "Waiting for the narration.")

    @staticmethod
    def stored_status(row, capture):
        if not row:
            return {"capture": capture, "phase": "idle"}
        pending = row["state"] == "pending"
        return {"capture": capture, "phase": "waiting" if pending else row["state"],
                "title": row["rule"]["title"],
                "message": OPENCODE_MANAGED if pending and opencode_managed() else row["message"], "seconds": 0}

    async def query(self, capture):
        if capture in self.routes:
            return self.status(capture)
        return self.stored_status(await asyncio.to_thread(self.receipts.get_route, capture), capture)

    def status(self, capture):
        offer = self.routes.get(capture)
        if not offer:
            return {"capture": capture, "phase": "idle"}
        seconds = offer.remaining
        if offer.phase == "countdown":
            seconds = max(0, offer.deadline - asyncio.get_running_loop().time())
        return {"capture": offer.capture, "phase": offer.phase, "title": offer.title,
                "message": offer.message, "seconds": seconds}

    def report(self, offer, phase, message=""):
        offer.phase = phase
        offer.message = message
        self.notify(self.status(offer.capture))

    async def cancel(self, capture, message="Automatic send cancelled."):
        if not await asyncio.to_thread(self.receipts.set_pending_route, capture, "cancelled", message):
            return False
        offer = self.routes.get(capture)
        if offer and not offer.task.done():
            self.report(offer, "cancelled", message)
            offer.task.cancel()
        return True

    async def panel_action(self, capture, action, held=False):
        if not isinstance(capture, str):
            return
        offer = self.routes.get(capture)
        if not offer:
            return
        if action == "cancel":
            await self.cancel(capture)
            return
        now = asyncio.get_running_loop().time()
        if action == "shown" and offer.phase == "offer":
            if held:
                self.report(offer, "held")
            else:
                offer.deadline = now + offer.remaining
                self.report(offer, "countdown")
        elif action == "hold" and offer.phase == "countdown" and now < offer.deadline:
            offer.remaining = offer.deadline - now
            self.report(offer, "held")
        elif action == "resume" and offer.phase == "held":
            offer.deadline = now + max(1.0, offer.remaining)
            self.report(offer, "countdown")
        else:
            return
        offer.changed.set()

    async def current_rule(self, offer):
        rule = await asyncio.to_thread(self.receipts.preferred_rule, route_scopes({"origin": offer.origin}))
        if not rule or rule["id"] != offer.rule["id"]:
            raise RoutingError("The saved destination changed before sending.")

    async def await_narration(self, offer):
        while True:
            record = await self.load_capture(offer.capture)
            if record["status"] != "processing":
                if record["status"] != "complete":
                    raise RoutingError("The narration did not finish.")
                if record.get("annotation", {}).get("origin") != offer.origin:
                    raise RoutingError("The saved annotation has a different capture origin.")
                return
            await asyncio.sleep(.5)

    async def await_countdown(self, offer):
        # Start only after the native panel is on screen and acknowledges the offer.
        self.report(offer, "offer")
        await asyncio.wait_for(offer.changed.wait(), OFFER_TIMEOUT)
        while True:
            offer.changed.clear()
            if offer.phase == "held":
                try:
                    await asyncio.wait_for(offer.changed.wait(), HOLD_TIMEOUT)
                except TimeoutError:
                    await self.panel_action(offer.capture, "resume")
            else:
                duration = offer.deadline - asyncio.get_running_loop().time()
                if duration <= 0:
                    return
                try:
                    await asyncio.wait_for(offer.changed.wait(), duration)
                except TimeoutError:
                    if offer.phase == "countdown":
                        return

    async def attempt(self, offer):
        async with self.panel:
            await self.current_rule(offer)
            service, destination = await resolve_automatic_rule(offer.rule)
            offer.title = destination["title"]
            offer.remaining = COUNTDOWN
            offer.deadline = 0
            offer.changed.clear()
            try:
                await self.await_countdown(offer)
            except TimeoutError:
                await self.finish_pending(offer, "cancelled", "Jard couldn’t show the automatic send countdown.")
                return
            self.report(offer, "preparing", "Verifying the destination.")

            async def authorize(key, chosen, included_sheet):
                identifier = await asyncio.to_thread(self.receipts.claim_route, offer.capture, route_scopes({"origin": offer.origin}),
                                                     offer.rule["id"], key, chosen, included_sheet)
                self.report(offer, "sending", "Sending annotation…")
                return identifier

            await self.deliver(offer.capture, destination["id"], service, authorize)
            await self.settle(offer)

    async def finish_pending(self, offer, state, message):
        await asyncio.to_thread(self.receipts.set_pending_route, offer.capture, state, message)
        await self.settle(offer)

    async def settle(self, offer):
        row = await asyncio.to_thread(self.receipts.settle_route, offer.capture)
        status = self.stored_status(row, offer.capture)
        self.report(offer, status["phase"], status.get("message", ""))

    async def run(self, offer):
        try:
            await self.await_narration(offer)
            while True:
                row = await asyncio.to_thread(self.receipts.get_route, offer.capture)
                if not row or row["state"] != "pending":
                    await self.settle(offer)
                    return
                if time() >= row["created"] + ROUTE_LIFETIME:
                    await self.finish_pending(offer, "failed", "The pending automatic send expired.")
                    return
                try:
                    await self.attempt(offer)
                    return
                except RouteUnavailable as error:
                    message = f"{error} Jard will check again."
                    if not await asyncio.to_thread(self.receipts.set_pending_route, offer.capture, "pending", message):
                        await self.settle(offer)
                        return
                    self.report(offer, "waiting", message)
                    await asyncio.sleep(RETRY_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = str(error) if isinstance(error, (ValueError, OSError, RuntimeError)) else ""
            await self.finish_pending(offer, "failed", message or "Automatic send could not finish.")

    async def close(self):
        async with self.lifecycle:
            tasks = []
            for offer in self.routes.values():
                if not offer.task.done():
                    self.report(offer, "waiting", "Automatic send will resume when Jard opens.")
                    offer.task.cancel()
                    tasks.append(offer.task)
            await asyncio.gather(*tasks, return_exceptions=True)
            self.routes.clear()
            if self.runner is not None:
                os.close(self.runner)
                self.runner = None
