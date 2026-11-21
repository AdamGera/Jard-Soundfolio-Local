"""Read transformation restrictions imposed by macOS configuration profiles."""
import asyncio
from enum import StrEnum

from managed_preferences import forced_boolean as _forced


class TransformPolicy(StrEnum):
    NORMAL = "normal"
    LOOPBACK = "loopback"
    DISABLED = "disabled"


DISABLED_MESSAGE = "Your organization has turned off transformations."
PROGRAM_MESSAGE = "Your organization allows transformations to contact this Mac only. Programs are disabled."
LOOPBACK_MESSAGE = "Your organization allows network requests to this Mac only."


def current_policy():
    if _forced("DisableTransforms"):
        return TransformPolicy.DISABLED
    if _forced("RestrictTransformsToLoopback"):
        return TransformPolicy.LOOPBACK
    return TransformPolicy.NORMAL


async def wait_for_policy_change(policy):
    while current_policy() == policy:
        await asyncio.sleep(0.04)


def changed_message():
    return (DISABLED_MESSAGE if current_policy() == TransformPolicy.DISABLED
            else "Your organization's transformation restrictions changed.")
