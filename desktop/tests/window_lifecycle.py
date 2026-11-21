"""Exercise Jard's startup and activation handlers in an isolated AppKit bundle."""

import json
import os
from pathlib import Path
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


SETUP = Path(__file__).resolve().parents[1] / "setup.html"
INTERFACE_LANGUAGES = SETUP.with_name("interface-languages.json")
HELPER = """#!/usr/bin/env python3
import json, os, sys
for line in sys.stdin:
    request = json.loads(line)
    response = {"id": request["id"], "ok": True, "result": {}}
    if request["command"] == "jard.prepare":
        outcome = os.environ["JARD_WINDOW_FIXTURE_PREPARE"]
        if outcome == "ready":
            print(json.dumps({"navigate": "app"}), flush=True)
            response["result"] = {"ready": True}
        elif outcome == "missing":
            response["result"] = {"ready": False}
        else:
            response.update(ok=False, error={"message": "Fixture engine failed to start."})
    print(json.dumps({"response": response}), flush=True)
"""


def wait_for(probe, message):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if result := probe():
            return result
        time.sleep(.03)
    raise AssertionError(message)


def read(path):
    return json.loads(path.read_text()) if path.exists() else None


def run_case(executable, scenario, prepare):
    with tempfile.TemporaryDirectory(prefix="jard-window-") as folder:
        root = Path(folder)
        identifier = "local.jardaudio.window-fixture." + uuid4().hex
        contents = root / "Jard Window Fixture.app/Contents"
        resources = contents / "Resources"
        resources.mkdir(parents=True)
        shutil.copyfile(INTERFACE_LANGUAGES, resources / INTERFACE_LANGUAGES.name)
        target = contents / "MacOS/jard-window-fixture"
        target.parent.mkdir()
        shutil.copy2(executable, target)
        info = {
            "CFBundleIdentifier": identifier, "CFBundleExecutable": target.name,
            "CFBundleDisplayName": "Jard Window Fixture", "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1", "NSMicrophoneUsageDescription": "Fixture microphone status only.",
        }
        if scenario in {"dock-updates", "managed-updates"}:
            info["JardUpdates"] = {"feed_url": "https://127.0.0.1:9/update.json", "check_on_start": scenario == "managed-updates",
                                   "public_key": "dxMjVxu9nD8oMW/Cx+DNTJ2sYv8JS8z95lJkz63hBMM="}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        helper = root / "prepare-helper"
        helper.write_text(HELPER)
        helper.chmod(0o700)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {
            **os.environ, "JARD_WINDOW_FIXTURE_ROOT": str(root), "JARD_WINDOW_FIXTURE_SCENARIO": scenario,
            "JARD_WINDOW_FIXTURE_PREPARE": prepare, "JARD_TEST_HELPER": str(helper), "JARD_TEST_SETUP": str(SETUP),
            "RUMBLE_DATA_DIR": str(root / "library"), "RUMBLE_PORT": str(port),
        }
        subprocess.run(["codesign", "--force", "--sign", "-", str(contents.parent)], check=True, capture_output=True)
        subprocess.run(["defaults", "write", identifier, "JardSystemDictation", "-dict",
                        "enabled", "-bool", "NO" if scenario in {"dock-setting", "dock-unavailable"} else "YES",
                        "onboarding", "welcome" if scenario == "login-setup" else "done"],
                       check=True)
        if scenario in {"dock-login", "dock-unavailable", "dock-updates"}:
            subprocess.run(["defaults", "write", identifier, "JardShowDockIcon", "-bool", "NO"], check=True)
        if scenario == "managed-updates":
            subprocess.run(["defaults", "write", identifier, "DisableUpdates", "-bool", "YES"], check=True)
        if scenario == "locale-preselected":
            subprocess.run(["defaults", "write", identifier, "JardInterfaceLanguage", "-string", "fr"], check=True)
        try:
            with (root / "host.log").open("wb") as log, subprocess.Popen([target], env=env, stderr=log) as process:
                def invoke(command):
                    reply = root / "reply.json"
                    reply.unlink(missing_ok=True)
                    staging = root / "next-command"
                    staging.write_text(command)
                    staging.replace(root / "command")
                    return wait_for(lambda: read(reply), f"{scenario}: AppKit did not acknowledge {command}.")

                try:
                    initial = wait_for(lambda: read(root / "launched.json"), f"{scenario}: AppKit did not launch.")
                    assert initial == {"visibleBeforeLaunch": False,
                                       "visible": scenario in {"normal", "annotation-managed", "managed-updates", "login-setup", "dock-setting",
                                                               "dock-unavailable", "locale-preselected"}}, initial
                    if prepare == "ready":
                        wait_for(lambda: invoke("status")["ready"], "The hidden workspace did not finish preparing.")
                    elif prepare == "missing":
                        title = "Configurer sur ce Mac" if scenario == "locale-preselected" else "Set up on this Mac"
                        wait_for(lambda: invoke("setup-title")["title"] == title, "Model setup did not open.")
                        assert invoke("status")["visible"]
                    elif prepare == "error":
                        wait_for(lambda: invoke("setup-title")["title"] == "Couldn’t open the workspace", "Engine error did not open.")
                        assert invoke("status")["visible"]

                    if scenario == "normal":
                        paste_menu = invoke("paste-menu")
                        assert paste_menu == {"title": "Paste Last Dictation", "shortcut": "v",
                            "shown": True, "enabled": False, "dockVisible": True}, paste_menu
                        policy = invoke("annotation-policy")
                        assert policy["shortcutRegistered"] and not policy["state"]["managed"], policy
                        live_meeting = invoke("menu-live-meeting")
                        assert live_meeting == {"idleFailure": "There is no live meeting transcript.", "pane": "live-meeting",
                                                "recording": True, "visible": True}, live_meeting
                    elif scenario == "annotation-managed":
                        policy = invoke("annotation-policy")
                        message = "Screen annotation is managed by your organization."
                        assert policy["state"]["managed"] and policy["state"]["phase"] == "idle", policy
                        assert policy["state"]["message"] == message and not policy["state"]["busy"], policy
                        assert not policy["shortcutRegistered"] and not policy["canStart"], policy
                        assert policy["startFailures"] == dict.fromkeys(("display", "window", "area", "phone"), message), policy
                    elif scenario == "managed-updates":
                        policy = invoke("update-policy")
                        message = "Updates managed by your organization"
                        assert policy["update"] == {"phase": "unavailable", "updatesDisabled": True,
                                                    "manualCheck": False, "currentVersion": "1"}, policy
                        assert not policy["updaterCreated"] and policy["visibleActions"] == 0, policy
                        assert policy["statusMenuShown"] and not policy["statusMenuEnabled"], policy
                        assert policy["statusMenuTitle"] == message, policy
                        assert policy["failures"] == dict.fromkeys(
                            ("check", "channel", "confirmSwitch", "restart", "download", "releaseNotes"), message), policy
                    elif scenario == "login":
                        assert not invoke("status")["visible"]
                        assert invoke("dock")["visible"]
                        assert not invoke("close")["visible"]
                        assert invoke("activate")["visible"]
                        assert invoke("overlay")["overlayFocused"]
                        assert not invoke("close")["visible"]
                        assert not invoke("activate")["visible"]
                        invoke("dismiss-overlay")
                        assert invoke("activate")["visible"]
                        invoke("minimize")
                        wait_for(lambda: invoke("status")["minimized"], "AppKit did not finish minimizing the window.")
                        assert invoke("activate")["minimized"]
                    elif scenario == "login-idle":
                        # When the system sends no startup activation, the later switch must still work.
                        time.sleep(1.05)
                        assert not invoke("status")["visible"]
                        assert invoke("activate")["visible"]
                    elif scenario == "login-input":
                        assert invoke("status")["visible"]
                    elif scenario == "dock-setting":
                        assert invoke("status")["dockSetting"]["enabled"]
                        changed = invoke("dock-off")
                        assert changed["dockVisible"] and changed["quitAvailable"]
                        assert changed["dockSetting"] == {"enabled": False, "menuBarAvailable": True}
                        assert invoke("preview-active")["soundPreview"]
                        invoke("minimize")
                        minimized = wait_for(lambda: (state if (state := invoke("status"))["minimized"] else None),
                                             "The Dock preference did not preserve minimization.")
                        assert minimized["dockVisible"] and not minimized["soundPreview"]
                        already_hidden = invoke("cli-hide")
                        assert already_hidden["result"] == {"windowVisible": False, "changed": False}, already_hidden
                        assert already_hidden["minimized"] and not already_hidden["visible"], already_hidden
                        invoke("cli-show")
                        wait_for(lambda: invoke("status")["visible"], "The CLI did not reopen the minimized workspace.")
                        assert invoke("preview-active")["soundPreview"]
                        hidden = invoke("cli-hide")
                        assert hidden["result"] == {"windowVisible": False, "changed": True}, hidden
                        assert not hidden["visible"] and not hidden["minimized"] and not hidden["dockVisible"], hidden
                        assert not invoke("status")["soundPreview"]
                        assert invoke("cli-hide")["result"] == {"windowVisible": False, "changed": False}
                        shown = invoke("cli-show")
                        assert shown["visible"] and shown["dockVisible"] and not shown["error"], shown
                        assert shown["result"] == {"windowVisible": True, "requestedPane": "history"}, shown
                        assert shown["navigation"]["pane"] == "history" and shown["navigation"]["id"], shown
                        assert invoke("cli-show")["navigation"]["id"] != shown["navigation"]["id"]
                        assert invoke("menu-open")["visible"]
                        assert invoke("preview-active")["soundPreview"]
                        closed = invoke("close")
                        assert not closed["visible"] and not closed["dockVisible"] and not closed["soundPreview"], closed
                        lost = invoke("menu-off")
                        assert lost["dockVisible"] and not lost["dockSetting"]["menuBarAvailable"], lost
                        assert not invoke("menu-on")["dockVisible"]
                        reopened = invoke("menu-open")
                        assert reopened["visible"] and reopened["dockVisible"]
                        reset = invoke("dock-on")
                        assert reset["dockVisible"] and reset["dockSetting"] == {"enabled": True, "menuBarAvailable": False}
                    elif scenario == "dock-login":
                        assert not invoke("status")["dockVisible"]
                        opened = invoke("menu-open")
                        assert opened["visible"] and opened["dockVisible"]
                    elif scenario == "dock-updates":
                        assert not invoke("status")["dockVisible"]
                        assert invoke("update-status")["statusMenuTitle"] == "Check for Updates…"
                        assert invoke("status-check")["visible"]

                        def finished_check(manual, previous=None):
                            state = invoke("update-status")
                            update = state["update"]
                            if update["phase"] == "error" and update["manualCheck"] == manual:
                                if previous is None or update["openUpdateSettings"] != previous:
                                    return state

                        first = wait_for(lambda: finished_check(True), "The status item check did not finish.")
                        assert first["update"]["manualCheck"] and not first["sheet"], first
                        request = first["update"]["openUpdateSettings"]
                        assert request
                        invoke("background-check")
                        automatic = wait_for(lambda: finished_check(False), "The background check did not finish.")
                        assert not automatic["update"]["manualCheck"] and not automatic["sheet"], automatic
                        assert automatic["update"]["openUpdateSettings"] == request, automatic
                        assert invoke("application-check")["visible"]
                        application = wait_for(lambda: finished_check(True, request), "The application menu check did not finish.")
                        assert application["update"]["manualCheck"] and not application["sheet"], application
                        assert application["update"]["openUpdateSettings"] != request, application
                        held = invoke("update-hold")
                        assert held["update"]["phase"] == "checking" and held["update"]["staged"], held
                        assert held["update"]["restartBlocked"] and held["publishedHold"] and not held["statusMenuEnabled"], held
                        cleared = invoke("update-unhold")
                        assert cleared["update"]["staged"] and not cleared["update"]["restartBlocked"], cleared
                        assert not cleared["publishedHold"] and cleared["statusMenuEnabled"], cleared
                    elif scenario == "dock-unavailable":
                        closed = invoke("close")
                        assert not closed["visible"] and closed["dockVisible"] and not closed["dockSetting"]["menuBarAvailable"], closed
                        assert invoke("dock")["visible"]
                    elif scenario == "locale-preselected":
                        assert invoke("setup-title")["lang"] == "fr"
                        assert invoke("locale-test") == {"frMenu": True, "jaMenu": True,
                            "resolved": ["en", "pl", "zh-Hant", "zh", "zh-Hant", "pt"],
                            "invalidRejected": True, "accepted": True, "selected": "ja"}
                except (AssertionError, TypeError):
                    print((root / "host.log").read_text(), file=sys.stderr)
                    raise
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
        finally:
            subprocess.run(["defaults", "delete", identifier], capture_output=True)


if __name__ == "__main__":
    for launch, outcome in [("normal", "ready"), ("annotation-managed", "ready"), ("managed-updates", "ready"), ("login-setup", "ready"), ("login", "ready"),
                            ("login-idle", "ready"), ("login-input", "ready"),
                            ("login-model", "missing"), ("login-error", "error"), ("dock-setting", "ready"),
                            ("dock-login", "ready"), ("dock-updates", "ready"),
                            ("dock-unavailable", "ready"), ("locale-preselected", "missing")]:
        run_case(Path(sys.argv[1]).resolve(), launch, outcome)
    print("AppKit verified quiet login, setup recovery, Dock and menu bar reopen, overlay focus, minimization, and the missing-item fallback.")
    print("AppKit verified persisted interface language, System tag resolution, inline setup, and live native menu labels.")
    print("AppKit checked updates from both menus, opened a hidden workspace, and separated the next background result.")
    print("AppKit verified a native capture hold on a staged update during a background check.")
    print("AppKit verified managed update actions and the managed Annotate capture and shortcut restrictions.")
