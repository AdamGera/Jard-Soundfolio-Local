"""Launch an atomic update fixture through the same macOS relaunch path as Jard."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


for quit_only in (False, True):
    with tempfile.TemporaryDirectory(prefix="jard-relaunch-") as directory:
        folder = Path(directory).resolve()
        identity = "local.jardaudio.update-fixture." + uuid4().hex
        stage = folder / ".jard-update-fixture"
        stage.mkdir(mode=0o700)

        def make_bundle(parent, version):
            app = parent / "Fixture.app"
            exe = app / "Contents/MacOS/fixture"
            exe.parent.mkdir(parents=True)
            shutil.copyfile(sys.argv[1], exe)
            exe.chmod(0o755)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
                "CFBundleIdentifier": identity, "CFBundleVersion": version, "CFBundlePackageType": "APPL",
                "CFBundleExecutable": "fixture", "LSUIElement": True,
            }))
            subprocess.run(["codesign", "--force", "--sign", "-", "-r",
                            f'=designated => identifier "{identity}"', app],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            return exe

        try:
            old = make_bundle(folder, "1.1.0")
            make_bundle(stage, "1.2.0")
            subprocess.run([old] + (["quit"] if quit_only else []), check=True, timeout=10)
            marker = folder / "launched"
            if quit_only:
                time.sleep(.4)
                assert not marker.exists(), "Update on quit relaunched the app."
                subprocess.run([old], check=True, timeout=10)
            deadline = time.monotonic() + 15
            while not marker.is_file() and time.monotonic() < deadline:
                time.sleep(.1)
            assert marker.read_text() == "new version launched; backup cleaned"
            assert not stage.exists()
            action = "installed on quit" if quit_only else "restarted at the same path"
            print(f"The isolated updater {action} and cleaned up the old bundle on launch.")
        finally:
            subprocess.run(["defaults", "delete", identity], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
