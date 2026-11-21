"""Feature restrictions from forced Boolean values in Jaro's macOS domain."""
import ctypes as c
from functools import cache
import sys


DOMAIN = "local.jardaudio.desktop"
UTF8 = 0x08000100
ANNOTATION_MANAGED = "Screen annotation is managed by your organization."
MEETINGS_MANAGED = "Meetings are managed by your organization."
OPENCODE_MANAGED = "OpenCode processing is managed by your organization."


class ManagedFeatureError(ValueError):
    """A configuration profile prohibits the requested operation."""


def bind(library, name, result, *parameters):
    function = getattr(library, name)
    function.restype = result
    function.argtypes = parameters
    return function


@cache
def core_foundation():
    return c.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


@cache
def _preferences():
    cf = core_foundation()
    pointer = c.c_void_p
    return (
        bind(cf, "CFStringCreateWithCString", pointer, pointer, c.c_char_p, c.c_uint32),
        bind(cf, "CFPreferencesAppValueIsForced", c.c_bool, pointer, pointer),
        bind(cf, "CFPreferencesCopyAppValue", pointer, pointer, pointer),
        bind(cf, "CFGetTypeID", c.c_ulong, pointer),
        bind(cf, "CFBooleanGetTypeID", c.c_ulong),
        bind(cf, "CFBooleanGetValue", c.c_bool, pointer),
        bind(cf, "CFRelease", None, pointer),
    )


def forced_boolean(key, domain=DOMAIN):
    if sys.platform != "darwin":
        return False
    create, forced, copy, type_id, boolean_id, boolean_value, release = _preferences()
    name = create(None, key.encode(), UTF8)
    application = create(None, domain.encode(), UTF8)
    try:
        if not name or not application or not forced(name, application):
            return False
        value = copy(name, application)
        if not value:
            return False
        try:
            return type_id(value) == boolean_id() and boolean_value(value)
        finally:
            release(value)
    finally:
        if name:
            release(name)
        if application:
            release(application)


@cache
def annotate_managed():
    return forced_boolean("DisableAnnotate")


def require_annotation():
    if annotate_managed():
        raise ManagedFeatureError(ANNOTATION_MANAGED)


@cache
def meetings_managed():
    return forced_boolean("DisableMeetings")


def require_meetings():
    if meetings_managed():
        raise ManagedFeatureError(MEETINGS_MANAGED)


@cache
def opencode_managed():
    return forced_boolean("DisableOpenCodeProcessing")


def require_opencode():
    if opencode_managed():
        raise ManagedFeatureError(OPENCODE_MANAGED)
