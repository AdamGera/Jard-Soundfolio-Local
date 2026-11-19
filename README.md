# Jard Soundfolio Local

*Your recordings, transcripts, and notes stay on your Mac.*

Record a conversation or open an audio file on an Apple Silicon Mac. Search the transcript, see who spoke, and jump from a note or chat answer to the moment you want to hear again. Setup downloads the models once. After that, transcription, speaker labeling, and writing work offline.

## Why Jard made this free

At Jard (Joint AI Research & Dynamics), we believe inference belongs close to the source when the hardware can do the work. For a conversation, that means your Mac can turn speech into a timestamped transcript once and use it again whenever you need a quote, a summary, or an answer. We made Jard Soundfolio Local free and open source so anyone can use it, inspect how it handles their recordings, or adapt it. [Jard](https://www.jard.ai/) · [Our manifesto](https://www.jard.ai/manifesto)

## Get started on your Mac

Use an Apple Silicon Mac running macOS 26 with Bun, uv, and FFmpeg installed. Download and extract the [Jard Soundfolio Local source ZIP](https://github.com/AdamGera/Soundfolio-Local/releases/download/v0.1.0/Jard-Soundfolio-Local.zip) or clone this repository. In Finder, double-click **Setup Jard Soundfolio Local.command**. When Terminal shows **Ready**, open **Start Jard Soundfolio Local.command**. Allow about 4 GB for the models, plus space for dependencies and your recordings.

The browser interface connects to the engine on your Mac at `http://127.0.0.1:8765`. You can also use the welcome page.

Open **[README.html](README.html)** from Finder. Its **Set up**, **Start**, and **Quit** buttons launch their matching Terminal commands through the bundled Jard Soundfolio Local Launcher. If the browser asks to open the launcher, choose **Open**. On a fresh Mac, double-click **Jard Soundfolio Local Launcher.app** once to register it and open the page. **Welcome to Jard Soundfolio Local.command** also opens it. Keep the launcher next to the HTML file.

- Double-click **Start Jard Soundfolio Local.command** to open the app in your browser. The server runs in the background. You can close the launcher’s Terminal window. Double-click Start again to reopen a running app.
- Double-click **Quit Jard Soundfolio Local.command** to stop the local server and release its models. Inside the app, the small power button in the header opens a confirmation. Confirm to quit or choose **Cancel** to keep working. Saved sessions are available the next time you start.
- **Setup Jard Soundfolio Local.command** prepares a fresh installation. It installs the project dependencies, builds the browser interface and downloads the models. Use Start for everyday launches.

## Using Jard Soundfolio Local

- **Record** captures your microphone and transcribes when you press **Stop**.
- **Live** is on by default and shows a provisional transcript while you speak. You can switch it off or on before or during a recording. Press **Stop** to get the final transcript from the full recording.
- **Open audio** transcribes WAV, MP3, M4A, and other common audio files. Files and microphone recordings can be up to three hours long. Long audio is decoded locally in chunks.
- **Language hints** lets you search for and select several expected languages for recordings, Live, or files. Hints give the model context while automatic language detection continues. Remove every tag to return to Auto. With multiple hints, the offline pass splits at pauses so each utterance can receive its own language detection. Switches within a continuous utterance can still be missed.
- **Speakers** is on by default. Nemotron 3 labels the Live preview and runs a fresh speaker pass after recording or file import. Rumble’s multilingual CTC head places the existing transcript into speaker turns, and words with missing timing stay with a neighboring speaker.
- **Playback** opens from the audio button. Expand it to play, pause, scrub, or skip ten seconds in either direction. Collapse it to give the note more room while playback continues. The waveform button shows when audio is playing. Click a transcript word to hear it, or scrub the speaker timeline to the same position. The highlight follows available word timestamps, even with Speakers off. For an untimed word, playback starts at a nearby anchor or its audio block. Open Sessions to return to any saved recording.
- **Transcript search** opens from the magnifying glass or ⌘F / Ctrl+F on the Transcript tab. Use Enter and Shift+Enter to move between matches. Searching keeps playback at its current position.
- The **Speaker analytics** tab shows the final speaker activity across the full recording. Hover to inspect a moment. Click, drag, or use the arrow keys to seek. Each speaker’s share is their detected speaking time divided by the sum of all speakers’ times. Overlap counts for each active speaker. The transcript and timeline use the same labels, with Speaker A corresponding to speaker 1 in the JSON.
- **Overview** creates a short session title, a one-paragraph lead, and a Markdown summary after the final transcript is saved. The local writing model first reads the recording and prepares a brief for that particular conversation. Titles aim for four words or fewer, with a five-word maximum in every language. Choose a language and confirm **Switch language** to open a saved version or generate one locally. Cancel keeps the current version. Each version is saved, and **Auto** follows the predominant detected language. **Regenerate** rewrites the selected language’s title, summary and chapters with local Qwen. Overview and Timeline show the draft as Qwen writes. The previous version is kept in SQLite until the replacement finishes, and reloading either tab reconnects to the current draft.
- **Timeline** lists the topics in chronological order, with a description of what the speakers discussed in each chapter. Select a chapter to hear the discussion. All languages share the same playback anchors.
- **Chat** answers questions using the selected session’s transcript, displayed overview and topic timeline. Timestamp references play the original passages. Open the conversation from the bottom of any completed-session view. You can minimize it, change tabs, and continue where you left off. The session saves your messages, replies, and any partial answer you stop.
- **Downloads** offers the selected session’s original audio and transcript JSON. JSON includes detected languages, speaker turns, available word timestamps and speaker activity intervals. If a job stops after processing some audio, you can download the completed portion.
- **Copy** puts the finished text on your clipboard. Use **Sessions** to search titles, filenames, and transcript text, reopen a recording, or delete it.

## Local session storage

Jard Soundfolio Local saves each valid upload or stopped recording under `~/Library/Application Support/Rumble/`. The folder keeps its original name so previous installations reopen the same library. SQLite stores session settings, model revisions, transcript text, speaker turns, word timing, speaker analytics, generated overviews, topic anchors, and chat history in `sessions.sqlite3`. The `audio/` directory holds a copy of each original audio file, linked by session ID. Sessions remain playable when an upload is moved or its original is deleted.

A fresh checkout creates an empty database on first launch. The table definitions and migrations live in `session_store.py`, `session_notes.py`, and `writing_cache.py`. For an in-repository development library, set `RUMBLE_DATA_DIR="$PWD/local-data"` when launching. Git ignores that entire directory. Keep personal SQL dumps and transcript exports there, in the ignored top-level `exports/` directory, or outside the checkout.

Saved-session URLs include the recording and selected tab. Refresh, Back and Forward restore that view. Once an upload is accepted, processing continues if the browser tab closes. If the server stops mid-session, the audio and completed chunks are available in Sessions. To remove a session, use its trash button and then **Delete permanently**. This removes its SQLite records and managed audio copy. An independently stored original or downloaded export stays where you saved it.

The writing model reuses common prompt tokens in memory. SQLite keeps the prepared chat context and completed results for exact model requests across restarts. A second session with the same timestamped transcript and speaker assignments can reuse a finished overview or translation. A completed chat answer can also be reused when the question, relevant history, source and model recipe match. Each session keeps its own conversation and audio. Deleting the last session using a cached result also removes that result.

## Set up from source

On an Apple Silicon Mac with macOS 26, install Bun, uv, and FFmpeg, then run **Setup Jard Soundfolio Local.command**. Setup provisions Python 3.13, builds the shadcn/ui Luma interface, and prepares the pinned speech and speaker models (about 1.8 GB), plus Qwen3.5-2B Q8_0 (2.08 GB) and its Metal-enabled llama.cpp runtime. Run setup again when upgrading an existing installation to add Overview and Chat. The writing model starts on demand and communicates through a private local socket. After setup, **Start Jard Soundfolio Local.command** runs from disk and works offline.

Run `./"Start Jard Soundfolio Local.command" --no-open` to start the same server without opening a browser.

The offline summary-language catalog follows [Qwen’s published language table](https://qwen.ai/blog?id=qwen3.5). Native language names are adapted from [Wikimedia Language Data](https://github.com/wikimedia/language-data), with additional dialect names. Its [license](licenses/wikimedia-language-data.txt) accompanies the bundled data.

## License and credits

Jard’s application code is free to use and modify under the [Apache License 2.0](LICENSE). Third-party software, model weights, and language data retain their respective licenses.

- Speech recognition and word alignment use [Reson8 Rumble-2-Flash](https://huggingface.co/Reson8/Rumble-2-Flash).
- Speaker labels use [NVIDIA Nemotron 3 Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization).
- Overviews, timelines, and chat use [Qwen3.5-2B, quantized by axteam](https://huggingface.co/axteam/Qwen3.5-2B-GGUF), running with [llama.cpp](https://github.com/ggml-org/llama.cpp).
