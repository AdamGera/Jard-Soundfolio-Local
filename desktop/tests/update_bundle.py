"""Check the update operations on disposable bundles without changing the installed app."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile


def bundle(folder, identifier, version):
    app = folder / "Fixture.app"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    binary = macos / "fixture"
    shutil.copyfile("/usr/bin/true", binary)
    binary.chmod(0o755)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": identifier, "CFBundleVersion": version,
        "CFBundleExecutable": "fixture", "CFBundlePackageType": "APPL",
    }))
    requirements = f'=designated => identifier "{identifier}"'
    subprocess.run(["codesign", "--force", "--sign", "-", "-r", requirements, str(app)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    return app


with tempfile.TemporaryDirectory(prefix="update-bundle-") as work:
    root = Path(work)
    identity = "local.jardaudio.update-fixture"
    current = bundle(root / "current", identity, "1.1.0")
    candidate = bundle(root / "candidate", identity, "1.2.0")
    foreign = bundle(root / "foreign", "local.other.fixture", "1.2.0")
    tampered = bundle(root / "tampered", identity, "1.2.0")
    (tampered / "Contents/Resources").mkdir()
    with (tampered / "Contents/MacOS/fixture").open("ab") as binary:
        binary.write(b"tampered")
    subprocess.run([sys.argv[1], current, candidate, foreign, tampered], check=True)
