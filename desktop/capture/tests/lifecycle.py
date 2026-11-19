"""Run the exact recorder process control protocol with synthetic timestamped samples."""
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def exercise(executable, folder, mode):
    path = folder / (mode + ".wav")
    path.touch()
    child = subprocess.Popen([executable, "record", "--mode", "both", "--output", str(path)],
                             env={**os.environ, "JARD_TEST_INPUT_LOSS": str(int(mode == "loss"))},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        events = []
        for line in child.stdout:
            item = json.loads(line)
            events.append(item)
            if item["event"] == "levels" and item["seconds"] >= 2.25:
                if mode == "stop":
                    child.stdin.write("stop\n")
                    child.stdin.flush()
                elif mode == "eof":
                    child.stdin.close()
                elif mode == "term":
                    child.send_signal(signal.SIGTERM)
                elif mode == "kill":
                    child.kill()
                break
        events.extend(map(json.loads, child.stdout))
        result = child.wait(timeout=5)
        assert events[0]["event"] == "started", events
        assert any(e["event"] == "levels" and e["system"] == .5 and e["mic"] == .25 for e in events)
        with wave.open(str(path), "rb") as w:
            assert w.getframerate() == 48000 and w.getnchannels() == 1 and w.getsampwidth() == 2
            frames = w.getnframes()
            assert frames >= 48000 and path.stat().st_size >= 44 + frames * 2
            w.setpos(16000)
            assert abs(struct.unpack("<h", w.readframes(1))[0] - 12288) < 3
        if mode == "kill":
            assert result == -signal.SIGKILL
            assert path.stat().st_size - 44 - frames * 2 <= 2 * 48000
        else:
            assert result == 0 and events[-1]["event"] == "finished", events[-1]
            assert path.stat().st_size == frames * 2 + 44
            if mode == "loss":
                assert "timing reference" in events[-1]["reason"], events[-1]
        return f"{mode}: {frames} valid mixed samples"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=5) as pool:
        for source in ("mic", "system"):
            path = Path(tmp) / (source + ".wav")
            path.touch()
            result = subprocess.run([sys.argv[1], "record", "--mode", source, "--output", str(path)],
                                    env={**os.environ, "JARD_TEST_DENIED_SOURCE": source}, input="", capture_output=True, text=True, timeout=5)
            event = json.loads(result.stdout)
            assert result.returncode == 1 and event["event"] == "error" and event["permission"] == source, event
            assert path.stat().st_size == 0
        print("Structured microphone and system audio denials left no recorded audio.")
        results = [pool.submit(exercise, sys.argv[1], Path(tmp), mode) for mode in ["stop", "eof", "term", "kill", "loss"]]
        for task in results:
            print(task.result(timeout=20))
