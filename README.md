# Jard Soundfolio Local

*Your recordings, transcripts, and notes stay on your Mac.*

Record a conversation or open an audio file on an Apple Silicon Mac. Search the transcript, see who spoke, and jump from a note or chat answer to the moment you want to hear again. Setup downloads the models once. After that, transcription, speaker labeling, and writing work offline.

## Why Jard made this free

At Jard (Joint AI Research & Dynamics), we believe inference belongs close to the source when the hardware can do the work. For a conversation, that means your Mac can turn speech into a timestamped transcript once and use it again whenever you need a quote, a summary, or an answer. We made Jard Soundfolio Local free and open source so anyone can use it, inspect how it handles their recordings, or adapt it. [Jard](https://www.jard.ai/) · [Our manifesto](https://www.jard.ai/manifesto)

## Get started on your Mac

### Mac application

**[Download Jard Soundfolio Local for Apple Silicon (.dmg)](https://github.com/AdamGera/Soundfolio-Local/releases/download/v0.1.0/Jard-Soundfolio-Local-arm64.dmg)** · [Matching native source (.zip)](https://github.com/AdamGera/Soundfolio-Local/releases/download/v0.1.0/Jard-Soundfolio-Local-native-source.zip)

The v0.1.0 native download has an ad-hoc signature and has not been notarized by Apple. Gatekeeper may block it on first launch.

On an Apple Silicon Mac running macOS 26 or later, drag **Jard Soundfolio Local.app** from the disk image to **Applications**, then open it from there. On first launch, choose the existing `model/` folder from a source installation or download the models (3.86 GB). The app includes its own runtime. Its sidebar opens the same local library as the browser.

To record a call, select **Microphone + Mac audio** and press **Record**. Grant the requested Microphone and Screen & System Audio Recording permissions in macOS. The app shows the active Mac input by name and a level meter for each source. Choose the built-in or headset microphone in System Settings → Sound → Input. Mac audio comes directly from macOS, so Zoom, Meet and other apps can play through your headphones. **Stop** saves the mix and starts the final transcript. Select **Microphone only** or **Mac audio only** for a single source.

Use **File → Open in Browser** to open the same workspace alongside the Mac window. To build the app and its disk image, see the [Mac build instructions](desktop/build-tools/README.md).

#### First recording and missing permissions

Open **Recording access** beside the source selector to check the current permissions without starting a recording. Press **Record** when you are ready to request the selected sources and record. **Microphone** captures your voice. **Mac audio** captures sound from Zoom, Meet, and other apps, including when you use headphones. The recording guide shows the permissions needed for the sources you selected.

- **Microphone.** Choose **Allow** in the macOS prompt. If you previously declined, use the guide’s settings button to open **Privacy & Security → Microphone** and turn on **Jard Soundfolio Local**. Mac apps appear in this list after requesting access. If Jard is missing, expand **Jard isn’t listed in System Settings** in the guide. [Apple’s microphone settings guide](https://support.apple.com/guide/mac-help/mchla1b1e1fe/mac)
- **Mac audio.** Use the guide’s settings button to open **Privacy & Security → Screen & System Audio Recording**. Enable Jard in the section for **screen and system audio**. Jard uses ScreenCaptureKit and saves only the audio. If its row is missing, click **+** under that section and select **Jard Soundfolio Local.app**. **Show this app in Finder** locates the exact copy you opened. [Apple’s screen and system audio settings guide](https://support.apple.com/guide/mac-help/control-access-screen-system-audio-recording-mchld6aa7d23/mac)
- **After changing access.** If the switch is already on and capture was still refused, quit Jard with **⌘Q** and reopen the copy in **Applications**. If macOS offers **Quit & Reopen**, choose it. Otherwise return to the recording guide, check access, and select **Retry recording** when ready. A restriction imposed by a managed Mac may require your administrator.

### Browser application

For the source installation, use an Apple Silicon Mac running macOS 26 with Bun, uv, and FFmpeg installed. Download and extract the [Jard Soundfolio Local source ZIP](https://github.com/AdamGera/Soundfolio-Local/releases/download/v0.1.0/Jard-Soundfolio-Local.zip) or clone this repository. In Finder, double-click **Setup Jard Soundfolio Local.command**. When Terminal shows **Ready**, open **Start Jard Soundfolio Local.command**. Allow about 4 GB for the models, plus space for dependencies and your recordings.

The browser interface connects to the engine on your Mac at `http://127.0.0.1:8765`. You can also use the welcome page.

Open **[README.html](README.html)** from Finder. Its **Set up**, **Start**, and **Quit** buttons launch their matching Terminal commands through the bundled Jard Soundfolio Local Launcher. If the browser asks to open the launcher, choose **Open**. On a fresh Mac, double-click **Jard Soundfolio Local Launcher.app** once to register it and open the page. **Welcome to Jard Soundfolio Local.command** also opens it. Keep the launcher next to the HTML file.

- Double-click **Start Jard Soundfolio Local.command** to open the app in your browser. The server runs in the background. You can close the launcher’s Terminal window. Double-click Start again to reopen a running app.
- Double-click **Quit Jard Soundfolio Local.command** to stop the local server and release its models. Inside the app, the small power button in the header opens a confirmation. Confirm to quit or choose **Cancel** to keep working. Saved sessions are available the next time you start.
- **Setup Jard Soundfolio Local.command** prepares a fresh installation. It installs the project dependencies, builds the browser interface and downloads the models. Use Start for everyday launches.

## Using Jard Soundfolio Local

- **Record** captures the selected sources in the Mac app and the microphone in the browser. Both transcribe when you press **Stop**.
- **Live** is on by default and shows a provisional transcript while you speak. You can switch it off or on before or during a recording. Press **Stop** to get the final transcript from the full recording.
- **Open audio** transcribes WAV, MP3, M4A, and other common audio files. Files and microphone recordings can be up to three hours long. Long audio is decoded locally in chunks.
- **Language hints** lets you search for and select several expected languages for recordings, Live, or files. Hints give the model context while automatic language detection continues. Remove every tag to return to Auto. With multiple hints, the offline pass splits at pauses so each utterance can receive its own language detection. Switches within a continuous utterance can still be missed.
- **Speakers** is on by default. Nemotron 3 labels the Live preview and runs a fresh speaker pass after recording or file import. Rumble’s multilingual CTC head places the existing transcript into speaker turns, and words with missing timing stay with a neighboring speaker.
- **Playback** opens automatically when a transcript word, timeline chapter, or chat timestamp starts the audio. The audio button also opens it manually. Expand it to play, pause, scrub, or skip ten seconds in either direction. Collapse it to give the note more room while playback continues. The five bars in the waveform button follow the sound in the recording and fall quiet during silence. Click a transcript word to hear it, or scrub the speaker timeline to the same position. The highlight follows available word timestamps, even with Speakers off. For an untimed word, playback starts at a nearby anchor or its audio block. Open Sessions to return to any saved recording.
- **Transcript search** opens from the magnifying glass or ⌘F / Ctrl+F on the Transcript tab. Use Enter and Shift+Enter to move between matches. Searching keeps playback at its current position.
- The **Speaker analytics** tab shows the final speaker activity across the full recording. Hover to inspect a moment. Click, drag, or use the arrow keys to seek. Each speaker’s share is their detected speaking time divided by the sum of all speakers’ times. Overlap counts for each active speaker. The transcript and timeline use the same labels, with Speaker A corresponding to speaker 1 in the JSON.
- **Overview** creates a short session title, a one-paragraph lead, and a Markdown summary after the final transcript is saved. Qwen reads the entire transcript and writes a user prompt tailored to this talk’s topics, emphasis and structure. A second call receives that exact prompt and the same complete transcript under a fixed editorial system prompt. The title and timeline follow separately. The local engine grows its context for long recordings and reports when the complete request exceeds the model’s limit. A short exchange can fit in one paragraph. Titles aim for four words or fewer, with a five-word maximum in every language. Choose a language and confirm **Switch language** to open a saved version or generate one locally. Cancel keeps the current version. Each version is saved, and **Auto** follows the predominant detected language. **Regenerate** rewrites the selected language’s title, summary and chapters with local Qwen. Overview and Timeline show the draft as Qwen writes. The previous version is kept in SQLite until the replacement finishes, and reloading either tab reconnects to the current draft.
- **Timeline** lists the topics in chronological order, with a description of what the speakers discussed in each chapter. Select a chapter to hear the discussion. All languages share the same playback anchors.
- **Chat** answers questions using the selected session’s transcript, displayed overview and topic timeline. Timestamp references play the original passages. Open the conversation from the bottom of any completed-session view. You can minimize it, change tabs, and continue where you left off. The session saves your messages, replies, and any partial answer you stop.
- **Downloads** offers the selected session’s original audio and transcript JSON. JSON includes detected languages, speaker turns, available word timestamps and speaker activity intervals. If a job stops after processing some audio, you can download the completed portion.
- **Copy** puts the finished text on your clipboard. Use the **Sidebar** button to show or hide your sessions. Click **Search** or press **⌘K** (Ctrl+K on Windows or Linux) to search session titles, filenames, and transcript text. Use the arrow keys and Return to open a result, or Escape to return to your work. Each session in the sidebar has a delete button.

## Local session storage

Jard Soundfolio Local saves each valid upload or stopped recording under `~/Library/Application Support/Rumble/`. The folder keeps its original name so previous installations reopen the same library. SQLite stores session settings, model revisions, transcript text, speaker turns, word timing, speaker analytics, generated overviews, topic anchors, and chat history in `sessions.sqlite3`. The `audio/` directory holds a copy of each original audio file, linked by session ID. Sessions remain playable when an upload is moved or its original is deleted.

A fresh checkout creates an empty database on first launch. The table definitions and migrations live in `session_store.py`, `session_notes.py`, and `writing_cache.py`. For an in-repository development library, set `RUMBLE_DATA_DIR="$PWD/local-data"` when launching. Git ignores that entire directory. Keep personal SQL dumps and transcript exports there, in the ignored top-level `exports/` directory, or outside the checkout.

Saved-session URLs include the recording and selected tab. Refresh, Back and Forward restore that view. Once an upload is accepted, processing continues if the browser tab closes. If the server stops mid-session, the audio and completed chunks are available in Sessions. To remove a session, use its trash button and then **Delete permanently**. This removes its SQLite records and managed audio copy. An independently stored original or downloaded export stays where you saved it.

The writing model reuses common prompt tokens in memory. SQLite keeps the prepared chat context and completed results for exact model requests across restarts. A second session with the same timestamped transcript and speaker assignments can reuse a finished overview or translation. A completed chat answer can also be reused when the question, relevant history, source and model recipe match. Each session keeps its own conversation and audio. Deleting the last session using a cached result also removes that result.

## Maintaining the Mac and web apps

The Mac window renders the same React production build as the browser. Edit screens, sidebar behavior, typography, and themes in `src/` for both clients. The native app uses the system WebView supplied by macOS. The Native SDK window and menu live in `desktop/mac_host.m`. On macOS the sidebar and search controls share the title-bar area with the system window buttons. The Mac host measures those buttons so the web controls stay clear of them when entering and leaving full screen.

Platform-specific UI actions go through `src/desktop.ts`. The Mac host forwards them over a private pipe to `desktop/mac_bridge.py`, which starts the Swift helpers in `desktop/capture/` for microphone access, system audio, and Finder panels. Both clients send audio to the same local Python engine for transcription, speaker labels, playback, and notes. SQLite and managed audio files live in one session library.

## Set up from source

On an Apple Silicon Mac with macOS 26, install Bun, uv, and FFmpeg, then run **Setup Jard Soundfolio Local.command**. Setup provisions Python 3.13, builds the shadcn/ui Luma interface, and prepares the pinned speech and speaker models (about 1.8 GB), plus Qwen3.5-2B Q8_0 (2.08 GB) and its Metal-enabled llama.cpp runtime. Run setup again when upgrading an existing installation to add Overview and Chat. The writing model starts on demand and communicates through a private local socket. After setup, **Start Jard Soundfolio Local.command** runs from disk and works offline.

Run `./"Start Jard Soundfolio Local.command" --no-open` to start the same server without opening a browser.

The offline summary-language catalog follows [Qwen’s published language table](https://qwen.ai/blog?id=qwen3.5). Native language names are adapted from [Wikimedia Language Data](https://github.com/wikimedia/language-data), with additional dialect names. Its [license](licenses/wikimedia-language-data.txt) accompanies the bundled data.

## License and credits

Jard’s application code is free to use and modify under the [Apache License 2.0](LICENSE). Third-party software, model weights, and language data retain their respective licenses.

- Speech recognition and word alignment use [Reson8 Rumble-2-Flash](https://huggingface.co/Reson8/Rumble-2-Flash).
- Speaker labels use [NVIDIA Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization).
- Overviews, timelines, and chat use [Qwen3.5-2B, quantized by axteam](https://huggingface.co/axteam/Qwen3.5-2B-GGUF), running with [llama.cpp](https://github.com/ggml-org/llama.cpp).
