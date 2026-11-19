"""Exercise the shipping bridge's NDJSON pipe against real local models in a disposable library."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4

import httpx


ROOT = Path(__file__).resolve().parents[2]


async def smoke(root):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "RUMBLE_PORT":str(port), "RUMBLE_DATA_DIR":str(root/"library"),
           "RUMBLE_LOG_DIR":str(root/"logs"),"JARD_UI_EXECUTABLE":str(root/"jard-ui"), "PYTHONUNBUFFERED":"1"}
    base = f"http://127.0.0.1:{port}"
    audio = root/"review.wav"
    subprocess.run(["say","-o",root/"speech.aiff","The library approved twelve history books. The books will arrive on Monday."],check=True)
    subprocess.run(["ffmpeg","-v","error","-i",root/"speech.aiff","-ar","48000","-ac","1","-af","apad=pad_dur=1","-fflags","+bitexact",audio],check=True)
    env["FIXTURE_AUDIO"] = str(audio)
    # Real file panels and audio hardware are interactive. These two helpers supply
    # exactly the paths/events those platform adapters deliver; the rest is production code.
    dialog=root/"jard-dialog"
    dialog.write_text("#!/usr/bin/env python3\n"+"""import json,os,sys
from pathlib import Path
audio=Path(os.environ["FIXTURE_AUDIO"])
print(json.dumps({"path":str(audio if sys.argv[1]=="open-audio" else audio.parent/("export-"+sys.argv[2]))}))
""")
    capture=root/"jard-capture"
    capture.write_text("#!/usr/bin/env python3\n"+"""import json,shutil,sys,os
from pathlib import Path
if sys.argv[1]=="devices":
    print(json.dumps({"microphone":"Fixture microphone"}),flush=True)
elif ("--mode" in sys.argv and sys.argv[sys.argv.index("--mode")+1] in ("mic","system")
      and not Path(sys.argv[sys.argv.index("--output")+1]).with_suffix(".dictation").exists()):
    source = sys.argv[sys.argv.index("--mode")+1]
    print(json.dumps({"event":"error","permission":source,"message":"macOS declined capture"}),flush=True)
    sys.exit(1)
else:
    shutil.copyfile(os.environ["FIXTURE_AUDIO"],sys.argv[sys.argv.index("--output")+1])
    print(json.dumps({"event":"started","microphone":"Fixture microphone"}),flush=True)
    print(json.dumps({"event":"levels","seconds":4.5,"mic":0.2,"system":0.3,"microphone":"Fixture microphone"}),flush=True)
    sys.stdin.readline()
    print(json.dumps({"event":"finished","reason":"Stopped"}),flush=True)
""")
    for helper in (dialog,capture):
        helper.chmod(0o700)
    subprocess.run([sys.executable,"server.py","--ensure"],cwd=ROOT,env=env,check=True)
    p=await asyncio.create_subprocess_exec(sys.executable,"desktop/mac_bridge.py",cwd=ROOT,env=env,
                                           stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    counter=0
    seen=[]
    async def event():
        async with asyncio.timeout(120):
            line=await p.stdout.readline()
        assert line, "Bridge closed unexpectedly"
        value=json.loads(line)
        seen.append(value)
        return value
    async def rpc(command, **args):
        nonlocal counter
        counter+=1
        id=str(counter)
        p.stdin.write((json.dumps(dict(id=id,command="jard."+command,payload=args))+"\n").encode())
        await p.stdin.drain()
        while True:
            row=(await event()).get("response",{})
            if row.get("id")==id:
                assert row["ok"],row
                return row["result"]
    async def wait_event(kind):
        while not any(row.get("detail",{}).get("event")==kind for row in seen):
            await event()
        return next(row["detail"] for row in seen if row.get("detail",{}).get("event")==kind)

    async with httpx.AsyncClient(base_url=base,timeout=120,trust_env=False) as web:
        try:
            assert (await rpc("prepare"))["ready"] and {"navigate":"app"} in seen
            assert (await rpc("devices"))["microphone"]=="Fixture microphone"
            async with asyncio.timeout(90):
                while (await web.get("/api/status")).json()["status"]=="loading":
                    await asyncio.sleep(.25)
            async def completed(key):
                async with asyncio.timeout(120):
                    while True:
                        data=(await web.get("/api/sessions/"+key)).raise_for_status().json()
                        if data["status"]=="complete": return data
                        assert data["status"]=="processing",data["status"]
                        await asyncio.sleep(.25)
            opts=dict(languages=["en"],diarization=True)
            take = str(uuid4())
            await rpc("record", mode="mic", live=True, languages=["en"], dictation=take, diarization=True)
            async with asyncio.timeout(45):
                while not any(row.get("detail",{}).get("event") == "preview" and
                              row["detail"].get("dictation") == take for row in seen):
                    await event()
            await rpc("dictation", action="stop", id=take)
            recorded = await wait_event("dictationReady")
            assert recorded["dictation"] == take
            dictated = await rpc("dictation", action="transcribe", id=recorded["id"], languages=["en"])
            assert "history books" in dictated["text"].lower()
            assert not (await web.get("/api/sessions")).raise_for_status().json()
            assert not list((root/"library"/"staging").glob("*.dictation"))
            cancelled = str(uuid4())
            await rpc("record", mode="mic", live=True, languages=["en"], dictation=cancelled)
            await rpc("dictation", action="cancel", id=cancelled)
            assert not list((root/"library"/"staging").glob("*.dictation"))
            document = {"type":"doc","content":[{"type":"heading","attrs":{"level":2},"content":[
                {"type":"text","text": "Ask about paperback pricing."}]}]}
            draft = (await web.post("/api/personal-note-drafts", json={"document":document})).raise_for_status().json()
            print("Native microphone-only dictation, live preview, final transcription and temporary file cleanup passed.",flush=True)
            first=(await rpc("openAudio", notesDraft=draft["id"], **opts))["id"]
            saved=await completed(first)
            assert saved["personalNotes"] == {"document":document,"revision":1}
            assert saved["transcript"]["words"] and saved["transcript"]["segments"]
            for kind in ("audio","json"):
                assert (await rpc("export", id=first,kind=kind))["saved"]
            assert (root/"export-review.wav").read_bytes()==audio.read_bytes()
            exported=json.loads((root/"export-review.json").read_text())
            assert all(exported[k]==v for k,v in saved["transcript"].items())
            assert exported["modelRevision"]==saved["modelRevision"] and exported["source"]==audio.name
            print("Authenticated bridge, staged import, real Metal models, timed speaker transcript and both native exports passed.",flush=True)

            for source in ("mic", "system"):
                seen.clear()
                await rpc("record", mode=source, live=False, **opts)
                denied = await wait_event("error")
                assert denied["permission"] == source and not (await rpc("devices"))["drafts"]
            print("Typed microphone and system audio denials reach the UI and remove empty staging files.", flush=True)
            await rpc("record",mode="both",live=True,**opts)
            await wait_event("started")
            await wait_event("levels")
            await rpc("live",enabled=False)
            await rpc("stop")
            detail=await wait_event("accepted")
            call=await completed(detail["id"])
            assert call["recording"] and call["transcript"]["text"]
            p.stdin.close()
            assert await p.wait()==0
            assert (await web.get("/api/status")).json()["status"]=="ready"
            print("Mixed-capture adapter events, stop/finalization, persisted recording and independent bridge shutdown passed.",flush=True)
        finally:
            if p.returncode is None:
                p.stdin.close()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(p.wait(),12)
                if p.returncode is None: p.kill(); await p.wait()
            await web.post("/api/quit",headers={"Origin":base,"X-Jard-Action":"quit"})
            for _ in range(50):
                try: await web.get("/api/status",timeout=.3)
                except httpx.RequestError: break
                await asyncio.sleep(.2)


if __name__=="__main__":
    with tempfile.TemporaryDirectory(prefix="jard-bridge-test-") as work:
        asyncio.run(smoke(Path(work)))
