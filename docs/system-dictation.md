# System dictation

In the current source build, open **Settings → System Dictation** to run setup. Allow Microphone and Accessibility in macOS, then try the shortcut in the practice field. By default, hold **Right Option** while you speak and release it to insert text. Double-tap **Right Option** to record hands-free, then press it again to finish. **Option–Shift–V** pastes the last dictation.

To discard a held take, press **Esc** after holding the dictation shortcut for at least 300 ms. In **Tap to start and stop** mode, the **Cancel shortcut** row defaults to **Escape** and lets you record another chord or reset it. Double-tap hands-free takes finish with the dictation shortcut and leave **Esc** available to the current app. Takes already transcribing or waiting to paste stay queued.

The compact dictation indicator stays with you when you switch Spaces, including fullscreen apps. **Top of screen** and **Bottom of screen** follow the display under the pointer. **Near the cursor** anchors it to the original insertion point when macOS exposes the caret.

With **Keep microphone ready** on, Jaro gives ordinary keyboard chords 40 ms to complete before it plays the dictation start cue. With that setting off, the cue waits for the microphone and a committed take. In Hold mode, muting, lowering audio, or pausing media begins only after the 300 ms hold threshold.

In **Dictation → General → Audio and feedback**, open **Sound style**. Use the play button next to a style to hear a sample, or select the style name to save it and hear it. Use the cue buttons to hear individual sounds from the style you last tried. The compact indicator follows the sample through its listening, processing, and result states. Previews play at half volume when dictation sounds are off or their volume is zero. Classic is the default. Styles with a Done cue play it after successful keyboard dictation when delivery takes at least two seconds after the stop cue.

Finishing with the shortcut gives a trailing word 200 ms to finish. If you are still speaking, Jard keeps up to 600 ms and stops at the next brief pause. Speak before ending the gesture so the take is recognized. You can press the shortcut for the next take while the last word is still finishing. With **Keep microphone ready** enabled and **While dictating** set to **Do nothing**, Jard can recover the audio around the next press. In other modes, hold the shortcut until the microphone opens for the new take.

If the microphone disconnects or stops sending audio during an intentional take, Jard can recover the speech it already received. It keeps that text in Paste Last and retained History and copies it when the clipboard is still available for recovery. Use Paste Last to choose where to insert the text.

If a cold microphone fails while the hold is still starting, keep holding the shortcut while Jard reopens it. Releasing before the device is ready ends that attempt.

With **Keep microphone ready** off, Jard retries a selected microphone briefly if it fails to open, then uses the system input for that take. An unplugged device falls back immediately. Jard tries your selected microphone again on the next take.

With **Keep microphone ready** on, Jaro retries a stalled microphone in the background. Press the dictation shortcut to try again immediately. If your selected microphone is unplugged or cannot open, Jaro records from the system input. It returns to your selected microphone between takes once that device is ready. With the MacBook lid closed, Jaro uses an available external microphone. A wired headphone jack still works. Dictation settings show the alternate input or a microphone error while Jaro is waiting for the next take.

Releasing a hold before 300 ms discards the audio. To lock hands-free, complete the second tap within 300 ms of releasing the first. In **Hold or double-tap** mode, holding the second press still works as an ordinary hold.

For a single Option, Command, Control, or Shift shortcut, **Dictation → General → Shortcut side** lets you choose Left, Either, or Right. With Either, you can start from either hand. If you hold both keys, releasing the last one ends the gesture.

With Input Monitoring granted, Jard uses the physical key side when it can distinguish a modifier remapped from Caps Lock. Recording a remapped Control shortcut selects **Either**. Shortcut labels follow your keyboard layout, including dead keys such as **^** on a German keyboard.

If macOS swallows a key release during **Command–Tab**, the next valid shortcut can still start a take or finish a hands-free one.

Under **Other shortcuts**, select **Paste Last** or **Dictate raw text** and press **Delete** to turn that shortcut off. **Reset Paste Last** restores **Option–Shift–V** when it is available. A modified Delete chord can be recorded like any other shortcut.

With dictation enabled, you can also choose **Paste Last Dictation** from Jaro’s menu bar icon, even if you turned off its shortcut. Leave the cursor in the field where you want the text.

To record a modifier-only shortcut, hold the combination in the shortcut recorder and then release the keys. A chord such as Option–Command works with either hand. A chord recorded with two keys of the same modifier, such as both Option keys, needs those two presses within 80 ms. Dictation and Dictate raw text both accept these chords.

Dictation works while the Jard window is closed and the app is running. It uses Rumble2 Flash on the Mac. Settings include the shortcut, input device, language, vocabulary, output, and text-only history. The default history retention is seven days.

In **Dictation → General → Vocabulary**, add up to 32 preferred names or terms. Add comma-separated alternate spellings in **Sounds like**. Jaro saves after you pause typing and when you close the editor.

For a profile with **Run local script**, choose an executable and approve its version in **Dictation → Output**. The executable receives the transcript on standard input and returns its result on standard output. Jaro runs a private copy of the approved contents and sets `JARO_OUTPUT_SCRIPT` to the original file path for scripts that need to find nearby files. Use a wrapper script if the executable needs to launch from its installed location. If the version changes, the transcript is kept and Output asks you to review the new version. Scripts run with your account’s access and are skipped in previews and raw dictation.

For several local steps, select a JSON catalog in the same picker. Each transformation has an ID, display name, executable entry, and optional dependency files and deadline in milliseconds. After reviewing the catalog in **Output**, choose its named steps in the ordered profile editor.

```json
{
  "transformations": {
    "format-lines": {
      "name": "Format lines",
      "entry": "format-lines.sh",
      "files": ["patterns.txt"],
      "timeout": 1000
    }
  }
}
```

The manifest, entry files, and declared dependencies are reviewed together. Paths are relative to the catalog and must stay within its directory. A named executable starts in a private copy of that directory’s declared files. It receives the text on stdin and writes text to stdout. `JARO_TRANSFORM_ID` names the step, and `JARO_TRANSFORM_CONTEXT` contains JSON with the source and any known application and browser host. Use `local:format-lines` to select the same step in a profile managed by `jardctl`.

For local JavaScript or TypeScript steps, select **Create starter configuration** in **Dictation → Output**. Jaro creates an example config, editor types, and notes in `~/.config/jaro`, or in `$XDG_CONFIG_HOME/jaro` when configured. Existing files are preserved. Use **Reveal script** to find the file and open it in your editor.

`jardctl transforms init` creates the same files with the app closed. Output offers **Use configuration** when it finds one there. Run `jardctl transforms check` to validate the config and list its steps. Both commands accept `--directory` for a different folder and `--json` for structured output. Choose another config with **Choose a local script…**. Keep one `jaro.config.js`, `.mjs`, `.ts`, or `.mts` per folder.

The config exports named transformations. Each receives the current text and available foreground metadata and returns a string or a promise of one.

In **Dictation → Output**, enable **Share page and window details** for the reviewed version if a transformation needs `context.browserUrl` or `context.windowTitle`. The URL can include its path, query, and fragment. The fields are optional and describe the page and window active when you finish dictating. You can remove that permission without removing the transformation.

```ts
import { defineJaroConfig } from "@jaro/commands"

export default defineJaroConfig({
  transformations: {
    "format-lines": {
      name: "Format lines",
      timeout: 1000,
      transform: (text: string) => text.split("\n").map(line => line.trim()).join("\n"),
    },
  },
})
```

Static relative imports can load JavaScript, TypeScript and JSON files within the configuration directory. Jaro approves the exact entry and import versions together. A missing file, expired deadline, or changed configuration keeps the text from before the failed stage. While Output is open, edits refresh the review. A failed load displays the error and keeps the last valid definitions visible for reference.

If the configuration uses `fetch` or imports `node:child_process`, **Dictation → Output** offers separate checkboxes for network requests and programs. Both start off for each version. Review the destinations shown, choose which capabilities the configuration may use, and select **Allow this version**. Change an existing grant with **Update permissions**. Requests can send the text and available app or site context. Programs run with your macOS account's access. Destinations and commands can also be computed at runtime.

`fetch` accepts absolute HTTP and HTTPS URLs, headers, and string bodies. It supports response text, JSON, and binary buffers, and accepts an `AbortSignal`. The program API provides callback-based `execFile` and `exec` and `promisify` from `node:util`. Use `execFile` with the `input` option to pass dictation on standard input. Pending requests and program groups end when the transformation returns, times out, or loses its approval.

During an approved transformation, `console.log`, `info`, `debug`, `warn`, and `error` write to Jaro's private local log, available through `jardctl logs`. A manual `jardctl transforms check` also logs console messages from configuration loading. Background review stays quiet. Network and program records include the host or program, duration, status, and byte counts. Leave dictated text and secrets out of console messages you write.

Mac administrators can force Boolean keys in the `local.jardaudio.desktop` preference domain. `DisableTransforms` bypasses built-in and custom transformations while leaving word replacements and local rewriting available. `RestrictTransformsToLoopback` turns off program execution and keeps network requests on this Mac, including after redirects. Output shows when these restrictions apply. Saved profiles and approvals remain available when the organization removes a restriction.

Ordinary requests use `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `NO_PROXY` overrides and the Mac’s configured HTTP and HTTPS proxies and exceptions. On a network that provides only automatic proxy configuration, set an explicit environment proxy for the transformation requests.

In **Dictation → History**, hover over a row to copy its final transcript. Drag across rows or Shift-click to select a range, or Command-click to add or remove individual rows. Copy from the detail pane to copy the selection.

Open **Dictation → General → Output and vocabulary corrections → Ordered output profiles** to add **Lowercase**, **SpongeBob case**, or **No trailing punctuation**. Arrange the steps in the order you want and try them with sample text. Website rules accept a hostname or a pasted URL. Application rules accept the running app name, its Finder name, or its bundle ID.

A local rewrite formats spoken cues such as “point one” and “point two” as a numbered list by default. Set a different list style in the profile’s rewrite instructions. If a rewrite changes a numeric literal such as `1200` or `−12.5%`, Jaro keeps the text that entered that rewrite and continues with later steps. Dictating only numbers skips rewriting.

Jard chooses the output profile from the app and page in front when you finish speaking. If the page is unavailable, it uses an app profile or the default. Automatic insertion still checks the field where you started. If you've moved away, use Paste Last to insert the result.

If Jard cannot identify the page in Safari or a Chromium browser, switch to that browser and return to **Dictation → General → Output and vocabulary corrections → Browser tab access**. Choose **Allow** for that browser and respond to the macOS Automation prompt. Jard uses only the active tab’s hostname for website matching.

Consecutive takes and **Paste Last** join at the same insertion point with spacing and capitalization that follow the previous text. Typing, clicking, moving the caret, switching apps, copying a dictation, or inserting a meeting excerpt starts a new sequence.

With **Launch at login** enabled and dictation ready, Jaro starts in the background after you sign in. It opens the workspace if startup needs your attention. If macOS asks for approval, choose **Open Login Items** and allow Jaro in System Settings. Click Jaro in the Dock or switch to it with **Command–Tab** to bring back a closed workspace.

Turn off **Show Dock icon** in **Settings → Application** to keep Jard in the menu bar after closing its window. Choose **Open Jaro** from that menu to restore the window and Dock icon. Jard keeps the Dock icon visible while its window is open or minimized, and when the menu bar item is unavailable.

In **Settings** or **Dictation**, press **Command–K** or choose **Find settings**. Type a control name or group, then use the arrow keys and Return to reveal it. From Dictation History, a result opens the matching control in General. The sidebar magnifier opens session search.

Choose **Look for new versions** beside Settings or under **Updates** in **Settings → General** to check for a signed release. When Jard has downloaded and verified it, both controls offer **Restart to Update**. Finish any active work before restarting. The current app and library reopen from the same location.

If a download is interrupted, choose **Look for new versions** again. Jaro resumes the same signed archive when the server supports partial downloads, including after you reopen the app.

When a downloaded Development release includes notes, Jaro shows up to three changes in the sidebar. Expand **Release notes** there or in **Settings → General** to read the full notes.

An open browser workspace shares the local engine and can hold a restart. Closing its connection releases that hold. If a browser connects while an update is restarting the engine, it reconnects when the engine returns.

To check meeting audio permissions before recording, open **Settings → Meetings → Recording → System Audio Recording**. Choose **Allow Mac audio** or enable Jaro in the **System Audio Recording Only** list in macOS Privacy & Security. If you return and audio-only access is still off, **Reset Mac audio access** lets you request that grant again. An existing Screen Recording grant remains available as a fallback.

When a meeting transcript finishes, Jard’s local writing model creates a short title and summary in the language spoken. Open the meeting’s **Overview** to read it, choose **Summarize meeting** on an earlier meeting, or choose **Regenerate summary** to try again. Jard includes the saved summary in meeting notes and transcript Markdown exports.

During a meeting, Jard also gives the recording a provisional title once enough speech has been transcribed. It appears in the recording capsule and is saved with the meeting. Calendar titles retain precedence.

Choose **View live transcript** in **Settings → Meetings** while recording, or open Jaro from its meeting status menu. New turns stay in view while you’re at the bottom. Scroll up to read earlier turns, then return to the bottom to follow the meeting again.

Use **Settings → Meetings → Detected calls** to choose **Ask**, **Always record**, or **Never offer** for a call provider. Saved website choices apply to their specific site. Always record uses the permissions you’ve already granted and applies when Jaro detects a qualifying call. Unknown browser pages still require an explicit recording choice.

Turn off **Automatic titles and summaries** in **Settings → Meetings** to stop Jard from writing new provisional titles and short summaries automatically, including for the current recording. Saved titles and summaries remain available. You can still choose **Summarize meeting** in Overview.

Mac administrators can force the Boolean `DisableMeetings` in the `local.jardaudio.desktop` preference domain. After relaunch, Jaro pauses meeting recording, detection, Calendar offers, transcript recovery, and generation of titles, summaries, and overviews. Saved meeting content remains available in Sessions, exports, and companion tools. Removing the restriction and relaunching restores preferences and resumes pending work.

Choose a **Meeting export folder** in **Settings → Meetings** to save Markdown automatically as new meetings finish. Jard uses the meeting time and title in the filename and adds a number if the name is taken. Use **Reveal Markdown** in a saved meeting to select its file in Finder. **Turn Off** stops new automatic exports. The **Meeting exports** menu also offers on-demand saves.

Managed transcripts include the short summary once it is ready. Turn on **Separate notes file** to export a `.transcript.md` file and a matching `.notes.md` file with a link to the transcript. Jard preserves changes you make to either file and respects a deleted notes file.

Use **Rename meeting** beside a saved meeting's title. Your title takes precedence over calendar and generated titles. Turn on **Descriptive meeting filenames** in **Settings → Meetings** to update the managed filenames when the title changes. The original date and time stay in the filename. If the meeting expires, Jard moves unchanged managed exports to macOS Trash and keeps your edited files and manually saved copies.

To move a finished meeting to the macOS Trash, use its delete button in **Sessions** and choose **Move to Trash**. Jaro archives the meeting and moves unchanged automatic Markdown exports with it. Edited exports and manually saved copies stay in their original folders.

After **Stop & Save**, the capsule shows transcription and summary progress, then the saved title and duration. **Copy New** copies turns you haven’t copied or pasted through Jard. **Copy All** copies the transcript as Markdown, and **Copy Agent Prompt** copies the local command for the saved meeting. The capsule stays for 15 seconds after transcription finishes and waits for the pointer to leave before closing. You can close it immediately with **×**.

With Paste output, Jard types into Windows App, Microsoft Remote Desktop, and apps based on Chromium 155 or 156 to avoid their clipboard paste issues. If you switch fields before typing finishes, the full dictation is still available through **Paste Last**.

If macOS enables Secure Event Input, Jard finishes any active dictation and keeps the text for recovery. The annotation chord can still open the capture picker. Paste shortcuts show feedback until you return to an editable field with secure input off.

Saved display, area, window, and wired phone or tablet recordings have an **Annotation** tab in Sessions. Play the video, jump to a moment from the contact sheet, or save the sheet and the timestamped strokes as separate files. These remain available if narration transcription is interrupted. The strokes use normalized coordinates relative to the recorded source.

With Accessibility enabled, press and release **Option–Shift–S** to open the macOS display picker. While recording, tap the same chord to save or hold it and move the pointer to draw. You can also Shift-drag or use the **Draw** button. **Esc** exits drawing and keeps the recording running. If the chord is already assigned to a dictation action, start the capture from Settings.

`jardctl annotate list` lists saved annotations newest first. `jardctl annotate show latest` returns the latest annotation's narration, frame times, and paths to the movie and available assets. You can give `show` a Session UUID or unique prefix instead of `latest`. Both commands emit JSON and work with Jard closed.

**Copy for Agent** on the Annotation tab copies the written context as Markdown. Apps that accept rich text also get the contact sheet above the context. To get the same Markdown from a terminal, run `jardctl annotate context latest`. An agent attached to `jardctl mcp` can call `list_annotations` and `get_annotation` with Jard closed. `get_annotation` includes the contact sheet as an inline image on its first page when one is available. Pass the returned annotation ID and `next_cursor` to read more narration.

**Paste into Last App** returns to the previous app and pastes the contact sheet followed by the written context. Supported terminal apps receive the sheet's file path. Jard restores the original clipboard after the paste and leaves the field ready for review.

**Send to OpenCode** lists open sessions from a running local OpenCode v2 service. Search by title, project path, or session ID, select one, and choose **Send annotation**. The prompt queues the narration and contact sheet together. The saved annotation stays available in Jard. Under **OpenCode deliveries**, choose **Undo send** within ten minutes. Jard withdraws an inbox item or rolls back the turn it started if the inbox and timeline show no newer input. **Restore files and undo** requires confirmation because OpenCode can restore files changed during that turn, including edits made outside OpenCode. If the turn contains other work, **Send correction** asks OpenCode to disregard that specific message. The receipt stays visible after you reopen Jard. For an unconfirmed rollback, **Check rollback status** reads the session again.

The destination picker can suggest a session mentioned in the narration or one whose project matches the captured app. Select **Remember destination** to prefer a session for this app, a verified browser site, or a terminal project on later annotations. Terminal rules cover project subfolders, and the closest matching project wins. Jard can also suggest the OpenCode conversation shown in the terminal. **Forget** removes the preference. To enable automatic sending for future matching captures, select the automatic option before saving the destination. Jard shows the chosen session when narration is ready. Hover over the three-second countdown to pause it or choose **Cancel**. If OpenCode is unavailable before a send, Jard keeps the capture queued for up to a day and checks again when Jard is open. A resumed send gets a fresh countdown. A send without confirmation appears as **Delivery unconfirmed** for review. If the saved session is unavailable, the picker still shows other open sessions.

Mac administrators can force the Boolean `DisableOpenCodeProcessing` in the `local.jardaudio.desktop` preference domain. Relaunch Jaro to apply or remove the restriction. The restriction turns off OpenCode session discovery, delivery, and changes to deliveries. Existing annotations, destination preferences, receipts, and pending sends stay on this Mac. You can still copy or export annotations and cancel a pending send. The CLI returns a `managed` error for restricted operations.

The forced Boolean `DisableAnnotate` turns off new screen annotations after relaunch. Annotate Settings shows the restriction, the capture shortcut is released, and the CLI returns `managed` if asked to start a capture. Existing annotations can still be opened, exported, and shared.

With Jard running, use `jardctl annotate destinations --query project-name` to find a session, then `jardctl annotate send latest --to SESSION_ID`. Pass the result’s `service` as `--service` to bind the send to the service you listed. Use `jardctl annotate destinations --capture latest` to inspect a capture’s suggestion and `annotate remember` or `annotate forget` to manage its preference. Add `--automatic` to opt in when saving a rule. Use `jardctl annotate route CAPTURE_ID` to check a pending send or add `--cancel` to cancel it, including while Jard is closed. Read recent receipts with `jardctl annotate deliveries latest` and use their `deliveryId` with `jardctl annotate undo DELIVERY_ID` for inbox withdrawal or turn rollback. If the result is `rollback-files`, add `--restore-files` to authorize file restoration. Use `jardctl annotate correct DELIVERY_ID` to send a correction into the conversation.

To annotate a wired iPhone or iPad, connect it over USB, unlock it, and trust the Mac when asked. Select **Wired iPhone or iPad** in **Settings → Screen Annotation**, or run `jardctl annotate start --source phone`. Allow Camera access to open the mirror and Microphone access for Mac narration. Draw on the mirrored picture and choose **Stop & Save** to add it to Sessions.

With Jard running, `jardctl annotate start --source window` opens the system picker. Sources are `display`, `window`, `area`, and `phone`. You can also pass `--language`. `jardctl annotate status` reports the capture ID and elapsed recording time. `jardctl annotate stop` starts saving in the background. Use the capture ID with `annotate show` once saving finishes. `jardctl annotate cancel` discards the unsaved take. If saving has already started, it returns that state with `changed: false`.

## Read history with jardctl

The packaged helper at `/Applications/Jaro.app/Contents/Helpers/jardctl` reads retained dictations even when Jard is closed. It uses the last persisted macOS history and applies the selected retention period before searching.

```sh
jardctl history --since 3d --app Notes --grep release --text
jardctl history --search "paperback schedule" --limit 10 --json
jardctl history --raw --oldest-first --json
```

## Control the Mac app with jardctl

With Jard running and dictation enabled, `jardctl dictation start --raw` starts a take without output processing. `jardctl dictation stop --print` delivers the text only to stdout and accepts `--json` for structured output. Printed takes appear in History when retention is enabled. If Jard hears no speech, the command exits with code 4 and leaves stdout empty.

With Jaro running, use `jardctl vocabulary list --json`, `jardctl vocabulary add "Mei Lin" --sounds-like "May Lean,May Linn"`, or `jardctl vocabulary remove "Mei Lin"`. Added terms appear in Dictation settings and provide context to Rumble2 Flash. Alternate spellings correct whole phrases during normal dictation and output previews. Add and remove match terms case-insensitively.

Use `jardctl meetings record --title "Design review"` to name a meeting before recording starts. The title appears during recording and stays with the meeting after saving or recovery. The command uses the permissions already granted to Jaro. Omit `--title` to use the usual title selection.

Run `jardctl transcribe "recording.m4a"` to transcribe a local audio or video file. Jaro uses Rumble2 Flash and processes long tracks in pieces. Pass `--language en` to supply a language hint or `--json` for structured output. The command can use an installed model with Jaro closed. If the speech model is missing, an interactive terminal offers to download the pinned assets. Scripts can check readiness with `jardctl model status --json` and explicitly prepare it with `jardctl model install`. Add `--from FOLDER` to verify and install an existing local copy. Silence exits with code 4 and leaves stdout empty.

Run `jardctl logs -n 50` to read recent desktop and recording events, or `jardctl logs --process -f` to follow the engine log until interrupted. Both commands work with Jaro closed. The structured event log stores phases and timestamps without the spoken text or its destination.

The packaged CLI can start its own app bundle and request a normal quit or restart. Quit and restart return a busy error while Jard has an active capture. A restart reopens the same app bundle. A staged update can install during either action once the shared engine is idle.

```sh
jardctl start
jardctl --json status
jardctl restart
jardctl quit
```

`quit --force` and `restart --force` stop active captures first. Saved recordings and meetings continue processing locally. Jard finishes saving screen annotations and discards unfinished shortcut and SDK takes. Verified updates still wait for the shared engine to be idle.

Use `jardctl window show [pane]` to bring the workspace forward. The default pane is General Settings. You can also choose `home`, `dictation`, `history`, `output`, `meetings`, or `annotate`. While the active session is busy, Jaro opens the requested pane when it can navigate. `jardctl window hide` hides the workspace and keeps the app and any active capture running.

With Jard running, `settings` shows the available dictation preferences and changes them immediately. `settings apply` also works when Jard is closed, and the changes take effect on the next launch. An `apply` document is a JSON object keyed by setting name. A bad key or value leaves the entire batch unapplied. Shortcut labels are read-only. Change the shortcut itself through Dictation settings. `launch-at-login` reads or sets macOS Launch at Login, and `check-for-updates` asks the desktop updater to check now.

If your build offers both update feeds, choose **Update channel** in Settings. The sidebar and app menu show the release version before a switch. Confirm the release to download it, then use **Restart to Update** once it is ready. With Jard running, `jardctl update-channel` shows the installed and selected channels. Use `jardctl update-channel dev` or `jardctl update-channel stable` to select one, and `jardctl update-channel --confirm OFFER` with the digest returned in the current offer to start the download.

```sh
jardctl settings list
jardctl settings get dictation.sound
jardctl settings set dictation.mode holdDouble
printf '%s\n' '{"dictation.sound":false,"dictation.languages":["en"]}' | jardctl settings apply -
jardctl launch-at-login
jardctl check-for-updates
```

With Jaro running, use `jardctl history clear --yes` to delete every retained dictation and reset Paste Last. To get the number removed as JSON, run `jardctl history clear --yes --json`. Saving a new dictation after clearing follows the current retention setting. History reads use the running app when available and work offline from the saved preference file.

`--grep` matches literal text in the original or final transcript. `--search` ranks both versions and returns each dictation once. Quoted phrases match consecutive words by their stems. A result snippet can show the matching excerpt from either version. `--raw` includes the full original transcript. A date in `--until` includes that entire local day. `history` with just the original `--query` and `--offset` options still returns the 15-entry JSON page.
