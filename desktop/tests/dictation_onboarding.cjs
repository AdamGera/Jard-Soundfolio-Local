#!/usr/bin/env node
// Run after bun run build with playwright-core and its WebKit installed.
const assert = require("node:assert/strict")
const { readFileSync } = require("node:fs")
const path = require("node:path")
const { webkit } = require("playwright-core")

async function run() {
  const browser = await webkit.launch()
  try {
    const page = await browser.newPage()
    page.setDefaultTimeout(7000)
    const errors = []
    page.on("pageerror", error => errors.push(error.message))
    const dist = path.resolve(__dirname, "../../dist")
    const meetingId = "3196de0c-cd84-4211-9367-98b5d859c20a"
    const annotationId = "bd09312a-4184-429e-8477-67be0d69237d"
    const saved = { id: meetingId, name: "Fixture meeting", created: new Date().toISOString(), status: "complete", processed: 10,
      meeting: true, meetingProvider: "Zoom", recording: true, languageHints: ["en"], audioUrl: `/api/sessions/${meetingId}/audio`,
      meetingMicrophoneWarning: "digitalSilence",
      transcript: { text: "Start here. Review the agenda.", durationSeconds: 10, detectedLanguages: ["English"], words: [],
        segments: [{ speaker: 1, start: 0, end: 2, text: "Start here." }, { speaker: 2, start: 7, end: 10, text: "Review the agenda." }],
        speakerNames: { 1: "You", 2: "Them" }, speakerActivity: null } }
    const annotationRoute = `/api/sessions/${annotationId}/annotation`
    const annotated = { ...saved, id: annotationId, name: "Fixture annotation", meeting: false,
      audioUrl: `/api/sessions/${annotationId}/audio`, annotation: { version: 1, strokeCount: 2,
        source: "area", frames: [.1, .6], manifestUrl: `${annotationRoute}/manifest`, contactSheetUrl: `${annotationRoute}/contact-sheet` } }
    let showSaved = false
    let showAnnotation = false
    let notesChunkMissing = false
    let holdSessionRestore
    let personalNotes = { revision: 1, document: { type: "doc", content: [
      { type: "paragraph", content: [{ type: "text", text: "Saved fixture notes" }] },
    ] } }
    let retention = null
    let wholeRetention = null
    let automaticNotes = true
    let meetingBrief = null
    let meetingBriefReads = 0
    let meetingTitle = null
    let managedMeetings = false
    let meetingTrashed = false
    let trashMeetingRequests = 0
    let managedExport = { status: "ready", filename: "2026-09-17 0815 Fixture meeting.md", error: null }
    const silence = Buffer.alloc(32044)
    silence.write("RIFF", 0); silence.writeUInt32LE(silence.length - 8, 4); silence.write("WAVEfmt ", 8)
    silence.writeUInt32LE(16, 16); silence.writeUInt16LE(1, 20); silence.writeUInt16LE(1, 22)
    silence.writeUInt32LE(16000, 24); silence.writeUInt32LE(32000, 28)
    silence.writeUInt16LE(2, 32); silence.writeUInt16LE(16, 34); silence.write("data", 36); silence.writeUInt32LE(32000, 40)
    const fixtureMovie = process.env.JARD_TEST_ANNOTATION_MOVIE
    const clip = fixtureMovie ? readFileSync(fixtureMovie) : silence
    await page.route("**/*", async route => {
      const { pathname, protocol, searchParams } = new URL(route.request().url())
      if (protocol === "blob:") return route.continue()
      // Dynamic chunks import the entry too. Keep its URL stable to boot React only once.
      if (/^\/assets\/index-[\w-]+\.js$/.test(pathname)) {
        const body = readFileSync(path.join(dist, pathname), "utf8")
        return route.fulfill({ contentType: "text/javascript", headers: { "Cache-Control": "no-store" },
          body: notesChunkMissing ? body.replace(/personal-notes-[\w-]+\.js/g, "fixture-missing-notes.js") : body })
      }
      if (pathname === "/assets/fixture-missing-notes.js") {
        return route.fulfill({ status: 404, headers: { "Cache-Control": "no-store" }, body: "Fixture missing Notes module" })
      }
      if (pathname === `/api/sessions/${annotationId}`) return route.fulfill({ json: annotated })
      if (pathname === annotated.audioUrl) {
        const contentType = fixtureMovie ? "video/mp4" : "audio/wav"
        const range = /^bytes=(\d+)-(\d*)$/.exec(route.request().headers().range ?? "")
        if (!range) return route.fulfill({ body: clip, contentType })
        const start = Number(range[1])
        const end = range[2] ? Math.min(Number(range[2]), clip.length - 1) : clip.length - 1
        if (start > end) return route.fulfill({ status: 416, headers: { "Content-Range": `bytes */${clip.length}` } })
        return route.fulfill({ status: 206, body: clip.subarray(start, end + 1), contentType,
          headers: { "Content-Range": `bytes ${start}-${end}/${clip.length}`, "Accept-Ranges": "bytes" } })
      }
      if (pathname === annotated.annotation.contactSheetUrl) return route.fulfill({ contentType: "image/png",
        body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jL1sAAAAASUVORK5CYII=", "base64") })
      if (pathname === `/api/sessions/${meetingId}/meeting-brief`) {
        if (route.request().method() === "POST") {
          meetingBriefReads = 0
          meetingBrief = { ...(meetingBrief ?? {}), status: "queued", error: null }
        } else if (meetingBrief?.status === "queued" && ++meetingBriefReads >= 2) {
          meetingBrief = meetingBrief.summary
            ? { ...meetingBrief, status: "error", error: "The local writer could not restart." }
            : { status: "ready", title: "Agenda approval", summary: "The group approved the agenda.", error: null }
        }
        return route.fulfill({ json: meetingBrief })
      }
      if (pathname === `/api/sessions/${meetingId}/managed-export`) return route.fulfill({ json: managedExport })
      if (pathname === `/api/sessions/${meetingId}/meeting-title` && route.request().method() === "PUT") {
        meetingTitle = route.request().postDataJSON().title
        managedExport = { ...managedExport, filename: `2026-09-17 0815 ${meetingTitle}.md` }
        return route.fulfill({ json: { title: meetingTitle, export: managedExport } })
      }
      if (pathname === `/api/sessions/${meetingId}/personal-notes`) {
        if (route.request().method() === "PUT") {
          const edit = route.request().postDataJSON()
          if (edit.revision !== personalNotes.revision) return route.fulfill({ status: 409, json: {} })
          personalNotes = { document: edit.document, revision: edit.revision + 1 }
          return route.fulfill({ json: { revision: personalNotes.revision } })
        }
        return route.fulfill({ json: personalNotes })
      }
      if (pathname === "/api/meetings/audio-retention") {
        if (route.request().method() === "PUT") retention = Number(searchParams.get("days")) || null
        return route.fulfill({ json: { days: retention } })
      }
      if (pathname === "/api/meetings/automatic-notes") {
        if (route.request().method() === "PUT") automaticNotes = route.request().postDataJSON().enabled
        return route.fulfill({ json: { enabled: automaticNotes } })
      }
      if (pathname === "/api/meetings/retention") {
        if (route.request().method() === "PUT") {
          wholeRetention = Number(searchParams.get("days"))
          saved.meetingRetentionDays = wholeRetention || null
        }
        return route.fulfill({ json: { days: wholeRetention } })
      }
      if (pathname === "/api/sessions" && showSaved) {
        const { id, name, status, processed, created, meeting, meetingProvider, meetingRetentionDays, audioRemovedAt, finalizationPending } = saved
        const row = { id, name: meetingTitle ?? meetingBrief?.title ?? name, status, processed, created, meeting, meetingProvider, meetingRetentionDays, audioRemovedAt, finalizationPending }
        const rows = meetingTrashed ? [] : [row]
        if (!searchParams.has("meetings")) rows.push({ ...row, id: "ordinary", name: "Ordinary recording", meeting: false })
        if (showAnnotation && !searchParams.has("meetings")) rows.unshift({ id: annotationId, name: annotated.name,
          status: annotated.status, created: annotated.created, processed: annotated.processed, meeting: false })
        return route.fulfill({ json: rows })
      }
      if (pathname === `/api/sessions/${meetingId}`) {
        if (route.request().method() === "DELETE") {
          if (++trashMeetingRequests === 1) return route.fulfill({ status: 409, json: { detail: "Wait for the notes to finish." } })
          meetingTrashed = true
          return route.fulfill({ status: 202, json: { trashPending: true } })
        }
        if (meetingTrashed) return route.fulfill({ status: 404, json: { detail: "Session not found." } })
        if (holdSessionRestore) await holdSessionRestore
        return route.fulfill({
          json: { ...saved, meetingTitle, meetingBrief, personalNotes, meetingsManaged: managedMeetings,
            meetingExport: meetingTitle ? managedExport : { status: "pending", filename: null, error: null } },
        })
      }
      if (pathname === `/api/sessions/${meetingId}/audio` && route.request().method() === "DELETE") {
        saved.audioUrl = null
        saved.audioRemovedAt = new Date().toISOString()
        return route.fulfill({ json: { audioRemovedAt: saved.audioRemovedAt } })
      }
      if (pathname === `/api/sessions/${meetingId}/audio`) return route.fulfill({ body: silence, contentType: "audio/wav" })
      if (pathname.endsWith("/stream")) return route.fulfill({ status: 204 })
      if (pathname.startsWith("/api/")) return route.fulfill({
        json: pathname === "/api/status" ? { status: "ready", languages: [], diarization: true } : [],
      })
      return route.fulfill({ path: path.join(dist, pathname === "/" ? "index.html" : pathname) })
    })
    const interfaceCatalog = JSON.parse(readFileSync(path.join(__dirname, "../interface-languages.json"), "utf8"))
    await page.addInitScript(interfaceCatalog => {
      const state = {
        enabled: false, onboarding: sessionStorage.getItem("fixtureOnboarded") ? "done" : "welcome", mode: "holdDouble", shortcutLabel: "Right ⌥",
        microphone: "request", accessibility: false, accessibilityRequested: false, accessibilityStale: false,
        listenerNeedsRestart: false, inputMonitoring: false,
        listener: false, phase: "idle", message: "", preview: "", languages: [], retention: 7,
        cancelLabel: "Escape", pasteLabel: "⌥⇧V", rawLabel: "", shortcutSide: "right", shortcutTarget: "", choosingShortcut: false,
        outputProfiles: "default | compact", historyRevision: 0, vocabularyEntries: [],
        sound: true, soundSet: "classic", soundVolume: 70, soundPreview: false, soundPreviewRevision: 0, placement: "top",
      }
      window.fixtureLoginState = "disabled"
      window.fixturePendingLoginWrites = []
      const stopSoundPreview = () => {
        state.soundPreview = false
        state.soundPreviewRevision++
        delete state.soundPreviewAnimation
      }
      window.dictationFixture = state
      const today = new Date()
      const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1, 12).getTime() / 1000
      window.historyFixture = [
        { id: "fixture", text: "A practice dictation about history books.", raw: "A practice dictation about history books.",
          app: "TextEdit", time: today.getTime() / 1000, seconds: 4 },
        { id: "next", text: "Send the revised notes to the team.", raw: "Um, send the revised notes to the team.",
          app: "Notes", time: yesterday, seconds: 3.5, mode: "default", inferenceMs: 203, totalMs: 364,
          processing: { latencyMs: 118, model: "Qwen3.5-2B", fallbacks: ["transformations"] } },
        { id: "long", text: "Long transcript. " + "The recording covers several agenda items. ".repeat(100),
          app: "TextEdit", time: yesterday - 100 },
        ...Array.from({ length: 14 }, (_, index) => ({
          id: `older-${index}`, text: `Older dictation ${index + 1}`, app: "Notes", time: yesterday - 200 - index * 60,
        })),
      ]
      const menuCheck = sessionStorage.getItem("fixtureMenuCheck")
      window.updateFixture = menuCheck
        ? { phase: "error", manualCheck: true, openUpdateSettings: menuCheck, message: "Could not verify the update feed." }
        : { phase: "current" }
      window.annotationFixture = localStorage.getItem("managedAnnotationFixture") === "true"
        ? { phase: "idle", busy: false, managed: true, message: "Screen annotation is managed by your organization." }
        : { phase: "idle", busy: false }
      window.meetingsFixture = { phase: "idle", busy: false, detection: false, providerPolicies: [
        { provider: "app:zoom", label: "Zoom", policy: "ask", canAlwaysRecord: true },
        { provider: "site:meet.google.com", label: "Google Meet", policy: "ask", canAlwaysRecord: true },
        { provider: "site:zoom.us", label: "Zoom", scope: "zoom.us", policy: "never", canAlwaysRecord: true },
        { provider: "browser:com.google.chrome", label: "Google Chrome", policy: "never", canAlwaysRecord: false },
      ], calendar: {
        access: "notDetermined", connected: false, reminders: true, lead: 1, calendars: [], upcoming: [],
      } }
      window.meetingsFixture.managed = localStorage.getItem("managedMeetingsFixture") === "true"
      window.calendarRequests = 0
      window.zero = { invoke: async (command, input) => {
        if (command === "jard.interfaceLanguage") {
          if (input.action === "set") localStorage.setItem("fixtureInterfaceLanguage", input.selected)
          const selected = localStorage.getItem("fixtureInterfaceLanguage") ?? "system"
          const resolved = selected === "system" ? "en" : selected
          return { selected, resolved, choices: interfaceCatalog.choices, messages: interfaceCatalog.messages[resolved] ?? {} }
        }
        if (command === "jard.export") { window.lastExport = input; return { saved: true } }
        if (command === "jard.copyAnnotation") { window.copiedAnnotation = input; return { includedImage: true } }
        if (command === "jard.pasteAnnotation") { window.pastedAnnotation = input; return { destination: "Fixture editor", includedSheet: true } }
        if (command === "jard.annotationDestinations") {
          const sessions = [{ id: "ses_one", title: "Fixture release", directory: "/local/releases" },
                            { id: "ses_two", title: "Project notes", directory: "/local/notes" }]
          const rule = window.fixtureAnnotationRule
          return {
            service: "fixture-service",
            sessions: sessions.filter(session => session.title.toLowerCase().includes(input.query)),
            scopes: [{ kind: "site", key: "example.test", label: "example.test" }, { kind: "app", key: "com.fixture.browser", label: "Jard" }],
            rule, suggestion: rule ? { sessionId: rule.target, reason: "Saved for example.test" }
              : { sessionId: "ses_one", reason: "Mentioned in the annotation" },
          }
        }
        if (command === "jard.rememberAnnotationDestination") {
          window.rememberedAnnotation = input
          window.fixtureAnnotationRule = { id: "rule-fixture", scope: { kind: input.scope, label: "example.test" },
            title: "Project notes", target: input.target, available: true, automatic: input.automatic }
          return window.fixtureAnnotationRule
        }
        if (command === "jard.forgetAnnotationDestination") {
          window.forgottenAnnotation = input
          window.fixtureAnnotationRule = null
          return { forgotten: true }
        }
        if (command === "jard.annotationRoutingPolicy") {
          return { managed: localStorage.getItem("managedRoutingFixture") === "true" }
        }
        if (command === "jard.annotationRoute") {
          window.lastRouteControl = input
          if (input.action === "cancel") return { capture: input.id, phase: "cancelled", message: "Automatic send cancelled.", title: "Project notes" }
          if (localStorage.getItem("managedRoutingFixture") === "true") return {
            capture: input.id, phase: "waiting", title: "Project notes", message: "Managed automatic send."
          }
          return { capture: input.id, phase: "idle" }
        }
        if (command === "jard.annotationDeliveries") {
          const rows = JSON.parse(localStorage.getItem("annotationRoutingFixture") || "[]")
          return { deliveries: rows.filter(item => item.annotation === input.id) }
        }
        if (command === "jard.sendAnnotation") {
          window.sentAnnotation = input
          const value = { annotation: input.id, delivery: "queued", status: "queued", deliveryId: "fixture-delivery",
            session: { id: "ses_two", title: "Project notes" }, canUndo: true, createdAt: Date.now(), undoUntil: Date.now() + 600000 }
          const delivered = { ...value, deliveryId: "fixture-received", status: "received", canUndo: false, canCorrect: true }
          const edited = { ...value, deliveryId: "fixture-edited", session: { id: "ses_edit", title: "Project edits" },
            status: "rollback-files", canCorrect: true }
          localStorage.setItem("annotationRoutingFixture", JSON.stringify([value, delivered, edited]))
          return value
        }
        if (command === "jard.undoAnnotationDelivery") {
          window.withdrawnAnnotation = input
          const rows = JSON.parse(localStorage.getItem("annotationRoutingFixture"))
          const found = rows.find(row => row.deliveryId === input.deliveryId)
          const result = { ...found, status: input.restoreFiles ? "reverted" : "withdrawn", canUndo: false, canCorrect: false }
          localStorage.setItem("annotationRoutingFixture", JSON.stringify(rows.map(row => row === found ? result : row)))
          return result
        }
        if (command === "jard.correctAnnotationDelivery") {
          window.correctedAnnotation = input
          const rows = JSON.parse(localStorage.getItem("annotationRoutingFixture"))
          const result = { ...rows[1], status: "corrected", canCorrect: false, correctionMessage: "msg_correction" }
          localStorage.setItem("annotationRoutingFixture", JSON.stringify(rows.map(row => row === rows[1] ? result : row)))
          return result
        }
        if (command === "jard.meetingExports") {
          if (input.action === "choose") window.meetingExportFolder = "/Users/fixture/Meeting transcripts"
          if (input.action === "disable") window.meetingExportFolder = null
          if (input.action === "reveal") window.revealedExport = input.id ?? "folder"
          if (input.action === "separateNotes") window.separateMeetingNotes = input.enabled
          if (input.action === "descriptiveNames") window.descriptiveMeetingNames = input.enabled
          return { folder: window.meetingExportFolder ?? null, separateNotes: window.separateMeetingNotes ?? false,
            descriptiveNames: window.descriptiveMeetingNames ?? false }
        }
        if (command === "jard.meetingAudioAccess") {
          window.meetingAudioFixture ??= { authorization: "request", screen: true, canRepair: false, busy: false }
          const access = window.meetingAudioFixture
          if (input.action === "allow") {
            if (access.authorization === "request") {
              window.requestedMeetingAudio = true
              access.busy = true
              await new Promise(resolve => { window.finishMeetingAudio = resolve })
              Object.assign(access, { authorization: "denied", busy: false })
            } else window.openedMeetingAudioSettings = true
          }
          if (input.action === "repair") {
            if (window.refuseMeetingAudioReset) throw new Error("Fixture audio reset failed.")
            Object.assign(access, { authorization: "granted", canRepair: false })
            window.repairedMeetingAudio = true
          }
          return { ...access }
        }
        if (command === "jard.meetings") {
          const meeting = window.meetingsFixture
          if (meeting.managed && input.action !== "status") throw new Error("Meetings are managed by your organization.")
          if (input.action === "detection") meeting.detection = input.enabled
          if (input.action === "indicator") meeting.showIndicator = input.enabled
          if (input.action === "indicatorCorner") meeting.indicatorCorner = input.corner
          if (input.action === "recordingPrivacy") window.recordingPrivacy = meeting.permission
          if (input.action === "retryPermission") Object.assign(meeting, { permission: "", phase: "idle", message: "" })
          if (input.action === "refreshRecovery" && window.pendingMeetingDraft) Object.assign(meeting,
            { phase: "interrupted", recoveryAvailable: true, message: "Meeting interrupted. Recover the recorded audio." })
          if (input.action === "recoverMeeting") Object.assign(meeting,
            { phase: "finished", recoveryAvailable: false, message: "Recovered meeting saved to Sessions." })
          if (input.action === "autoStop") meeting.autoStop = input.enabled
          if (input.action === "calendarConnect") {
            window.calendarRequests++
            if (window.calendarRequests === 1) meeting.calendar.access = "denied"
            else Object.assign(meeting.calendar, { access: "granted", connected: true,
              calendars: [{ id: "work", title: "Work calendar", source: "Fixture account", included: true }],
              upcoming: [{ id: "event", title: "Design sync", start: Date.now() / 1000 + 180,
                end: Date.now() / 1000 + 480, provider: "Zoom", canJoin: true }],
            })
          }
          if (input.action === "calendarSettings") window.openedCalendarSettings = true
          if (input.action === "calendarLead") meeting.calendar.lead = input.minutes
          if (input.action === "calendarReminders") meeting.calendar.reminders = input.enabled
          if (input.action === "calendarInclude") meeting.calendar.calendars[0].included = input.enabled
          if (input.action === "calendarEvent") window.calendarChoice = input.choice
          if (input.action === "calendarDisconnect") Object.assign(meeting.calendar, { connected: false, upcoming: [], calendars: [] })
          if (input.action === "start") Object.assign(meeting, { phase: "recording", busy: true })
          if (input.action === "showLiveTranscript") {
            if (meeting.phase !== "recording") throw new Error("There is no live meeting transcript.")
            window.dispatchEvent(new CustomEvent("native-sdk:jard-window-navigation", {
              detail: { id: "view-live-meeting-fixture", pane: "live-meeting" },
            }))
          }
          if (input.action === "copyNew") meeting.hasNewText = false
          if (input.action === "setPolicy") {
            const provider = meeting.providerPolicies.find(row => row.provider === input.provider)
            if (!provider || (input.policy === "auto" && !provider.canAlwaysRecord)) throw new Error("Choose an available recording preference for this provider.")
            provider.policy = input.policy
          }
          if (input.action === "stop") Object.assign(meeting, { phase: "finished", busy: false, message: "Meeting saved to Sessions." })
          return { ...meeting }
        }
        if (command === "jard.annotation") {
          if (input.action === "start" && window.annotationFixture.managed) {
            throw new Error("Screen annotation is managed by your organization.")
          }
          const sourcePrompts = { display: "Choose the display.", window: "Choose the window.", area: "Choose the display that contains the area.", phone: "Opening the wired screen mirror…" }
          if (input.action === "start") window.annotationFixture = { phase: input.source === "phone" ? "starting" : "picking", busy: true,
            source: input.source, message: sourcePrompts[input.source] }
          if (input.action === "stop" || input.action === "cancel") window.annotationFixture = {
            phase: input.action === "cancel" ? "cancelled" : "finished", busy: false,
          }
          return window.annotationFixture
        }
        if (command === "jard.windowNavigation") {
          const snapshot = sessionStorage.getItem("fixtureWindowNavigation")
          return snapshot ? { navigation: JSON.parse(snapshot) } : {}
        }
        if (command === "jard.update") {
          if (input.action === "check") {
            window.updateChecks = (window.updateChecks ?? 0) + 1
            if (window.delayUpdateCheck) await new Promise(resolve => { window.finishUpdateCheck = resolve })
            window.updateFixture = { ...window.updateFixture, phase: "checking", message: "" }
            window.dispatchEvent(new CustomEvent("native-sdk:jard-update", { detail: window.updateFixture }))
          }
          if (input.action === "download") window.updateDownloads = (window.updateDownloads ?? 0) + 1
          if (input.action === "restart") throw new Error("Finish your recording before restarting Jard.")
          if (input.action === "releaseNotes") window.requestedReleaseNotes = input
          if (input.action === "channel" || input.action === "confirmSwitch") {
            window.lastUpdateAction = input
            window.updateFixture = input.action === "channel"
              ? { ...window.updateFixture, phase: "switch", channel: input.channel, version: "0.11.0", offer: "a".repeat(64) }
              : { ...window.updateFixture, phase: "ready", staged: true }
            window.dispatchEvent(new CustomEvent("native-sdk:jard-update", { detail: window.updateFixture }))
          }
          return window.updateFixture
        }
        if (command === "jard.dockIcon") {
          const enabled = input.action === "set" ? input.enabled : localStorage.getItem("fixtureDockIcon") !== "off"
          if (input.action === "set") localStorage.setItem("fixtureDockIcon", enabled ? "on" : "off")
          return { enabled, menuBarAvailable: !enabled }
        }
        if (command === "jard.loginItem") {
          if (input.action === "status" && window.fixtureHoldLoginRead)
            await new Promise(resolve => { window.fixtureReleaseLoginRead = resolve })
          if (input.action === "openSettings") window.fixtureLoginSettingsOpened = true
          if (input.action === "set") {
            if (window.fixtureHoldLoginWrites) await new Promise(resolve => { window.fixturePendingLoginWrites.push(resolve) })
            if (window.fixtureLoginError) return { state: window.fixtureLoginState, message: window.fixtureLoginError }
            window.fixtureLoginState = input.enabled ? "enabled" : "disabled"
          }
          return { state: window.fixtureLoginState }
        }
        if (command === "jard.devices") return {
          microphone: "Fixture microphone",
          dictationMicrophones: ["Fixture headset", "Fixture USB microphone"],
        }
        if (command !== "jard.systemDictation") return {}
        switch (input.action) {
          case "describeOutputProfiles": {
            if (input.profiles === "default | compact") return { profiles: [{ target: "default", steps: ["compact"], instruction: "", vocabulary: [], deadline: 60, corrections: "" }] }
            const profiles = JSON.parse(input.profiles)
            if (profiles.some(row => row.target === "app:")) throw new Error("Invalid output profile. Enter an application.")
            if (profiles.some(row => row.corrections === "Missing replacement syntax")) throw new Error("Invalid profile replacement on line 1.")
            return { profiles: profiles.map(row => ({ instruction: "", vocabulary: [], deadline: 30, corrections: "", ...row })) }
          }
          case "previewOutput": {
            window.fixtureOutputPreview = input
            const row = JSON.parse(input.profiles).find(item => item.target === "site:https://example.com/settings")
            return { profile: row.target, steps: row.steps, vocabulary: row.vocabulary, text: "Scheduled 12 books",
              warnings: ["The local rewrite exceeded its 7-second deadline."] }
          }
          case "tryOutput": {
            window.fixtureTryOutput = input
            return { profile: "default", steps: ["compact"], vocabulary: [], text: "Sample with compact spaces",
              warnings: [], skipped: ["rewrite", "script"] }
          }
          case "copyOutputAgentPrompt": window.fixtureCopiedOutputInstructions = true; break
          case "revealOutputSettings": window.fixtureRevealedOutputSettings = true; break
          case "chooseOutputScript":
            state.outputScriptName = window.fixtureChoosesEmbedded
              ? "jaro.config.ts"
              : window.fixtureChoosesCatalog ? "fixture-output.json" : "fixture-output.sh"
            window.scriptVersion = "aaaaaaaaaaaa"
            window.scriptApproved = ""
            window.scriptGrants = {}
            break
          case "createOutputConfig":
            state.outputScriptName = "jaro.config.ts"
            window.fixtureDefaultConfig = true
            window.fixtureStarterSelected = true
            window.scriptVersion = "eeeeeeeeeeee"
            window.scriptApproved = ""
            break
          case "inspectOutputScript": {
            if (window.fixtureTransformPolicy === "disabled") return {
              state: "unavailable", policy: "disabled", message: "Your organization has turned off transformations.",
            }
            if (!state.outputScriptName) return window.fixtureDefaultConfig
              ? { state: "found", file: "jaro.config.ts" } : { state: "none" }
            if (window.fixtureInvalidConfig) return {
              state: "unavailable", message: "label.ts:1:20: Expected an expression", stale: true,
              transforms: [{ id: "add-label", name: "Add label", description: "Add a transcript label" }],
            }
            if (window.fixtureStarterSelected) return {
              state: "review", kind: "embedded", version: window.scriptVersion, token: window.scriptVersion,
              transforms: [{ id: "trim-lines", name: "Trim lines", description: "" }],
            }
            const version = window.scriptVersion
            return { state: version === window.scriptApproved ? "approved" : "review", version, token: version,
              policy: window.fixtureTransformPolicy,
              kind: window.fixtureChoosesEmbedded ? "embedded" : "executable",
              permissions: window.fixtureChoosesEmbedded ? { network: true, programs: true, pageContext: true, commands: ["cat"], hosts: ["example.test"], partial: true } : {},
              grants: version === window.scriptApproved ? window.scriptGrants : {},
              transforms: window.fixtureChoosesCatalog ? [{ id: "add-label", name: "Add label", description: "Add a transcript label" }] : [] }
          }
          case "approveOutputScript":
            if (input.token !== window.scriptVersion) throw new Error("The selected script changed. Review its current version before approving it.")
            window.scriptApproved = input.token
            window.scriptGrants = input.grants ?? {}
            return { state: "approved", version: input.token, token: input.token, kind: window.fixtureChoosesEmbedded ? "embedded" : "executable",
              policy: window.fixtureTransformPolicy,
              permissions: window.fixtureChoosesEmbedded ? { network: true, programs: true, pageContext: true, commands: ["cat"], hosts: ["example.test"], partial: true } : {},
              grants: window.scriptGrants,
              transforms: window.fixtureChoosesCatalog ? [{ id: "add-label", name: "Add label", description: "Add a transcript label" }] : [] }
          case "revealOutputScript": window.fixtureRevealedOutputScript = true; break
          case "clearOutputScript":
            state.outputScriptName = ""
            window.fixtureStarterSelected = false
            window.scriptApproved = ""
            break
          case "history": {
            if (input.query === "other words") await new Promise(resolve => setTimeout(resolve, 400))
            const entries = window.historyFixture.filter(item => item.text.includes(input.query))
            return { entries: entries.slice(input.offset, input.offset + 15), total: entries.length, words: 7, seconds: 4 }
          }
          case "bulkHistory":
            if (input.operation === "delete") {
              window.deletedHistory = input.ids
              window.historyFixture = window.historyFixture.filter(item => !input.ids.includes(item.id))
            } else {
              if (window.failHistoryCopy) throw new Error("Could not update the clipboard.")
              window.copiedHistory = input.ids
            }
            break
          case "clearHistory": window.historyFixture = []; break
          case "microphone": state.microphone = "granted"; break
          case "accessibility": state.accessibilityRequested = true; break
          case "repairAccessibility":
            window.accessibilityRepairRequested = true
            state.accessibilityStale = false
            break
          case "reopen":
            throw new Error("Finish your recording or dictation before closing Jard.")
          case "requestMediaAccess":
            window.playbackAccessRequests = (window.playbackAccessRequests || 0) + 1
            state.mediaAccessMessage = "Allowed for Music. Check Automation in System Settings for VLC."
            break
          case "openMediaAutomationSettings": window.openedAutomationSettings = true; break
          case "requestBrowserAccess":
            window.browserAccessRequests = (window.browserAccessRequests || 0) + 1
            state.browserAccessMessage = "Allowed for Fixture Browser."
            break
          case "openBrowserAutomationSettings": window.openedBrowserAutomationSettings = true; break
          case "chooseShortcut": state.shortcutTarget = input.target || "shortcut"; state.choosingShortcut = true; break
          case "cancelShortcut": state.shortcutTarget = ""; state.choosingShortcut = false; break
          case "setShortcutSide":
            state.shortcutSide = input.value
            state.shortcutLabel = { left: "Left ⌥", either: "⌥", right: "Right ⌥" }[input.value]
            break
          case "resetCancelShortcut": state.cancelLabel = "Escape"; break
          case "resetPasteShortcut":
            if (state.rawLabel === "⌥⇧V") {
              state.shortcutTarget = "paste"
              state.choosingShortcut = true
              state.message = "That shortcut is already in use by Jard."
            } else {
              state.pasteLabel = "⌥⇧V"
              state.shortcutTarget = ""
              state.choosingShortcut = false
              state.message = ""
            }
            break
          case "editVocabulary": {
            const index = rows => new Map(rows.map(row => [row.term.toLowerCase(), row]))
            const base = index(input.base), next = index(input.entries), saved = index(state.vocabularyEntries)
            for (const [key, row] of base) {
              const wanted = next.get(key), current = saved.get(key)
              if (!current) continue
              if (!wanted) saved.delete(key)
              else saved.set(key, {
                term: wanted.term === row.term ? current.term : wanted.term,
                soundsLike: JSON.stringify(wanted.soundsLike) === JSON.stringify(row.soundsLike) ? current.soundsLike : wanted.soundsLike,
              })
            }
            for (const [key, row] of next) if (!base.has(key)) saved.set(key, row)
            state.vocabularyEntries = [...saved.values()]
            state.vocabulary = state.vocabularyEntries.map(row => row.term).join("\n")
            state.preferencesRevision = (state.preferencesRevision || 0) + 1
            break
          }
          case "set":
            if (input.key === "soundSet" && window.fixtureDelaySoundSelection) {
              await new Promise(resolve => { window.fixtureFinishSoundSelection = resolve })
              window.fixtureDelaySoundSelection = false
            }
            state[input.key] = input.value
            if (input.key === "onboarding" && input.value === "access") state.enabled = true
            if (["sound", "soundSet", "soundVolume"].includes(input.key)) stopSoundPreview()
            break
          case "previewSound":
            window.fixturePreviewCue = input.cue
            window.fixturePreviewStyle = input.style
            state.soundPreview = true
            state.soundPreviewAnimation = { cue: input.cue, style: input.style ?? state.soundSet, startedAt: Date.now(), id: ++state.soundPreviewRevision }
            break
          case "stopSoundPreview":
            stopSoundPreview()
            break
        }
        return { ...state }
      } }
    }, interfaceCatalog)
    const button = name => page.getByRole("button", { name, exact: true })
    const settingsTab = name => page.getByRole("tablist", { name: "Settings", exact: true }).getByRole("tab", { name, exact: true })
    const heading = name => page.getByRole("heading", { name, exact: true })
    await page.goto("http://127.0.0.1:18997/?jard-launch=fixture")
    assert.equal(new URL(page.url()).searchParams.has("jard-launch"), false)
    await heading("Speak where you write.").waitFor()
    await button("Set up later").click()
    await page.evaluate(() => { window.fixtureHoldLoginRead = true })
    await button("Settings").click()
    await settingsTab("Dictation").click()
    await heading("System Dictation").waitFor()
    const loginSwitch = page.getByRole("switch", { name: "Launch at login" })
    await page.getByText("Checking…", { exact: true }).waitFor()
    assert.equal(await loginSwitch.count(), 0)
    await page.waitForFunction(() => !!window.fixtureReleaseLoginRead)
    await page.evaluate(() => {
      window.fixtureHoldLoginRead = false
      window.fixtureReleaseLoginRead()
      window.fixtureHoldLoginWrites = true
    })
    await loginSwitch.click()
    assert(await loginSwitch.isChecked())
    await loginSwitch.click()
    assert(!await loginSwitch.isChecked())
    await page.waitForFunction(() => window.fixturePendingLoginWrites.length === 2)
    await page.evaluate(() => window.fixturePendingLoginWrites[0]())
    await page.waitForFunction(() => window.fixtureLoginState === "enabled")
    assert(!await loginSwitch.isChecked())
    await page.evaluate(() => {
      window.fixtureHoldLoginWrites = false
      window.fixturePendingLoginWrites[1]()
    })
    await page.waitForFunction(() => window.fixtureLoginState === "disabled")
    await page.evaluate(() => { window.fixtureLoginError = "Fixture refused registration." })
    await loginSwitch.click()
    await page.getByRole("alert").getByText("Fixture refused registration.").waitFor()
    assert(!await loginSwitch.isChecked())
    await page.evaluate(() => {
      window.fixtureLoginError = ""
      window.fixtureLoginState = "approval"
      window.dispatchEvent(new Event("focus"))
    })
    await button("Open Login Items").click()
    assert.equal(await page.evaluate(() => window.fixtureLoginSettingsOpened), true)
    await page.evaluate(() => {
      window.fixtureLoginState = "disabled"
      window.dispatchEvent(new Event("focus"))
    })
    await loginSwitch.waitFor()
    const preferenceWarning = "Jard used defaults for some invalid dictation settings. Saving will preserve the original values."
    await page.evaluate(warning => { window.dictationFixture.preferencesWarning = warning }, preferenceWarning)
    await page.getByRole("status").getByText(preferenceWarning, { exact: true }).waitFor()
    await page.evaluate(() => { window.dictationFixture.preferencesWarning = "" })
    await page.getByText(preferenceWarning, { exact: true }).waitFor({ state: "hidden" })
    await button("Set up dictation").click()
    await heading("Speak where you write.").waitFor()
    await button("Set up dictation").click()
    await heading("Give Jard access").waitFor()
    assert.equal(await button("Continue").isEnabled(), false)
    await button("Allow microphone").click()
    await button("Open settings").click()
    assert.equal(await button("Reset Accessibility").count(), 0)
    assert.equal(await button("Reopen Jard").count(), 0)
    await page.evaluate(() => { window.dictationFixture.accessibilityStale = true })
    await button("Reset Accessibility").click()
    await button("Reset Accessibility").waitFor({ state: "hidden" })
    assert.equal(await page.evaluate(() => window.accessibilityRepairRequested), true)
    assert.equal(await button("Continue").isEnabled(), false)
    await page.evaluate(() => {
      window.dictationFixture.accessibility = true
      window.dictationFixture.listenerNeedsRestart = true
    })
    await page.getByText("Jaro is retrying the shortcut. If it stays unavailable, reopen the app.", { exact: true }).waitFor()
    await button("Reopen Jard").click()
    await page.getByRole("alert").getByText("Finish your recording or dictation before closing Jard.").waitFor()
    await button("Check Input Monitoring").waitFor()
    await page.evaluate(() => {
      window.dictationFixture.listenerNeedsRestart = false
      window.dictationFixture.listener = true
    })
    await page.getByText("Jaro is retrying the shortcut. If it stays unavailable, reopen the app.", { exact: true }).waitFor({ state: "hidden" })
    await button("Continue").click()
    await heading("Try the shortcut").waitFor()
    const practice = page.getByRole("textbox", { name: "Try saying “Here’s what I want to work on today.”" })
    await page.waitForFunction(() => document.activeElement?.id === "dictation-practice")
    await page.evaluate(() => {
      window.dictationFixture.phase = "listening"
      window.dictationFixture.message = "Listening…"
    })
    await page.getByText("Listening…", { exact: true }).waitFor()
    await page.evaluate(() => {
      window.dictationFixture.phase = "inserted"
      window.dictationFixture.message = "Sent to the active app."
    })
    await page.getByText("Sent to the active app.", { exact: true }).waitFor()
    await practice.dispatchEvent("paste")
    await practice.fill("Here’s what I want to work on today.")
    await page.getByText("Your words landed.", { exact: true }).waitFor()
    await button("Continue").click()
    await heading("Dictation is ready").waitFor()
    await page.getByRole("checkbox", { name: "Launch Jard at login" }).check()
    assert.equal(await page.evaluate(() => window.fixtureLoginState), "enabled")
    await button("Start using Jard").click()
    await heading("Dictation is ready").waitFor({ state: "hidden" })
    assert.equal(await page.evaluate(() => window.dictationFixture.onboarding), "done")
    const navigateWorkspace = (id, pane) => page.evaluate(detail => {
      sessionStorage.setItem("fixtureWindowNavigation", JSON.stringify(detail))
      window.dispatchEvent(new CustomEvent("native-sdk:jard-window-navigation", { detail }))
    }, { id, pane })
    await navigateWorkspace("cli-history", "history")
    await page.getByRole("tab", { name: "History", selected: true }).waitFor()
    await navigateWorkspace("cli-output", "output")
    await page.getByRole("tab", { name: "Output", selected: true }).waitFor()
    await page.getByRole("region", { name: "Every dictation", exact: true }).getByText("Compact whitespace").waitFor()
    await page.getByRole("region", { name: "Local script" }).getByRole("button", { name: "Choose a local script…", exact: true }).waitFor()
    const starterRegion = page.getByRole("region", { name: "Local script" })
    await starterRegion.getByRole("button", { name: "Create starter configuration", exact: true }).waitFor()
    await page.evaluate(() => { window.fixtureDefaultConfig = true })
    await starterRegion.getByRole("button", { name: "Use configuration", exact: true }).click()
    await starterRegion.getByText("Trim lines", { exact: true }).waitFor()
    await starterRegion.getByText("Review this script version", { exact: true }).waitFor()
    await starterRegion.getByRole("button", { name: "Remove script", exact: true }).click()
    await starterRegion.getByRole("button", { name: "Use configuration", exact: true }).waitFor()
    await page.evaluate(() => { window.fixtureDefaultConfig = false })
    await starterRegion.getByRole("button", { name: "Create starter configuration", exact: true }).click()
    await starterRegion.getByText("Trim lines", { exact: true }).waitFor()
    await starterRegion.getByRole("button", { name: "Remove script", exact: true }).click()
    await navigateWorkspace("cli-meetings", "meetings")
    await settingsTab("Meetings").and(page.getByRole("tab", { selected: true })).waitFor()
    await navigateWorkspace("cli-home", "home")
    await heading("Settings").waitFor({ state: "hidden" })
    const sidebarUpdate = page.locator(".sidebar-update")
    const updates = page.getByRole("region", { name: "Updates" })
    const sidebarAction = name => sidebarUpdate.getByRole("button", { name, exact: true })
    const settingsAction = name => updates.getByRole("button", { name, exact: true })
    const publishUpdate = patch => page.evaluate(patch => {
      window.updateFixture = { ...window.updateFixture, ...patch }
      window.dispatchEvent(new CustomEvent("native-sdk:jard-update", { detail: window.updateFixture }))
    }, patch)
    const checkButton = sidebarAction("Look for new versions")
    const settingsBounds = await button("Settings").boundingBox()
    const checkBounds = await checkButton.boundingBox()
    assert.ok(checkBounds.x >= settingsBounds.x + settingsBounds.width)
    assert.ok(Math.abs(checkBounds.y - settingsBounds.y) <= 2)
    assert.equal(await checkButton.getAttribute("title"), "Look for new versions")
    await checkButton.click()
    await sidebarAction("Looking for new versions…").waitFor()
    assert.equal(await sidebarAction("Looking for new versions…").isDisabled(), true)
    await button("Settings").click()
    await settingsTab("General").click()
    await settingsAction("Looking for new versions…").waitFor()
    assert.equal(await settingsAction("Looking for new versions…").isDisabled(), true)
    await publishUpdate({ phase: "current", currentVersion: "0.0.6", channel: "stable", channels: ["stable"] })
    await sidebarUpdate.getByText("Jard is up to date.", { exact: true }).waitFor()
    await updates.getByText("Jard is up to date.", { exact: true }).waitFor()
    const updateChannel = page.getByRole("combobox", { name: "Update channel", exact: true })
    assert.equal(await updateChannel.count(), 0)
    await page.evaluate(() => { window.delayUpdateCheck = true })
    await settingsAction("Look for new versions").click()
    assert.equal(await page.evaluate(() => window.updateChecks), 2)
    await sidebarAction("Looking for new versions…").waitFor()
    await settingsAction("Looking for new versions…").waitFor()
    assert.equal(await updates.getByText("Jard is up to date.", { exact: true }).count(), 0)
    assert.equal(await sidebarUpdate.getByText("Jard is up to date.", { exact: true }).count(), 0)
    await page.evaluate(() => { window.delayUpdateCheck = false; window.finishUpdateCheck() })
    await publishUpdate({ phase: "error", message: "Could not verify the update feed." })
    await sidebarUpdate.getByText("Could not verify the update feed.").waitFor()
    await updates.getByText("Could not verify the update feed.").waitFor()
    await settingsAction("Look for new versions").click()
    await publishUpdate({ phase: "download", version: "0.99.0", message: "Install from Applications." })
    await settingsAction("Download Update").click()
    assert.equal(await page.evaluate(() => window.updateDownloads), 1)
    await publishUpdate({ phase: "checking", staged: true, message: "" })
    await sidebarAction("Restart to Update").waitFor()
    assert.equal(await settingsAction("Restart to Update").isDisabled(), false)
    assert.match(await sidebarAction("Restart to Update").getAttribute("class"), /update-ready/)
    await settingsAction("Restart to Update").click()
    await updates.getByText("Finish your recording before restarting Jard.", { exact: true }).waitFor()
    await sidebarUpdate.getByText("Finish your recording before restarting Jard.", { exact: true }).waitFor()
    await publishUpdate({ phase: "current", staged: false, installedChannel: "stable", channel: "stable", channels: ["stable", "dev"] })
    const findSettings = page.getByRole("combobox", { name: "Find settings" })
    const findTrigger = button("Find settings")
    const focusedSetting = title => page.waitForFunction(name =>
      document.activeElement?.closest("[data-setting-title]")?.dataset.settingTitle === name, title)
    await findTrigger.click()
    await findSettings.fill("Dock icon")
    await page.getByRole("option", { name: /Show Dock icon/ }).click()
    await focusedSetting("Show Dock icon")
    const dockIcon = page.getByRole("switch", { name: "Show Dock icon" })
    assert.equal(await dockIcon.isChecked(), true)
    await dockIcon.uncheck()
    await page.waitForFunction(() => localStorage.getItem("fixtureDockIcon") === "off")
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("native-sdk:jard-dock-icon", {
      detail: { enabled: false, menuBarAvailable: false },
    })))
    await page.getByText("Jard stays in the Dock while the menu bar item is unavailable.").waitFor()
    await dockIcon.check()
    await page.waitForFunction(() => localStorage.getItem("fixtureDockIcon") === "on")
    await heading("Settings").focus()
    await page.keyboard.press("Meta+k")
    await findSettings.fill("Development")
    await page.getByRole("option", { name: /Update channel/ }).click()
    await focusedSetting("Update channel")
    assert.equal(await updateChannel.inputValue(), "stable")
    assert.equal(await page.evaluate(() => window.lastUpdateAction), undefined)
    await updateChannel.selectOption("dev")
    const offer = sidebarAction("Switch to Development 0.11.0")
    await offer.waitFor()
    assert.equal(await sidebarAction("Restart to Update").count(), 0)
    assert.deepEqual(await page.evaluate(() => window.lastUpdateAction), { action: "channel", channel: "dev" })
    await offer.click()
    await settingsAction("Restart to Update").waitFor()
    assert.deepEqual(await page.evaluate(() => window.lastUpdateAction), { action: "confirmSwitch", offer: "a".repeat(64) })
    const notes = "# Changes\n- Improved recording\n- Improved recording\n- Fixed setup\n- Faster search\n- Complete notes only"
    await publishUpdate({ stageChannel: "dev", notes, releaseNotesAvailable: true })
    const preview = sidebarUpdate.getByRole("list", { name: "Release notes" })
    await preview.waitFor()
    assert.deepEqual(await preview.getByRole("listitem").allTextContents(), ["Improved recording", "Fixed setup", "Faster search"])
    const sidebarNotes = sidebarUpdate.locator("details")
    const settingsNotes = updates.locator("details")
    await sidebarNotes.locator("summary").click()
    assert.equal(await sidebarNotes.locator("p").textContent(), notes)
    await sidebarNotes.getByRole("button", { name: "Open release page" }).click()
    assert.deepEqual(await page.evaluate(() => window.requestedReleaseNotes), { action: "releaseNotes", version: "0.11.0", channel: "dev" })
    await publishUpdate({ phase: "checking", staged: true })
    assert.equal(await sidebarNotes.locator("p").isVisible(), true)
    const captureHold = "Finish your current work to restart Jard."
    await publishUpdate({ restartBlocked: true })
    await sidebarUpdate.getByText(captureHold).waitFor()
    await updates.getByText(captureHold).waitFor()
    assert.equal(await sidebarAction("Restart to Update").isDisabled(), true)
    assert.equal(await settingsAction("Restart to Update").isDisabled(), true)
    await publishUpdate({ restartBlocked: false })
    await sidebarUpdate.getByText(captureHold).waitFor({ state: "hidden" })
    assert.equal(await sidebarAction("Restart to Update").isEnabled(), true)
    assert.equal(await settingsAction("Restart to Update").isEnabled(), true)
    await settingsNotes.locator("summary").click()
    assert.equal(await settingsNotes.locator("p").textContent(), notes)
    await publishUpdate({ phase: "ready", version: "0.11.1", notes: "New verified stage", releaseNotesAvailable: false })
    await preview.getByText("New verified stage").waitFor()
    assert.equal(await sidebarNotes.locator("p").isVisible(), false)
    await sidebarNotes.locator("summary").click()
    assert.equal(await sidebarNotes.locator("p").textContent(), "New verified stage")
    assert.equal(await sidebarNotes.getByRole("button", { name: "Open release page" }).count(), 0)
    await publishUpdate({ stageChannel: "stable", channelManaged: true })
    await sidebarNotes.waitFor({ state: "detached" })
    await updates.getByText("Your administrator manages this setting.").waitFor()
    assert.equal(await updateChannel.isDisabled(), true)
    await publishUpdate({ phase: "unavailable", updatesDisabled: true })
    const managed = "Updates managed by your organization"
    await updates.getByText(managed).waitFor()
    await sidebarUpdate.getByText(managed).waitFor()
    assert.equal(await sidebarUpdate.getByRole("button").count(), 0)
    assert.equal(await updates.getByRole("button").count(), 0)
    assert.equal(await updateChannel.count(), 0)
    await publishUpdate({ updatesDisabled: false, staged: false })
    await updates.getByText("Updates are disabled for this installation.").waitFor()
    assert.equal(await sidebarUpdate.count(), 0)
    assert.equal(await settingsAction("Look for new versions").isDisabled(), true)
    assert.equal(await settingsTab("General").getAttribute("aria-selected"), "true")
    assert.equal(await heading("Meetings").isVisible(), false)
    await settingsTab("General").focus()
    await page.keyboard.press("ArrowRight")
    assert.equal(await settingsTab("Dictation").getAttribute("aria-selected"), "true")
    await settingsTab("Annotate").click()
    await button("Choose Display…").click()
    await page.getByText("Choose the display.", { exact: true }).waitFor()
    await button("Cancel").click()
    await button("Choose Display…").waitFor()
    await button("Choose Window…").click()
    await page.getByText("Choose the window.", { exact: true }).waitFor()
    await button("Cancel").click()
    await button("Choose Window…").waitFor()
    await button("Wired iPhone or iPad").click()
    await page.getByText("Opening the wired screen mirror…", { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.annotationFixture.source), "phone")
    await button("Cancel").click()
    await button("Wired iPhone or iPad").waitFor()
    await button("Choose Area…").click()
    await page.getByText("Choose the display that contains the area.", { exact: true }).waitFor()
    await page.evaluate(() => {
      window.annotationFixture = { phase: "selecting", busy: true, message: "Drag to select an area." }
      window.dispatchEvent(new CustomEvent("native-sdk:jard-annotation", { detail: window.annotationFixture }))
    })
    await page.getByText("Drag to select an area.", { exact: true }).waitFor()
    await button("Cancel").click()
    await button("Choose Area…").waitFor()
    await page.evaluate(() => {
      window.annotationFixture = { phase: "recording", busy: true, message: "Recording the selected area." }
      window.dispatchEvent(new CustomEvent("native-sdk:jard-annotation", { detail: window.annotationFixture }))
    })
    await button("Discard…").click()
    await page.getByRole("alertdialog", { name: "Discard this annotation?" }).waitFor()
    await button("Keep recording").click()
    await button("Stop & Save").waitFor()
    assert.equal(await page.evaluate(() => window.annotationFixture.phase), "recording")
    await button("Discard…").click()
    await button("Discard").click()
    await button("Choose Area…").waitFor()
    assert.equal(await page.evaluate(() => window.annotationFixture.phase), "cancelled")
    await findTrigger.click()
    await findSettings.fill("Meeting export folder")
    await page.getByRole("option", { name: /Meeting export folder/ }).click()
    await focusedSetting("Meeting export folder")
    assert.equal(await settingsTab("Meetings").getAttribute("aria-selected"), "true")
    await heading("Meetings").waitFor()
    const meetingAudio = page.locator('[data-setting-title="System Audio Recording"]')
    await meetingAudio.getByText(/Screen Recording is allowed as a fallback/).waitFor()
    assert.equal(await page.evaluate(() => !!window.requestedMeetingAudio), false)
    await meetingAudio.getByRole("button", { name: "Allow Mac audio" }).click()
    await meetingAudio.getByRole("button", { name: "Waiting for macOS…" }).waitFor()
    await page.waitForFunction(() => !!window.finishMeetingAudio)
    await page.evaluate(() => window.finishMeetingAudio())
    await meetingAudio.getByRole("button", { name: "Open System Settings" }).click()
    assert.equal(await page.evaluate(() => window.openedMeetingAudioSettings), true)
    assert.equal(await meetingAudio.getByRole("button", { name: "Reset Mac audio access" }).count(), 0)
    await page.evaluate(() => {
      window.meetingAudioFixture.canRepair = true
      window.refuseMeetingAudioReset = true
      window.dispatchEvent(new Event("focus"))
    })
    await meetingAudio.getByRole("button", { name: "Reset Mac audio access" }).click()
    await meetingAudio.getByRole("alert").getByText("Fixture audio reset failed.").waitFor()
    await page.evaluate(() => { window.refuseMeetingAudioReset = false })
    await meetingAudio.getByRole("button", { name: "Reset Mac audio access" }).click()
    await meetingAudio.getByRole("status").getByText("Allowed", { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.repairedMeetingAudio && window.meetingAudioFixture.screen), true)
    const folderControls = page.locator('[data-setting-title="Meeting export folder"]')
    await folderControls.getByRole("button", { name: "Choose Folder…" }).click()
    await folderControls.getByText("/Users/fixture/Meeting transcripts").waitFor()
    await page.getByRole("switch", { name: /Separate notes file/ }).click()
    await page.locator('[data-setting-title="Separate notes file"] input:checked:enabled').waitFor()
    assert.equal(await page.evaluate(() => window.separateMeetingNotes), true)
    await page.getByRole("switch", { name: /Descriptive meeting filenames/ }).click()
    await page.locator('[data-setting-title="Descriptive meeting filenames"] input:checked:enabled').waitFor()
    await folderControls.getByRole("button", { name: "Reveal Folder" }).click()
    assert.equal(await page.evaluate(() => window.revealedExport), "folder")
    await folderControls.getByRole("button", { name: "Turn Off" }).click()
    await folderControls.getByRole("button", { name: "Choose Folder…" }).waitFor()
    assert.equal(await page.getByRole("switch", { name: /Separate notes file/ }).isChecked(), true)
    assert.equal(await page.getByRole("switch", { name: /Descriptive meeting filenames/ }).isChecked(), true)
    const zoomPolicy = page.getByRole("combobox", { name: "Zoom", exact: true })
    const zoomSitePolicy = page.getByRole("combobox", { name: "Zoom zoom.us", exact: true })
    const chromePolicy = page.getByRole("combobox", { name: "Google Chrome", exact: true })
    assert.equal(await zoomPolicy.inputValue(), "ask")
    assert.equal(await zoomSitePolicy.inputValue(), "never")
    assert.deepEqual(await chromePolicy.locator("option").allTextContents(), ["Ask", "Never offer"])
    await zoomPolicy.selectOption("auto")
    assert.equal(await page.evaluate(() => window.meetingsFixture.providerPolicies[0].policy), "auto")
    assert.equal(await zoomSitePolicy.inputValue(), "never")
    await page.getByRole("combobox", { name: "Google Meet", exact: true }).selectOption("never")
    assert.equal(await page.evaluate(() => window.meetingsFixture.phase), "idle")
    await page.getByRole("switch", { name: "Offer to record detected calls" }).check()
    assert.equal(await page.evaluate(() => window.meetingsFixture.detection), true)
    await page.getByRole("switch", { name: /Stop automatically when a call ends/ }).check()
    assert.equal(await page.evaluate(() => window.meetingsFixture.autoStop), true)
    assert.equal(await page.evaluate(() => window.calendarRequests), 0)
    await button("Connect Calendar").click()
    await button("Open Calendar Settings").click()
    assert.equal(await page.evaluate(() => window.openedCalendarSettings), true)
    await page.evaluate(() => {
      window.meetingsFixture.calendar.access = "granted"
      window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
    })
    await button("Connect Calendar").click()
    await page.getByText("Design sync", { exact: true }).waitFor()
    await page.getByRole("combobox", { name: "Reminder time" }).selectOption("5")
    await page.getByRole("region", { name: "Calendar", exact: true }).getByText("Calendars (1 of 1)").click()
    await page.getByRole("switch", { name: /Work calendar/ }).uncheck()
    assert.equal(await page.evaluate(() => window.meetingsFixture.calendar.calendars[0].included), false)
    await button("Join").click()
    assert.equal(await page.evaluate(() => window.calendarChoice), "join")
    await button("Join & Record").click()
    assert.equal(await page.evaluate(() => window.calendarChoice), "joinRecord")
    await button("Disconnect").click()
    await page.getByText("Design sync", { exact: true }).waitFor({ state: "hidden" })
    await button("Record Meeting").click()
    await button("Stop & Save").waitFor()
    await page.evaluate(() => {
      window.meetingsFixture.title = "Launch planning"
      window.meetingsFixture.microphone = "Call headset"
      window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
    })
    await page.getByText("Launch planning", { exact: true }).waitFor()
    const meetingsSettings = page.getByRole("region", { name: "Meetings", exact: true })
    const recordingSettings = meetingsSettings.getByRole("region", { name: "Recording", exact: true })
    await recordingSettings.getByText("Call headset", { exact: true }).waitFor()
    const microphoneWarning = recordingSettings.getByRole("alert").filter({ hasText: "Microphone silent. Mac audio is still recording." })
    const systemWarning = recordingSettings.getByRole("alert").filter({ hasText: "Mac audio is reconnecting." })
    for (const systemAudioUnavailable of [true, false]) {
      await page.evaluate(unavailable => {
        window.meetingsFixture.systemAudioUnavailable = unavailable
        window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
      }, systemAudioUnavailable)
      await systemWarning.waitFor({ state: systemAudioUnavailable ? "visible" : "hidden" })
      assert.equal(await recordingSettings.getByRole("button", { name: "Stop & Save" }).isEnabled(), true)
    }
    for (const microphoneSilent of [true, false]) {
      await page.evaluate(silent => {
        window.meetingsFixture.microphoneSilent = silent
        window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
      }, microphoneSilent)
      await microphoneWarning.waitFor({ state: microphoneSilent ? "visible" : "hidden" })
    }
    assert.equal(await recordingSettings.getByRole("button", { name: "Stop & Save" }).isEnabled(), true)
    await recordingSettings.locator('[data-setting-title="Meeting transcript"]').getByRole("button", { name: "Copy New" }).waitFor()
    assert.equal(await meetingsSettings.getByRole("region", { name: "Markdown export" }).count(), 1)
    assert.equal(await meetingsSettings.getByRole("region", { name: "After a meeting" }).count(), 1)
    const indicator = page.getByRole("switch", { name: /Show recording indicator/ })
    assert.equal(await indicator.isVisible(), false)
    await settingsTab("General").click()
    await findTrigger.click()
    await findSettings.fill("Indicator corner")
    await page.getByRole("option", { name: /Indicator corner/ }).click()
    await focusedSetting("Indicator corner")
    assert.equal(await settingsTab("Meetings").getAttribute("aria-selected"), "true")
    await page.getByRole("combobox", { name: "Indicator corner" }).selectOption("bottomLeft")
    assert.equal(await page.evaluate(() => window.meetingsFixture.indicatorCorner), "bottomLeft")
    await indicator.uncheck()
    assert.equal(await page.evaluate(() => window.meetingsFixture.showIndicator), false)
    assert.equal(await button("Stop & Save").isEnabled(), true)
    assert.equal(await button("Copy New").isEnabled(), true)
    await page.evaluate(() => {
      Object.assign(window.meetingsFixture, { hasText: true, hasNewText: true })
      window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
    })
    await button("Copy New").click()
    await page.getByText("Copied meeting transcript.").waitFor()
    assert.equal(await button("Copy New").isEnabled(), true)
    await zoomSitePolicy.selectOption("ask")
    assert.equal(await page.evaluate(() => window.meetingsFixture.providerPolicies[2].policy), "ask")
    assert.equal(await zoomPolicy.inputValue(), "auto")
    await button("Stop & Save").click()
    await page.getByText("Meeting saved to Sessions.").waitFor()
    assert.equal(await button("Copy New").isEnabled(), false)
    await page.evaluate(() => {
      Object.assign(window.meetingsFixture, { phase: "permission", permission: "system", message: "Allow Mac audio access." })
      window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
      window.pendingMeetingDraft = true
    })
    const permissionRow = recordingSettings.locator('[data-setting-title="Recording permission"]')
    await permissionRow.getByRole("button", { name: "Open Screen & System Audio Settings" }).waitFor()
    const permissionViewport = page.viewportSize()
    await page.setViewportSize({ width: 430, height: 760 })
    assert.ok(await permissionRow.evaluate(row => row.scrollWidth <= row.clientWidth + 1))
    await page.setViewportSize(permissionViewport)
    await permissionRow.getByRole("button", { name: "Open Screen & System Audio Settings" }).click()
    assert.equal(await page.evaluate(() => window.recordingPrivacy), "system")
    await button("Try Again").click()
    await button("Check for interrupted meetings").click()
    await recordingSettings.locator('[data-setting-title="Interrupted meeting"]').getByRole("button", { name: "Recover Meeting" }).click()
    await page.getByText("Recovered meeting saved to Sessions.").waitFor()
    const keepAudio = page.getByRole("combobox", { name: /Keep meeting audio/ })
    await keepAudio.selectOption("7")
    await button("Set retention").click()
    await page.waitForFunction(() => [...document.querySelectorAll("select")].some(node =>
      node.value === "7" && node.parentElement?.textContent?.includes("Keep meeting audio")))
    const audioPolicyChanged = page.waitForResponse(response => response.url().endsWith("/api/meetings/audio-retention?days=-1"))
    await keepAudio.selectOption({ label: "Forever" })
    await audioPolicyChanged
    assert.equal(retention, -1)
    const keepMeetings = page.getByRole("combobox", { name: /Keep meetings/ })
    assert.equal(await keepMeetings.inputValue(), "default")
    await keepMeetings.selectOption("7")
    await page.getByRole("alertdialog", { name: "Set meeting retention?" }).getByText(/will move to the macOS Trash/).waitFor()
    await button("Cancel").click()
    assert.equal(wholeRetention, null)
    await keepMeetings.selectOption("7")
    await button("Set retention").click()
    await page.waitForFunction(() => [...document.querySelectorAll("select")].some(node =>
      node.value === "7" && node.parentElement?.textContent?.includes("Keep meetings")))
    assert.equal(wholeRetention, 7)
    assert.equal(await keepAudio.inputValue(), "-1")
    assert.equal(retention, -1)
    const automaticTitles = page.getByRole("switch", { name: /Automatic titles and summaries/ })
    await automaticTitles.waitFor()
    assert.equal(await automaticTitles.isChecked(), true)
    await automaticTitles.click()
    await page.locator('[data-setting-title="Automatic titles and summaries"] input:not(:checked):enabled').waitFor()
    assert.equal(automaticNotes, false)
    const workspaceSize = page.viewportSize()
    await page.setViewportSize({ width: 430, height: 760 })
    assert.equal(await meetingsSettings.evaluate(node => [...node.querySelectorAll(".dictation-preference-group")]
      .every(card => card.scrollWidth <= card.clientWidth + 1)), true)
    await page.setViewportSize(workspaceSize)
    await button("Dictation").click()
    await heading("Dictation").waitFor()
    await button("Settings").click()
    await page.locator('[data-setting-title="Automatic titles and summaries"] input:not(:checked):enabled').waitFor()
    await page.setViewportSize({ width: 1100, height: 420 })
    await button("Dictation").click()
    await heading("Dictation").waitFor()
    const dictationHeadingX = (await heading("Dictation").boundingBox()).x
    assert.equal(await page.getByRole("tab", { name: "General" }).getAttribute("aria-selected"), "true")
    await heading("Dictation").focus()
    await page.keyboard.press("Meta+f")
    assert.equal(await findSettings.getAttribute("aria-expanded"), "false")
    await findSettings.fill("dictation")
    const settingsResults = page.getByRole("listbox", { name: "Matching settings" }).locator("..")
    await settingsResults.evaluate(node => { node.scrollTop = node.scrollHeight })
    assert.ok(await settingsResults.evaluate(node => node.scrollTop > 0))
    await findSettings.fill("advanced")
    await page.waitForFunction(() => document.querySelector(".session-search-results")?.scrollTop === 0)
    await findSettings.fill("shortcut")
    assert.match(await page.getByRole("option").first().textContent(), /^ShortcutDictation/)
    await findSettings.press("ArrowDown")
    await findSettings.press("Enter")
    await focusedSetting("Shortcut mode")
    assert.equal(await page.evaluate(() => window.dictationFixture.mode), "holdDouble")
    await heading("Dictation").focus()
    await page.keyboard.press("Meta+k")
    await findSettings.fill("Cancel shortcut")
    await page.getByText("No matching settings.").waitFor()
    await findSettings.press("Escape")
    await page.waitForFunction(() => document.activeElement?.textContent === "Dictation" && document.activeElement.tagName === "H1")
    await findTrigger.focus()
    await findTrigger.click()
    await findSettings.waitFor()
    await page.locator(".session-search-overlay").click({ position: { x: 6, y: 6 } })
    await page.waitForFunction(() => document.activeElement?.textContent === "Find settings")
    assert.equal(await page.getByText("Last dictation failed", { exact: true }).count(), 0)
    await page.evaluate(() => {
      Object.assign(window.dictationFixture, { lastFailure: "Reconnect the selected microphone.", phase: "processing" })
    })
    await page.getByRole("status").getByText("Last dictation failed", { exact: true }).waitFor()
    await page.getByText("Reconnect the selected microphone.", { exact: true }).waitFor()
    await page.evaluate(() => {
      window.dictationFixture.warmInput = { phase: "unavailable", message: "The fixture microphone could not open." }
    })
    await page.getByRole("status").getByText("Microphone unavailable", { exact: true }).waitFor()
    await page.getByText("The fixture microphone could not open.", { exact: true }).waitFor()
    assert.equal(await page.getByText("Last dictation failed", { exact: true }).count(), 0)
    await page.evaluate(() => {
      window.dictationFixture.warmInput = { phase: "fallback", microphone: "Fixture USB microphone" }
    })
    await page.getByText("Using Fixture USB microphone for dictation.", { exact: true }).waitFor()
    await page.getByText("Reconnect the selected microphone.", { exact: true }).waitFor()
    await page.evaluate(() => {
      delete window.dictationFixture.warmInput
      Object.assign(window.dictationFixture, { lastFailure: "", phase: "inserted" })
    })
    await page.getByText("Using Fixture USB microphone for dictation.", { exact: true }).waitFor({ state: "hidden" })
    await page.getByText("Last dictation failed", { exact: true }).waitFor({ state: "hidden" })
    const shortcutMode = page.getByRole("region", { name: "Shortcuts" }).getByRole("combobox", { name: "Shortcut mode" })
    await shortcutMode.waitFor()
    const shortcutSide = page.getByRole("combobox", { name: "Shortcut side" })
    assert.equal(await shortcutSide.inputValue(), "right")
    await shortcutSide.selectOption("either")
    await button("Change dictation shortcut (⌥)").waitFor()
    await shortcutSide.selectOption("left")
    await button("Change dictation shortcut (Left ⌥)").waitFor()
    await shortcutSide.selectOption("right")
    await button("Change dictation shortcut (Right ⌥)").waitFor()
    await page.evaluate(() => { window.dictationFixture.shortcutSide = "" })
    await shortcutSide.waitFor({ state: "hidden" })
    assert.equal(await button("Change Cancel shortcut (Escape)").count(), 0)
    await shortcutMode.selectOption("tap")
    await button("Change Cancel shortcut (Escape)").click()
    await button("Stop recording Cancel shortcut").waitFor()
    assert.equal(await page.evaluate(() => window.dictationFixture.shortcutTarget), "cancel")
    await page.keyboard.press("Meta+k")
    assert.equal(await findSettings.count(), 0)
    await button("Stop recording Cancel shortcut").click()
    await page.evaluate(() => { window.dictationFixture.cancelLabel = "⌃K" })
    await button("Change Cancel shortcut (⌃K)").waitFor()
    await button("Reset to Escape").click()
    await button("Change Cancel shortcut (Escape)").waitFor()
    await shortcutMode.selectOption("holdDouble")
    await button("Change Cancel shortcut (Escape)").waitFor({ state: "hidden" })
    await page.getByText("Other shortcuts", { exact: true }).click()
    await button("Change Dictate raw text shortcut (Off)").click()
    await page.getByText("Press Delete to turn off.", { exact: true }).waitFor()
    await button("Stop recording Dictate raw text shortcut").click()
    await button("Change Paste Last shortcut (⌥⇧V)").click()
    await page.getByText("Press Delete to turn off.", { exact: true }).waitFor()
    await page.evaluate(() => Object.assign(window.dictationFixture, { pasteLabel: "", shortcutTarget: "", choosingShortcut: false }))
    await button("Change Paste Last shortcut (Off)").waitFor()
    await page.getByText("Default shortcut is ⌥⇧V.", { exact: true }).waitFor()
    assert.equal(await page.getByText("Paste Last uses ⌥⇧V.", { exact: true }).count(), 0)
    await page.evaluate(() => { window.dictationFixture.rawLabel = "⌥⇧V" })
    await button("Reset Paste Last").click()
    await button("Stop recording Paste Last shortcut").waitFor()
    await page.getByText("That shortcut is already in use by Jard.", { exact: true }).waitFor()
    await page.evaluate(() => { window.dictationFixture.rawLabel = "" })
    await button("Reset Paste Last").click()
    await button("Change Paste Last shortcut (⌥⇧V)").waitFor()
    await page.getByText("Paste Last uses ⌥⇧V.", { exact: true }).waitFor()
    await page.getByRole("region", { name: "Voice input" }).getByRole("combobox", { name: "Language" }).waitFor()
    const keepMicrophone = page.getByRole("switch", { name: /Keep microphone ready/ })
    const micRowColor = () => keepMicrophone.evaluate(node => getComputedStyle(node.closest(".dictation-setting-row")).backgroundColor)
    const unfocusedRow = await micRowColor()
    await keepMicrophone.press("Space")
    assert.equal(await page.evaluate(() => window.dictationFixture.keepMicrophoneReady), true)
    assert.notEqual(await micRowColor(), unfocusedRow)
    const feedback = page.getByRole("region", { name: "Advanced" }).getByText("Audio and feedback", { exact: true })
    await feedback.click()
    const microphone = page.getByRole("region", { name: "Voice input" }).getByRole("combobox", { name: "Microphone" })
    const bluetooth = page.getByRole("switch", { name: "Prefer the Mac’s mic over Bluetooth" })
    await microphone.getByText("System default · Fixture headset", { exact: true }).waitFor({ state: "attached" })
    await bluetooth.check()
    await microphone.getByText("System default · Fixture USB microphone", { exact: true }).waitFor({ state: "attached" })
    await bluetooth.uncheck()
    const soundToggle = page.getByRole("switch", { name: "Play dictation sounds" })
    await soundToggle.waitFor()
    const soundStyle = button("Sound style")
    const soundDialog = page.getByRole("dialog", { name: "Sound style" })
    const previewSoundStyle = style => button(`Preview · ${style}`)
    const stopPreview = style => button(`Stop preview · ${style}`)
    assert.equal(await soundStyle.textContent(), "Classic")
    await soundStyle.click()
    await soundDialog.waitFor()
    const cue = name => soundDialog.getByRole("group", { name: "Sound preview" }).getByRole("button", { name, exact: true })
    assert.deepEqual(await soundDialog.locator("button[aria-pressed]").allTextContents(),
      ["Classic", "Signature", "Click", "Soft tone", "Wood", "Warm synth", "Thock", "Soft", "Glass"])
    assert(await cue("Done").isDisabled())
    await previewSoundStyle("Signature").click()
    await stopPreview("Signature").waitFor()
    assert.equal(await page.evaluate(() => window.fixturePreviewCue), "sample")
    assert.equal(await page.evaluate(() => window.fixturePreviewStyle), "signature")
    assert.equal(await button("Classic").getAttribute("aria-pressed"), "true")
    await cue("Done").click()
    assert.equal(await page.evaluate(() => window.fixturePreviewCue), "done")
    await cue("Start").click()
    assert.equal(await page.evaluate(() => window.fixturePreviewCue), "start")
    await page.keyboard.press("Escape")
    await soundDialog.waitFor({ state: "hidden" })
    await page.waitForFunction(() => !window.dictationFixture.soundPreview)
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Sound style")
    await soundStyle.click()
    await button("Thock").click()
    await stopPreview("Thock").waitFor()
    assert.equal(await page.evaluate(() => window.dictationFixture.soundSet), "thock")
    assert(await cue("Done").isDisabled())
    await button("Close").click()
    await page.waitForFunction(() => !window.dictationFixture.soundPreview)
    await soundToggle.click()
    await soundStyle.click()
    await previewSoundStyle("Signature").click()
    await stopPreview("Signature").waitFor()
    assert.equal(await page.evaluate(() => window.dictationFixture.sound), false)
    await page.mouse.click(8, 8)
    await soundDialog.waitFor({ state: "hidden" })
    await page.waitForFunction(() => !window.dictationFixture.soundPreview)
    await page.evaluate(() => { window.fixtureDelaySoundSelection = true })
    await soundStyle.click()
    await button("Wood").click()
    await page.waitForFunction(() => !!window.fixtureFinishSoundSelection)
    await page.keyboard.press("Escape")
    await soundDialog.waitFor({ state: "hidden" })
    await soundStyle.click()
    await page.evaluate(() => window.fixtureFinishSoundSelection())
    await page.waitForFunction(() => window.dictationFixture.soundSet === "wood")
    await soundDialog.getByRole("button", { name: "Wood", exact: true, pressed: true }).waitFor()
    assert.equal(await page.evaluate(() => window.dictationFixture.soundPreview), false)
    await page.keyboard.press("Escape")
    await soundDialog.waitFor({ state: "hidden" })
    await soundToggle.click()
    await page.emulateMedia({ reducedMotion: "no-preference" })
    const soundIndicator = page.locator(".dictation-sound-preview")
    const hasInk = () => page.waitForFunction(() => {
      const canvas = document.querySelector(".dictation-sound-preview canvas")
      return canvas?.getContext("2d").getImageData(142, 50, 1, 1).data[3] > 32
    })
    await soundStyle.click()
    await soundDialog.locator(".dictation-sound-stage").waitFor()
    await button("Classic").click()
    await page.locator('.dictation-sound-preview[data-phase="start"]').waitFor()
    await hasInk()
    await page.locator('.dictation-sound-preview[data-phase="stop"]').waitFor()
    await page.locator('.dictation-sound-preview[data-phase="done"]').waitFor()
    const stalePreview = await page.evaluate(() => ({ ...window.dictationFixture }))
    await stopPreview("Classic").click()
    await soundIndicator.waitFor({ state: "detached" })
    await page.evaluate(stale => {
      window.dispatchEvent(new CustomEvent("native-sdk:jard-dictation-preferences", { detail: stale }))
    }, stalePreview)
    assert.equal(await soundIndicator.count(), 0)
    await page.emulateMedia({ reducedMotion: "reduce" })
    await cue("Start").click()
    await hasInk()
    const previewCanvas = soundIndicator.locator("canvas")
    const still = await previewCanvas.evaluate(canvas => canvas.toDataURL())
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 90)))
    assert.equal(await previewCanvas.evaluate(canvas => canvas.toDataURL()), still)
    await stopPreview("Classic").click()
    await soundIndicator.waitFor({ state: "detached" })
    await page.emulateMedia({ reducedMotion: "no-preference" })
    await cue("Error").click()
    await page.locator('.dictation-sound-preview[data-phase="error"]').waitFor()
    await page.waitForFunction(() => {
      const canvas = document.querySelector(".dictation-sound-preview canvas")
      if (!canvas) return false
      const data = canvas.getContext("2d").getImageData(40, 10, 204, 80).data
      return data.some((red, index) => index % 4 === 0 && red > 110 && red > 2 * data[index + 1] && data[index + 3] > 32)
    })
    await page.keyboard.press("Escape")
    await soundIndicator.waitFor({ state: "detached" })
    await page.waitForFunction(() => !window.dictationFixture.soundPreview)
    const whileDictating = page.getByRole("combobox", { name: "While dictating" })
    const lowerAudio = page.getByRole("spinbutton", { name: /Lower to/ })
    assert.equal(await whileDictating.inputValue(), "none")
    await whileDictating.selectOption("lower")
    assert.equal(await lowerAudio.inputValue(), "20")
    await lowerAudio.fill("35")
    await lowerAudio.press("Enter")
    await page.waitForFunction(() => window.dictationFixture.lowerAudioPercent === 35)
    await lowerAudio.fill("101")
    await lowerAudio.press("Enter")
    assert.equal(await lowerAudio.inputValue(), "35")
    await whileDictating.selectOption("mute")
    await lowerAudio.waitFor({ state: "hidden" })
    assert.equal(await page.evaluate(() => window.dictationFixture.audioWhileDictating), "mute")
    await whileDictating.selectOption("pause")
    await button("Allow playback control").waitFor()
    assert.equal(await page.evaluate(() => window.playbackAccessRequests), undefined)
    await button("Allow playback control").click()
    await page.getByText("Allowed for Music. Check Automation in System Settings for VLC.", { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.playbackAccessRequests), 1)
    await button("Automation settings").click()
    assert.equal(await page.evaluate(() => window.openedAutomationSettings), true)
    await whileDictating.selectOption("none")
    await button("Allow playback control").waitFor({ state: "hidden" })
    await feedback.click()
    const searchTools = button("Search sessions")
    const toggleSidebar = button("Hide sidebar")
    const fixedBounds = await searchTools.boundingBox()
    const toggleBounds = await toggleSidebar.boundingBox()
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight))
    await page.waitForFunction(() => window.scrollY > 0)
    assert.deepEqual(await searchTools.boundingBox(), fixedBounds)
    assert.deepEqual(await toggleSidebar.boundingBox(), toggleBounds)
    await page.getByText("Output and vocabulary corrections", { exact: true }).click()
    assert.equal(await button("Allow browser access").isDisabled(), true)
    assert.equal(await page.evaluate(() => window.browserAccessRequests), undefined)
    await page.evaluate(() => { window.dictationFixture.browserAccessTarget = "Fixture Browser" })
    await button("Allow Fixture Browser").click()
    await page.getByText("Allowed for Fixture Browser.", { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.browserAccessRequests), 1)
    await button("Automation settings").click()
    assert.equal(await page.evaluate(() => window.openedBrowserAutomationSettings), true)
    await page.getByText("Ordered output profiles", { exact: true }).click()
    await page.getByRole("list", { name: "Output steps" }).getByText("Compact whitespace", { exact: true }).waitFor()
    await button("Add profile").click()
    await button("Save profiles").click()
    await page.getByText("Invalid output profile. Enter an application.", { exact: true }).waitFor()
    await page.getByRole("combobox", { name: "Applies to" }).selectOption("site")
    await page.getByRole("textbox", { name: "Website host or URL", exact: true }).fill("https://example.com/settings")
    await page.getByRole("combobox", { name: "Add a step" }).selectOption("rewrite")
    await page.getByRole("combobox", { name: "Add a step" }).selectOption("trim")
    await button("Move Trim whitespace up").click()
    const addOutputStep = page.getByRole("combobox", { name: "Add a step" })
    await addOutputStep.selectOption("lowercase")
    await addOutputStep.selectOption("spongebob-case")
    await button("Remove SpongeBob case").click()
    await button("Remove Lowercase").click()
    await addOutputStep.selectOption("no-trailing-punctuation")
    await page.getByRole("textbox", { name: /Profile vocabulary/ }).fill("Jard\nRumble2 Flash\n")
    const corrections = page.getByRole("textbox", { name: /Profile word replacements/ })
    await corrections.fill("Missing replacement syntax")
    await button("Save profiles").click()
    await page.getByText("Invalid profile replacement on line 1.", { exact: true }).waitFor()
    await corrections.fill("jard audio => Jard Soundfolio\num =>")
    await page.getByRole("textbox", { name: /Profile rewrite instructions/ }).fill("Use scheduled.")
    const deadline = page.getByRole("spinbutton", { name: /Rewrite deadline/ })
    assert.equal(await deadline.inputValue(), "30")
    await deadline.fill("7")
    await page.setViewportSize({ width: 430, height: 730 })
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth)
    await page.setViewportSize({ width: 1100, height: 420 })
    await button("Save profiles").click()
    await page.waitForFunction(() => window.dictationFixture.outputProfiles.startsWith("["))
    assert.equal(await page.evaluate(() => window.fixtureOutputPreview), undefined)
    assert.deepEqual(await page.evaluate(() => JSON.parse(window.dictationFixture.outputProfiles)[1]),
      { target: "site:https://example.com/settings", steps: ["trim", "rewrite", "no-trailing-punctuation"], instruction: "Use scheduled.", vocabulary: ["Jard", "Rumble2 Flash"],
        deadline: 7, corrections: "jard audio => Jard Soundfolio\num =>" })
    await page.getByText("Preview with sample text", { exact: true }).click()
    await page.getByRole("textbox", { name: "Sample", exact: true }).fill("Planned 12 books.")
    await page.getByRole("textbox", { name: "Sample website host or URL", exact: true }).fill("https://example.com/another-page")
    await button("Preview").click()
    await page.getByText("Scheduled 12 books", { exact: true }).waitFor()
    await page.getByText("The local rewrite exceeded its 7-second deadline.", { exact: true }).waitFor()
    await findTrigger.click()
    await findSettings.fill("Ordered output profiles")
    await findSettings.press("Enter")
    await focusedSetting("Ordered output profiles")
    await page.waitForFunction(() => {
      const bounds = document.activeElement?.getBoundingClientRect()
      return bounds && bounds.top >= 0 && bounds.bottom <= innerHeight
    })
    const editVocabulary = button("Edit vocabulary")
    await editVocabulary.click()
    const vocabulary = page.getByRole("dialog", { name: "Vocabulary" })
    const terms = vocabulary.getByRole("textbox", { name: "Term", exact: true })
    const spellings = vocabulary.getByRole("textbox", { name: "Sounds like", exact: true })
    await terms.first().waitFor()
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Term")
    await terms.first().fill("Mei Lin ")
    await spellings.first().fill("May Lean, ")
    await page.waitForFunction(() => window.dictationFixture.vocabularyEntries[0]?.soundsLike[0] === "May Lean")
    assert.equal(await terms.first().inputValue(), "Mei Lin ")
    assert.equal(await spellings.first().inputValue(), "May Lean, ")
    await spellings.first().fill("May Lean, May Linn")
    await page.evaluate(() => {
      window.dictationFixture.vocabularyEntries = [...window.dictationFixture.vocabularyEntries, { term: "Added through the CLI", soundsLike: [] }]
      window.dictationFixture.preferencesRevision++
      window.dispatchEvent(new CustomEvent("native-sdk:jard-dictation-preferences", { detail: { ...window.dictationFixture } }))
    })
    await spellings.first().press("Meta+k")
    assert.equal(await findSettings.count(), 0)
    await page.waitForFunction(() => window.dictationFixture.vocabularyEntries[0].soundsLike.includes("May Linn"))
    assert.equal(await terms.nth(1).inputValue(), "Added through the CLI")
    await button("Add term").click()
    await page.waitForFunction(() => document.activeElement?.getAttribute("aria-label") === "Term" &&
      document.activeElement.closest("[data-added]")?.dataset.added === "true")
    await terms.last().fill("Unindexed private draft")
    await button("Done").click()
    await vocabulary.waitFor({ state: "hidden" })
    await page.waitForFunction(() => window.dictationFixture.vocabularyEntries.length === 3)
    await findTrigger.click()
    await findSettings.fill("Unindexed private draft")
    await page.getByText("No matching settings.").waitFor()
    await findSettings.press("Escape")
    await editVocabulary.click()
    const deleteTerm = vocabulary.getByRole("button", { name: "Remove term" })
    await deleteTerm.nth(1).click()
    await deleteTerm.nth(1).click()
    await terms.first().press("Escape")
    await vocabulary.waitFor({ state: "hidden" })
    assert.deepEqual(await page.evaluate(() => window.dictationFixture.vocabularyEntries),
      [{ term: "Mei Lin", soundsLike: ["May Lean", "May Linn"] }])
    await editVocabulary.click()
    await spellings.first().fill("x")
    await vocabulary.getByRole("alert").getByText("Use 2 to 80 characters per alternate spelling.").waitFor()
    await page.setViewportSize({ width: 430, height: 485 })
    await page.waitForFunction(() => document.querySelector(".dictation-vocabulary-dialog").scrollWidth <= innerWidth)
    await button("Discard unsaved changes").click()
    await vocabulary.waitFor({ state: "hidden" })
    assert.deepEqual(await page.evaluate(() => window.dictationFixture.vocabularyEntries[0].soundsLike), ["May Lean", "May Linn"])
    await page.setViewportSize({ width: 1100, height: 420 })
    await editVocabulary.click()
    await spellings.first().fill("May Lean, May Linn, Mae Lin")
    await page.mouse.click(8, 8)
    await vocabulary.waitFor({ state: "hidden" })
    assert.deepEqual(await page.evaluate(() => window.dictationFixture.vocabularyEntries[0].soundsLike),
      ["May Lean", "May Linn", "Mae Lin"])
    await page.evaluate(() => {
      window.dictationFixture.preferencesWarning = "Updated saved preferences fixture."
      window.dispatchEvent(new CustomEvent("native-sdk:jard-dictation-preferences", { detail: { ...window.dictationFixture } }))
      window.dispatchEvent(new CustomEvent("native-sdk:jard-dictation-preferences", {
        detail: { ...window.dictationFixture, preferencesRevision: 1, preferencesWarning: "Older preferences fixture." },
      }))
    })
    await page.getByText("Updated saved preferences fixture.", { exact: true }).waitFor()
    assert.equal(await page.getByText("Older preferences fixture.", { exact: true }).count(), 0)
    await page.evaluate(() => { window.dictationFixture.preferencesWarning = "" })
    await page.getByRole("tab", { name: "History" }).click()
    assert.equal((await heading("Dictation").boundingBox()).x, dictationHeadingX)
    assert.equal(await whileDictating.isVisible(), false)
    await page.keyboard.press("Meta+k")
    await findSettings.fill("Dictation HUD")
    await findSettings.press("Enter")
    await focusedSetting("Recording indicator")
    assert.equal(await page.getByRole("tab", { name: "General" }).getAttribute("aria-selected"), "true")
    assert.equal(await feedback.evaluate(label => label.closest("details").open), true)
    await page.getByRole("tab", { name: "History" }).click()
    const list = page.getByRole("list", { name: "Dictations" })
    const detail = page.getByRole("article", { name: "Selected dictation" })
    const firstEntry = list.getByRole("button", { name: /A practice dictation/ })
    await firstEntry.waitFor()
    assert.deepEqual(await list.locator(".dictation-history-day").allTextContents(), ["Today", "Yesterday"])
    assert.equal(await firstEntry.getAttribute("aria-pressed"), "true")
    await detail.getByText("TextEdit").waitFor()
    const secondEntry = list.getByRole("button", { name: /Send the revised notes/ })
    assert.equal(await list.locator('button[tabindex="0"]').count(), 1)
    const historySearch = page.getByRole("searchbox", { name: "Search dictation history" })
    const historyRetention = page.getByRole("combobox", { name: "History retention" })
    await historyRetention.press("Tab")
    assert.equal(await historySearch.evaluate(node => node === document.activeElement), true)
    await historySearch.press("Tab")
    assert.equal(await firstEntry.evaluate(node => node === document.activeElement), true)
    await firstEntry.press("ArrowDown")
    assert.equal(await secondEntry.getAttribute("aria-pressed"), "true")
    assert.equal(await secondEntry.evaluate(node => node === document.activeElement), true)
    assert.equal(await firstEntry.getAttribute("tabindex"), "-1")
    assert.equal(await secondEntry.getAttribute("tabindex"), "0")
    await secondEntry.press("Home")
    assert.equal(await firstEntry.getAttribute("aria-pressed"), "true")
    const thirdEntry = list.getByRole("button", { name: /^Long transcript/ })
    const historyViewport = page.viewportSize()
    await page.setViewportSize({ width: historyViewport.width, height: 980 })
    await list.evaluate(element => { element.parentElement.scrollTop = 0 })
    const firstRow = await firstEntry.boundingBox()
    const thirdRow = await thirdEntry.boundingBox()
    await page.mouse.move(firstRow.x + 30, firstRow.y + firstRow.height / 2)
    await page.mouse.down()
    await page.mouse.move(thirdRow.x + 30, thirdRow.y + thirdRow.height / 2, { steps: 8 })
    await page.mouse.up()
    await detail.getByRole("heading", { name: "3 dictations selected" }).waitFor()
    assert.equal(await secondEntry.getAttribute("aria-pressed"), "true")
    assert.equal(await thirdEntry.evaluate(row => row.closest("li").dataset.connectBefore), "true")
    await button("Copy selected dictation").click()
    assert.deepEqual(await page.evaluate(() => window.copiedHistory), ["fixture", "next", "long"])
    await page.getByRole("status").filter({ hasText: /^Copied$/ }).waitFor()
    const rowCopy = thirdEntry.locator("..").getByRole("button", { name: "Copy dictation" })
    await thirdEntry.hover()
    await page.evaluate(() => { window.failHistoryCopy = true })
    await rowCopy.click()
    await page.getByRole("alert").getByText("Could not update the clipboard.").waitFor()
    assert.equal(await thirdEntry.locator("..").getByTitle("Copied", { exact: true }).count(), 0)
    await page.evaluate(() => { window.failHistoryCopy = false })
    await rowCopy.click()
    await thirdEntry.locator("..").getByTitle("Copied", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.copiedHistory), ["long"])
    await detail.getByRole("heading", { name: "3 dictations selected" }).waitFor()
    await firstEntry.click()
    await thirdEntry.click({ modifiers: ["Shift"] })
    await detail.getByRole("heading", { name: "3 dictations selected" }).waitFor()
    await secondEntry.click({ modifiers: ["Meta"] })
    await detail.getByRole("heading", { name: "2 dictations selected" }).waitFor()
    assert.equal(await thirdEntry.evaluate(row => row.closest("li").dataset.connectBefore), "false")
    await firstEntry.click()
    await page.setViewportSize(historyViewport)
    const detailBox = await detail.boundingBox()
    await detail.getByText("Audio duration").waitFor()
    const searchFrame = await historySearch.locator("..").boundingBox()
    assert.ok(detailBox.x > searchFrame.x + searchFrame.width && Math.abs(detailBox.y - searchFrame.y) < 2)
    const searchBox = await historySearch.boundingBox()
    const retentionBox = await historyRetention.boundingBox()
    await list.evaluate(element => { element.parentElement.scrollTop = 80 })
    assert.ok(await list.evaluate(element => element.parentElement.scrollTop > 0))
    assert.deepEqual(await historySearch.boundingBox(), searchBox)
    assert.deepEqual(await historyRetention.boundingBox(), retentionBox)
    await list.getByRole("button", { name: /^Long transcript/ }).click()
    assert.equal(await detail.getByText("Processing", { exact: true }).count(), 0)
    const copyBox = await button("Copy selected dictation").boundingBox()
    const detailBody = detail.locator(".dictation-history-detail-body")
    await detailBody.evaluate(element => { element.scrollTop = 100 })
    assert.ok(await detailBody.evaluate(element => element.scrollTop > 0))
    assert.deepEqual(await button("Copy selected dictation").boundingBox(), copyBox)
    await list.getByRole("button", { name: /Send the revised notes/ }).click()
    assert.equal(await secondEntry.getAttribute("tabindex"), "0")
    await detail.getByText("Rumble2 Flash").waitFor()
    await detail.getByText("Latency", { exact: true }).waitFor()
    await detail.getByText(/ms audio · 203 ms inference · 364 ms total/).waitFor()
    await detail.getByText("Output profile").waitFor()
    await detail.getByText("Default", { exact: true }).waitFor()
    await detail.getByText("Processing", { exact: true }).waitFor()
    await detail.getByText(/Qwen3\.5-2B · 118 ms · Transformations fell back/).waitFor()
    await detail.getByText("Original transcript").click()
    await detail.getByText("Um, send the revised notes to the team.").waitFor()
    await button("Copy selected dictation").click()
    await page.getByRole("status").filter({ hasText: /^Copied$/ }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.copiedHistory), ["next"])
    await historyRetention.selectOption("30")
    assert.equal(await page.evaluate(() => window.dictationFixture.retention), 30)
    await button("Older results").click()
    await list.getByRole("button", { name: /^Older dictation 13/ }).waitFor()
    assert.equal(await firstEntry.count(), 0)
    await button("Newer results").click()
    await firstEntry.waitFor()
    await historySearch.fill("other words")
    await page.getByText("Loading history…").waitFor()
    assert.equal(await button("Copy selected dictation").count(), 0)
    await page.getByText("No dictations found.").waitFor()
    assert.equal((await historySearch.boundingBox()).width, searchBox.width)
    await historySearch.fill("history books")
    await firstEntry.waitFor()
    await toggleSidebar.click()
    await button("Show sidebar").waitFor()
    assert.deepEqual(await searchTools.boundingBox(), fixedBounds)
    await button("Show sidebar").click()
    await page.setViewportSize({ width: 460, height: 710 })
    const compactList = await list.boundingBox()
    const compactDetail = await detail.boundingBox()
    assert.ok(compactDetail.y > compactList.y + compactList.height)
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await button("Delete selected dictation").click()
    await button("Delete").click()
    await page.getByText("No dictations found.").waitFor()
    await historySearch.clear()
    await secondEntry.click()
    await button("Delete selected dictation").click()
    await page.evaluate(() => {
      window.historyFixture = window.historyFixture.filter(item => item.id !== "next")
      window.dictationFixture.historyRevision++
    })
    await page.waitForFunction(() => document.querySelector(".dictation-history-text")?.textContent.startsWith("Long transcript."))
    await button("Delete", { exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.deletedHistory), ["next"])
    await list.getByRole("button", { name: /^Long transcript/ }).waitFor()
    await button("Clear history…").click()
    await page.keyboard.press("Meta+k")
    assert.equal(await findSettings.count(), 0)
    await button("Delete").click()
    await page.getByText("No saved dictations yet.").waitFor()
    await page.evaluate(() => {
      window.historyFixture = Array.from({ length: 16 }, (_, index) => ({
        id: `refilled-${index}`, text: `Refilled dictation ${index + 1}`, app: "Notes", time: Date.now() / 1000,
      }))
      window.dictationFixture.historyRevision++
    })
    await button("Older results").click()
    await list.getByRole("button", { name: /Refilled dictation 16/ }).waitFor()
    await page.evaluate(() => {
      window.historyFixture = []
      window.dictationFixture.historyRevision++
    })
    await page.getByText("No saved dictations yet.").waitFor()
    await page.evaluate(() => {
      window.historyFixture = [{ id: "new", text: "After clearing", app: "Notes", time: Date.now() / 1000 }]
      window.dictationFixture.historyRevision++
    })
    await list.getByRole("button", { name: /After clearing/ }).waitFor()
    showSaved = true
    await page.setViewportSize({ width: 1100, height: 750 })
    await button("Search sessions").click()
    await page.getByRole("checkbox", { name: "Meetings only" }).check()
    const meetingResult = page.getByRole("option", { name: /Fixture meeting/ })
    await meetingResult.waitFor()
    assert.equal(await page.getByRole("option", { name: /Ordinary recording/ }).count(), 0)
    await meetingResult.click()
    await heading("Fixture meeting").waitFor()
    await page.getByText("The microphone stopped sending sound during this meeting. Mac audio continued recording.", { exact: true }).waitFor()
    const notesTab = page.getByRole("tab", { name: "My Notes" })
    const editor = page.getByRole("textbox", { name: "My notes" })
    await notesTab.click()
    await editor.getByText("Saved fixture notes", { exact: true }).waitFor()
    const notesPadding = await editor.evaluate(node => parseFloat(getComputedStyle(node).paddingBlockStart) +
      parseFloat(getComputedStyle(node.closest('[role="tabpanel"]')).paddingBlockStart))
    for (const name of ["Overview", "Transcript", "Timeline", "Speaker analytics"]) {
      await page.getByRole("tab", { name, exact: true }).click()
      const padding = await page.getByRole("tabpanel", { name, exact: true }).evaluate(node =>
        parseFloat(getComputedStyle(node).paddingBlockStart))
      assert.ok(Math.abs(padding - notesPadding) < .1, `${name} starts at ${padding}px, My Notes at ${notesPadding}px`)
    }
    await notesTab.click()
    await editor.press("Meta+k")
    await page.getByRole("textbox", { name: "Link address" }).waitFor()
    await page.getByRole("textbox", { name: "Link address" }).press("Escape")
    await button("Reveal Markdown").click()
    assert.equal(await page.evaluate(() => window.revealedExport), meetingId)
    await page.getByRole("tab", { name: "Overview" }).click()
    await button("Summarize meeting").click()
    await button("Summarizing…").waitFor()
    await heading("Agenda approval").waitFor()
    const meetingSummary = page.getByRole("region", { name: "Meeting summary" })
    await meetingSummary.getByText("The group approved the agenda.").waitFor()
    await button("Regenerate summary").click()
    await meetingSummary.getByRole("alert").getByText("The local writer could not restart.").waitFor()
    await meetingSummary.getByText("The group approved the agenda.").waitFor()
    await button("Rename meeting").click()
    const meetingTitleField = page.getByRole("textbox", { name: "Meeting title" })
    await meetingTitleField.fill("Cancelled title")
    await meetingTitleField.press("Escape")
    await heading("Agenda approval").waitFor()
    assert.equal(meetingTitle, null)
    await button("Rename meeting").click()
    await meetingTitleField.fill("Team agenda review")
    await meetingTitleField.press("Enter")
    await heading("Team agenda review").waitFor()
    await button("Reveal Markdown").and(page.locator('[title="2026-09-17 0815 Team agenda review.md"]')).waitFor()
    await button("Reveal Markdown").click()
    assert.equal(await page.evaluate(() => window.revealedExport), meetingId)
    await page.getByText("Moves to Trash after 7 days", { exact: true }).waitFor()
    await button("Meeting exports").click()
    await page.getByRole("menuitem", { name: "Notes (.md)" }).click()
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: meetingId, kind: "notes" })
    await button("Meeting exports").click()
    await page.getByRole("menuitem", { name: "Move meeting audio to Trash…" }).click()
    await button("Move to Trash").click()
    await page.getByText("Audio removed", { exact: true }).waitFor()
    assert.equal(await button("Open audio player").count(), 0)
    await page.evaluate(() => sessionStorage.setItem("fixtureOnboarded", "true"))
    await page.goto(`http://127.0.0.1:18997/?session=${meetingId}&tab=transcript&at=7`)
    await page.waitForFunction(() => document.activeElement?.dataset.turn === "1")
    assert.equal(new URL(page.url()).searchParams.get("at"), "7")
    function publishMeeting(text, processed, status = "processing") {
      Object.assign(saved, { status, processed, finalizationPending: status !== "complete" })
      Object.assign(saved.transcript, { text, transcriptSource: status === "complete" ? "final" : "rolling",
        segments: [{ speaker: 2, start: 0, end: 2, text }] })
    }
    publishMeeting("Draft transcript", 0, "interrupted")
    await page.goto(`http://127.0.0.1:18997/?session=${meetingId}&tab=transcript`)
    await page.getByText("Draft transcript", { exact: true }).waitFor()
    await page.getByText("Waiting to resume meeting transcription…", { exact: true }).waitFor()
    publishMeeting("Updated working transcript", 4)
    await page.getByText("Updated working transcript", { exact: true }).waitFor()
    publishMeeting("Final meeting transcript", 10, "complete")
    await page.getByText("Final meeting transcript", { exact: true }).waitFor()
    const annotationEntry = page.getByRole("button", { name: "Fixture annotation", exact: true })
    assert.equal(await annotationEntry.count(), 0)
    showAnnotation = true
    await page.evaluate(id => window.dispatchEvent(new CustomEvent("native-sdk:jard-annotation", {
      detail: { phase: "finished", session: id },
    })), annotationId)
    await annotationEntry.click()
    const annotationPanel = page.getByRole("tabpanel", { name: "Annotation" })
    await annotationPanel.getByRole("img", { name: /contact sheet/i }).waitFor()
    assert.ok(Math.abs(await annotationPanel.evaluate(node => parseFloat(getComputedStyle(node).paddingBlockStart)) - notesPadding) < .1)
    assert.equal(new URL(page.url()).searchParams.get("tab"), "annotation")
    await annotationPanel.getByText("2 strokes", { exact: true }).waitFor()
    await annotationPanel.getByRole("button", { name: "Jump to 0:00.1", exact: true }).waitFor()
    const video = annotationPanel.locator("video")
    assert.equal(await video.getAttribute("src"), annotated.audioUrl)
    await annotationPanel.getByRole("button", { name: "Jump to 0:00.6", exact: true }).click()
    await page.waitForFunction(() => Math.abs(document.querySelector("video")?.currentTime - .6) < .05)
    await annotationPanel.getByRole("button", { name: "Save contact sheet" }).click()
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: annotationId, kind: "annotation-contact-sheet" })
    await annotationPanel.getByRole("button", { name: "Save strokes" }).click()
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: annotationId, kind: "annotation-manifest" })
    await button("Copy for Agent").click()
    await page.getByText("Copied context and contact sheet for rich-text paste.", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.copiedAnnotation), { id: annotationId })
    await button("Paste into Last App").click()
    await page.getByText("Sent annotation to Fixture editor.", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.pastedAnnotation), { id: annotationId })
    await button("Send to OpenCode…").click()
    const destinationPicker = page.getByRole("dialog", { name: "Send to OpenCode" })
    await destinationPicker.getByRole("radio", { name: /fixture release/i }).waitFor()
    await destinationPicker.getByText("Mentioned in the annotation", { exact: true }).waitFor()
    assert.equal(await button("Send annotation").isDisabled(), true)
    await destinationPicker.getByRole("textbox", { name: "Search OpenCode sessions" }).fill("notes")
    await destinationPicker.getByRole("radio", { name: /project notes/i }).check()
    assert.equal(await destinationPicker.getByRole("radio", { name: /fixture release/i }).count(), 0)
    assert.equal(await page.evaluate(() => window.sentAnnotation), undefined)
    await destinationPicker.getByRole("checkbox", { name: /Automatically send future/ }).check()
    await button("Remember destination").click()
    await destinationPicker.getByText("Project notes · Saved for example.test · Automatic", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.rememberedAnnotation), {
      id: annotationId, scope: "site", service: "fixture-service", target: "ses_two", automatic: true,
    })
    assert.equal(await page.evaluate(() => window.sentAnnotation), undefined)
    await button("Forget").click()
    await button("Forget").waitFor({ state: "detached" })
    assert.deepEqual(await page.evaluate(() => window.forgottenAnnotation), { id: annotationId, ruleId: "rule-fixture" })
    await destinationPicker.getByRole("radio", { name: /project notes/i }).check()
    await button("Send annotation").click()
    const deliveryLog = page.getByRole("region", { name: "OpenCode deliveries" })
    await deliveryLog.getByText("Project notes", { exact: true }).first().waitFor()
    await deliveryLog.getByText(/^Queued/).waitFor()
    assert.deepEqual(await page.evaluate(() => window.sentAnnotation), {
      id: annotationId, target: "ses_two", service: "fixture-service",
    })
    annotated.status = "interrupted"
    annotated.transcript = { ...annotated.transcript, interruption: "Narration transcription was interrupted." }
    await page.evaluate(() => localStorage.setItem("managedRoutingFixture", "true"))
    await page.goto(`http://127.0.0.1:18997/?session=${annotationId}&tab=annotation`)
    await annotationPanel.getByText("OpenCode processing is managed by your organization.", { exact: true }).waitFor()
    await deliveryLog.getByText(/^Queued/).waitFor()
    for (const label of ["Send to OpenCode…", "Undo send", "Restore files and undo…"]) {
      assert.equal(await button(label).isDisabled(), true, label)
    }
    assert.ok((await deliveryLog.getByRole("button", { name: "Send correction…" }).all()).length)
    for (const correction of await deliveryLog.getByRole("button", { name: "Send correction…" }).all()) {
      assert.equal(await correction.isDisabled(), true)
    }
    await button("Copy for Agent").click()
    await page.getByText("Copied context and contact sheet for rich-text paste.", { exact: true }).waitFor()
    await button("Save strokes").click()
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: annotationId, kind: "annotation-manifest" })
    await button("Cancel automatic send").click()
    await page.getByText("Project notes · Automatic send cancelled.", { exact: true }).waitFor()
    await page.evaluate(() => localStorage.removeItem("managedRoutingFixture"))
    await page.goto(`http://127.0.0.1:18997/?session=${annotationId}&tab=annotation`)
    await annotationPanel.getByRole("img", { name: /contact sheet/i }).waitFor()
    await page.getByText("Narration transcription was interrupted.", { exact: true }).waitFor()
    await deliveryLog.getByRole("button", { name: "Undo send" }).click()
    await deliveryLog.getByText(/^Withdrawn/).waitFor()
    assert.deepEqual(await page.evaluate(() => window.withdrawnAnnotation), { deliveryId: "fixture-delivery" })
    assert.equal(await deliveryLog.getByRole("button", { name: "Undo send" }).count(), 0)
    const fileUndo = deliveryLog.getByRole("button", { name: "Restore files and undo…" })
    const restore = page.getByRole("alertdialog", { name: "Restore files and undo this turn?" })
    await fileUndo.click()
    await restore.getByText(/including edits you made outside OpenCode/).waitFor()
    await restore.getByRole("button", { name: "Cancel", exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.withdrawnAnnotation), { deliveryId: "fixture-delivery" })
    await fileUndo.click()
    await restore.getByRole("button", { name: "Restore files and undo", exact: true }).click()
    await deliveryLog.getByText(/^Turn rolled back/).waitFor()
    assert.deepEqual(await page.evaluate(() => window.withdrawnAnnotation), { deliveryId: "fixture-edited", restoreFiles: true })
    await deliveryLog.getByRole("button", { name: "Send correction…" }).click()
    const confirmation = page.getByRole("alertdialog", { name: "Send a correction?" })
    await confirmation.getByText("Ask Project notes to disregard this annotation.").waitFor()
    await confirmation.getByRole("button", { name: "Cancel", exact: true }).click()
    assert.equal(await page.evaluate(() => window.correctedAnnotation), undefined)
    await deliveryLog.getByRole("button", { name: "Send correction…" }).click()
    await confirmation.getByRole("button", { name: "Send correction", exact: true }).click()
    await deliveryLog.getByText(/^Correction sent/).waitFor()
    await page.evaluate(id => {
      window.dispatchEvent(new CustomEvent("native-sdk:jard-annotation-routing", {
        detail: { capture: id, phase: "waiting", title: "Project notes", message: "OpenCode is temporarily unavailable. Jard will check again." },
      }))
    }, annotationId)
    await button("Cancel automatic send").click()
    await page.getByText("Project notes · Automatic send cancelled.", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.lastRouteControl), { id: annotationId, action: "cancel" })
    await page.evaluate(id => {
      window.dispatchEvent(new CustomEvent("native-sdk:jard-annotation-routing", {
        detail: { capture: id, phase: "unconfirmed", title: "Project notes", message: "Delivery unconfirmed. Check OpenCode for the annotation." },
      }))
    }, annotationId)
    await page.getByText(/Delivery unconfirmed\. Check OpenCode/).waitFor()
    assert.equal(await button("Cancel automatic send").count(), 0)
    assert.deepEqual(await page.evaluate(() => window.correctedAnnotation), { deliveryId: "fixture-received" })
    assert.equal(await page.getByRole("tab", { name: "Overview" }).count(), 0)
    await page.getByRole("tab", { name: "Transcript" }).click()
    assert.equal(new URL(page.url()).searchParams.get("tab"), "transcript")
    await page.getByRole("tab", { name: "Annotation" }).click()
    await button("Save strokes").click()
    assert.equal(new URL(page.url()).searchParams.get("tab"), "annotation")
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: annotationId, kind: "annotation-manifest" })
    await page.goto(`http://127.0.0.1:18997/?session=${meetingId}&tab=annotation`)
    await page.getByRole("tabpanel", { name: "Overview" }).waitFor()
    assert.equal(new URL(page.url()).searchParams.get("tab"), "overview")
    await page.evaluate(({ id, revision }) => sessionStorage.setItem(`jard.personalNotes.${id}`, JSON.stringify({
      revision, document: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text: "Recovered fixture draft" }] }] },
    })), { id: meetingId, revision: personalNotes.revision })
    notesChunkMissing = true
    const missingNotes = page.waitForResponse(response => response.url().endsWith("/fixture-missing-notes.js") && response.status() === 404)
    await page.goto(`http://127.0.0.1:18997/?session=${meetingId}&tab=notes`)
    await missingNotes
    await page.getByRole("alert").getByText("My Notes couldn’t open.", { exact: false }).waitFor()
    await page.getByRole("tab", { name: "Transcript", exact: true }).click()
    await page.getByRole("tabpanel", { name: "Transcript" }).waitFor()
    await notesTab.click()
    await page.getByRole("tabpanel", { name: "My Notes", exact: true }).waitFor()
    await button("Reload workspace").waitFor()
    notesChunkMissing = false
    await button("Reload workspace").click()
    await editor.getByText("Recovered fixture draft", { exact: true }).waitFor()
    await page.getByText("Saved locally", { exact: true }).waitFor()
    assert.equal(personalNotes.document.content[0].content[0].text, "Recovered fixture draft")
    await page.evaluate(() => sessionStorage.setItem("fixtureWindowNavigation", JSON.stringify({
      id: "cli-snapshot", pane: "history",
    })))
    await page.reload()
    await page.getByRole("tab", { name: "History", selected: true }).waitFor()
    await navigateWorkspace("cli-return", "home")
    await editor.getByText("Recovered fixture draft", { exact: true }).waitFor()
    assert.equal(new URL(page.url()).searchParams.get("tab"), "notes")
    await page.reload()
    await editor.getByText("Recovered fixture draft", { exact: true }).waitFor()
    await button("Settings").click()
    await settingsTab("General").click()
    const chooseLanguage = (locale, name) => page.getByRole("combobox", { name: interfaceCatalog.messages[locale]?.["Interface language"] ?? "Interface language" }).selectOption(name)
    await page.evaluate(() => { window.dictationFixture.languages = ["en"] })
    await chooseLanguage("en", "fr")
    const french = interfaceCatalog.messages.fr
    await heading(french.Settings).waitFor()
    assert.equal(await page.locator("html").getAttribute("lang"), "fr")
    assert.deepEqual(await page.evaluate(() => window.dictationFixture.languages), ["en"])
    await button(french.Back).click()
    await page.getByText("Recovered fixture draft", { exact: true }).waitFor()
    await page.evaluate(() => sessionStorage.removeItem("fixtureOnboarded"))
    await page.reload()
    await heading(french["Speak where you write."]).waitFor()
    await button(french["Set up later"]).click()
    await button(french.Settings).click()
    await chooseLanguage("fr", "system")
    await heading("Settings").waitFor()
    assert.equal(await page.locator("html").getAttribute("lang"), "en")
    await settingsTab("Dictation").click()
    await publishUpdate({ phase: "checking", manualCheck: true, openUpdateSettings: "first-menu-check" })
    await settingsAction("Looking for new versions…").waitFor()
    assert.equal(await settingsTab("General").getAttribute("aria-selected"), "true")
    await publishUpdate({ phase: "current", manualCheck: true })
    await updates.getByText("Jard is up to date.", { exact: true }).waitFor()
    await button("Back").click()
    await publishUpdate({ phase: "checking", manualCheck: false, message: "" })
    const automaticCheck = sidebarAction("Look for new versions")
    await automaticCheck.waitFor()
    assert.equal(await automaticCheck.isDisabled(), true)
    await publishUpdate({ phase: "error", manualCheck: false, message: "Background request failed." })
    await sidebarAction("Look for new versions").waitFor()
    await sidebarUpdate.getByText("Background request failed.").waitFor({ state: "hidden" })
    await sidebarUpdate.getByText("Jard is up to date.", { exact: true }).waitFor({ state: "hidden" })
    assert.equal(await heading("Settings").isVisible(), false)

    await page.evaluate(() => {
      sessionStorage.setItem("fixtureOnboarded", "done")
      sessionStorage.setItem("fixtureMenuCheck", "pre-workspace-menu-check")
    })
    assert.equal(new URL(page.url()).searchParams.get("session"), meetingId)
    let finishRestore
    holdSessionRestore = new Promise(resolve => { finishRestore = resolve })
    await page.reload()
    await sidebarUpdate.getByText("Could not verify the update feed.").waitFor()
    assert.equal(await heading("Settings").isVisible(), false)
    assert.equal(await button("Settings").isDisabled(), true)
    holdSessionRestore = undefined
    finishRestore()
    await updates.getByText("Could not verify the update feed.").waitFor()
    await button("Back").click()
    await page.reload()
    await sidebarUpdate.getByText("Could not verify the update feed.").waitFor()
    assert.equal(await heading("Settings").isVisible(), false)
    await page.evaluate(() => {
      window.dictationFixture.outputProfiles = JSON.stringify([
        { target: "default", steps: ["compact", "rewrite", "script"] },
        { target: "site:https://example.com/settings", steps: ["rewrite"] },
      ])
      window.dispatchEvent(new CustomEvent("native-sdk:jard-dictation-preferences", { detail: { ...window.dictationFixture } }))
    })
    await navigateWorkspace("cli-output-preview", "output")
    const output = page.getByRole("tabpanel", { name: "Output" })
    await output.getByRole("region", { name: "Every dictation" }).getByText("Compact whitespace").waitFor()
    const scriptReview = output.getByRole("region", { name: "Local script" })
    await scriptReview.getByRole("button", { name: "Choose a local script…", exact: true }).click()
    await scriptReview.getByText("Review this script version").waitFor()
    await scriptReview.getByText("Version aaaaaaaaaaaa", { exact: false }).waitFor()
    await scriptReview.getByRole("button", { name: "Reveal script", exact: true }).click()
    await page.waitForFunction(() => window.fixtureRevealedOutputScript)
    await page.evaluate(() => { window.scriptVersion = "bbbbbbbbbbbb" })
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).click()
    await scriptReview.getByRole("alert").getByText(/selected script changed/).waitFor()
    await scriptReview.getByText("Version bbbbbbbbbbbb", { exact: false }).waitFor()
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).click()
    await scriptReview.getByText("This version is approved.").waitFor()
    await page.evaluate(() => { window.scriptVersion = "cccccccccccc" })
    await scriptReview.getByText("Review this script version").waitFor()
    await scriptReview.getByRole("button", { name: "Remove script", exact: true }).click()
    await scriptReview.getByRole("button", { name: "Choose a local script…", exact: true }).waitFor()
    const scopedOutput = output.getByRole("region", { name: "In some apps" })
    await scopedOutput.getByText("https://example.com/settings", { exact: true }).click()
    await scopedOutput.getByText("Rewrite with local Qwen", { exact: true }).waitFor()
    const sample = output.getByRole("textbox", { name: /Sample sentence/ })
    await sample.fill("Sample  with  compact spaces")
    await output.getByRole("button", { name: "Preview sample" }).click()
    const previewResult = output.getByRole("region", { name: "Preview result" })
    await previewResult.getByText("Sample with compact spaces", { exact: true }).waitFor()
    await previewResult.getByText("Skipped in preview · Rewrite with local Qwen, Run local script", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.fixtureTryOutput), { action: "tryOutput", text: "Sample  with  compact spaces" })
    await sample.fill("Fresh sample")
    await previewResult.waitFor({ state: "hidden" })
    await output.getByRole("button", { name: "Edit with agent" }).click()
    await output.getByText("Output instructions copied.").waitFor()
    await output.getByRole("button", { name: "Reveal settings file" }).click()
    await page.waitForFunction(() => window.fixtureCopiedOutputInstructions && window.fixtureRevealedOutputSettings)
    await page.getByRole("tab", { name: "Output", selected: true }).press("ArrowRight")
    await page.getByRole("tab", { name: "History", selected: true }).press("ArrowLeft")
    await page.getByRole("tab", { name: "Output", selected: true }).waitFor()
    await findTrigger.click()
    await findSettings.fill("Preview sample")
    await findSettings.press("Enter")
    await page.waitForFunction(() => document.activeElement?.tagName === "TEXTAREA")
    await page.evaluate(() => { window.fixtureChoosesCatalog = true })
    await scriptReview.getByRole("button", { name: "Choose a local script…", exact: true }).click()
    await scriptReview.getByText("Add label", { exact: true }).waitFor()
    await scriptReview.getByText("Add a transcript label", { exact: true }).waitFor()
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).click()
    await scriptReview.getByText("This version is approved.", { exact: true }).waitFor()
    await page.getByRole("tablist", { name: "Dictation tabs" }).getByRole("tab", { name: "General", exact: true }).click()
    await findTrigger.click()
    await findSettings.fill("Ordered output profiles")
    await findSettings.press("Enter")
    await focusedSetting("Ordered output profiles")
    const profileSummary = page.getByText("Ordered output profiles", { exact: true })
    if (!await profileSummary.evaluate(node => node.parentElement.open)) await profileSummary.click()
    await addOutputStep.selectOption("local:add-label")
    await button("Remove Add label").waitFor()
    await button("Save profiles").click()
    await page.waitForFunction(() => JSON.parse(window.dictationFixture.outputProfiles).some(row => row.steps.includes("local:add-label")))
    await page.getByRole("tab", { name: "Output", exact: true }).click()
    await output.getByRole("region", { name: "Every dictation" }).getByText("Add label", { exact: true }).waitFor()
    await page.evaluate(() => { window.fixtureChoosesEmbedded = true })
    await scriptReview.getByRole("button", { name: "Choose another script…", exact: true }).click()
    await scriptReview.getByText("jaro.config.ts", { exact: true }).waitFor()
    await scriptReview.getByText(/Changes to the configuration or its imports need a new approval/).waitFor()
    const allowNetwork = scriptReview.getByRole("checkbox", { name: /Allow network requests/ })
    const allowPrograms = scriptReview.getByRole("checkbox", { name: /Allow programs/ })
    const sharePage = scriptReview.getByRole("checkbox", { name: /Share page and window details/ })
    assert.equal(await sharePage.isChecked(), false)
    assert.equal(await allowNetwork.isChecked(), false)
    assert.equal(await allowPrograms.isChecked(), false)
    await scriptReview.getByText("example.test", { exact: true }).waitFor()
    await allowPrograms.check()
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).click()
    await scriptReview.getByText("This version is approved.", { exact: true }).waitFor()
    assert.deepEqual(await page.evaluate(() => window.scriptGrants), { programs: true })
    await sharePage.check()
    await scriptReview.getByRole("button", { name: "Update permissions", exact: true }).click()
    await page.waitForFunction(() => window.scriptGrants.pageContext === true)
    await sharePage.uncheck()
    await allowPrograms.uncheck()
    await scriptReview.getByRole("button", { name: "Update permissions", exact: true }).click()
    await page.waitForFunction(() => window.scriptGrants.programs === false && window.scriptGrants.pageContext === false)
    await page.evaluate(() => { window.scriptVersion = "dddddddddddd" })
    await scriptReview.getByText("Review this script version").waitFor()
    assert.equal(await sharePage.isChecked(), false)
    assert.equal(await allowNetwork.isChecked(), false)
    assert.equal(await allowPrograms.isChecked(), false)
    await page.evaluate(() => { window.fixtureInvalidConfig = true })
    await scriptReview.getByText("label.ts:1:20: Expected an expression", { exact: true }).waitFor()
    await scriptReview.getByText("Definitions from the last valid version", { exact: true }).waitFor()
    await scriptReview.getByText("Add label", { exact: true }).waitFor()
    assert.equal(await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).count(), 0)
    await page.evaluate(() => { window.fixtureInvalidConfig = false; window.scriptVersion = "eeeeeeeeeeee" })
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).waitFor()
    await scriptReview.getByText("Definitions from the last valid version", { exact: true }).waitFor({ state: "hidden" })
    await allowPrograms.check()
    await scriptReview.getByRole("button", { name: "Allow this version", exact: true }).click()
    await scriptReview.getByText("This version is approved.", { exact: true }).waitFor()
    await page.evaluate(() => { window.fixtureTransformPolicy = "loopback" })
    await scriptReview.getByText("Your organization allows network requests to this Mac only. Programs are disabled.", { exact: true }).waitFor()
    assert.equal(await allowPrograms.isDisabled(), true)
    assert.equal(await allowPrograms.isChecked(), false)
    assert.equal(await allowNetwork.isDisabled(), false)
    await allowNetwork.check()
    await scriptReview.getByRole("button", { name: "Update permissions", exact: true }).click()
    await page.waitForFunction(() => window.scriptGrants.network && window.scriptGrants.programs)
    await page.evaluate(() => { window.fixtureTransformPolicy = "disabled" })
    await scriptReview.getByText("Your organization has turned off transformations.", { exact: true }).waitFor()
    assert.equal(await scriptReview.getByRole("button", { name: "Choose another script…", exact: true }).count(), 0)
    assert.equal(await allowNetwork.count(), 0)

    await page.evaluate(() => {
      localStorage.setItem("managedAnnotationFixture", "true")
      sessionStorage.removeItem("fixtureWindowNavigation")
    })
    await page.goto(`http://127.0.0.1:18997/?session=${annotationId}&tab=annotation`)
    await annotationPanel.getByRole("img", { name: /contact sheet/i }).waitFor()
    await button("Copy for Agent").click()
    await page.getByText("Copied context and contact sheet for rich-text paste.", { exact: true }).waitFor()
    await button("Save strokes").click()
    assert.deepEqual(await page.evaluate(() => window.lastExport), { id: annotationId, kind: "annotation-manifest" })
    await button("Settings").click()
    await settingsTab("Annotate").click()
    await page.getByText("Screen annotation is managed by your organization.", { exact: true }).waitFor()
    for (const name of ["Choose Display…", "Choose Window…", "Choose Area…", "Wired iPhone or iPad"]) {
      assert.equal(await button(name).isDisabled(), true, name)
    }
    assert.equal(await page.getByText(/With Accessibility enabled, press/).count(), 0)
    await page.evaluate(() => localStorage.removeItem("managedAnnotationFixture"))
    await page.reload()
    if (!(await settingsTab("Annotate").isVisible())) await button("Settings").click()
    await settingsTab("Annotate").click()
    await button("Choose Display…").click()
    await page.getByText("Choose the display.", { exact: true }).waitFor()
    await button("Cancel").click()

    managedMeetings = true
    await page.evaluate(() => localStorage.setItem("managedMeetingsFixture", "true"))
    await page.goto(`http://127.0.0.1:18997/?session=${meetingId}&tab=overview`)
    await page.getByText("Meeting summaries and overviews are managed by your organization.", { exact: true }).waitFor()
    assert.equal(await button("Regenerate summary").count(), 0)
    assert.equal(await button("Summarize meeting").count(), 0)
    await page.getByRole("tab", { name: "Transcript", exact: true }).click()
    await page.getByRole("tabpanel", { name: "Transcript" }).getByText("Final meeting transcript", { exact: true }).waitFor()
    await button("Settings").click()
    await settingsTab("Meetings").click()
    await page.getByText("Meetings are managed by your organization.", { exact: true }).waitFor()
    assert.equal(await page.locator('[data-setting-title="System Audio Recording"]').count(), 0)
    assert.equal(await button("Record Meeting").count(), 0)
    assert.equal(await page.getByRole("switch", { name: "Offer to record detected calls" }).count(), 0)
    managedMeetings = false
    await page.evaluate(() => localStorage.removeItem("managedMeetingsFixture"))
    await page.reload()
    if (!(await settingsTab("Meetings").isVisible())) await button("Settings").click()
    await settingsTab("Meetings").click()
    await button("Record Meeting").waitFor()
    assert.equal(await button("Record Meeting").isDisabled(), false)

    await button("Record Meeting").click()
    const capture = (event, detail = {}) => page.evaluate(({ event, detail }) => {
      window.dispatchEvent(new CustomEvent("native-sdk:jard-capture", {
        detail: { event, meeting: "live-meeting-fixture", ...detail },
      }))
    }, { event, detail })
    const publishLiveTurns = count => {
      const segments = Array.from({ length: count }, (_, i) => ({ start: i * 4, end: i * 4 + 4,
        speaker: i % 2 + 1, text: `Meeting turn ${i + 1} discusses the schedule and assigns the next task.` }))
      return capture("preview", { text: segments.map(turn => turn.text).join(" "), segments })
    }
    await capture("started", { mode: "both", microphone: "Fixture microphone", live: true })
    await publishLiveTurns(26)
    await button("View live transcript").click()
    const liveTranscript = page.locator("[data-live-transcript]")
    const liveTurn = n => liveTranscript.getByText(`Meeting turn ${n} discusses the schedule and assigns the next task.`, { exact: true })
    const atLiveEnd = () => page.waitForFunction(() => {
      const area = document.querySelector("[data-live-transcript]")
      return area && area.scrollHeight > area.clientHeight + 100 && area.scrollHeight - area.clientHeight - area.scrollTop <= 2
    })
    const scrollTo = (offset) => liveTranscript.evaluate(async (area, offset) => {
      area.scrollTop = offset === "end" ? area.scrollHeight : offset
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      return area.scrollTop
    }, offset)
    await liveTurn(26).waitFor()
    await page.waitForFunction(() => document.activeElement?.hasAttribute("data-live-transcript"))
    await atLiveEnd()
    await publishLiveTurns(27)
    await liveTurn(27).waitFor()
    await atLiveEnd()
    const reading = await scrollTo(120)
    assert.ok(reading > 0)
    await publishLiveTurns(29)
    await liveTurn(29).waitFor()
    assert.ok(Math.abs(await liveTranscript.evaluate(area => area.scrollTop) - reading) <= 2)
    await scrollTo("end")
    await publishLiveTurns(30)
    await liveTurn(30).waitFor()
    await atLiveEnd()

    await page.evaluate(() => {
      Object.assign(window.meetingsFixture, { phase: "saving" })
      window.dispatchEvent(new CustomEvent("native-sdk:jard-meetings", { detail: { ...window.meetingsFixture } }))
    })
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    const stopping = await liveTranscript.evaluate(area => area.scrollTop)
    await publishLiveTurns(34)
    await liveTurn(34).waitFor()
    assert.ok(Math.abs(await liveTranscript.evaluate(area => area.scrollTop) - stopping) <= 2)
    await capture("finished", { reason: "Stopped" })
    await capture("accepted", { id: meetingId })
    await page.getByRole("tabpanel", { name: "Transcript" }).getByText("Final meeting transcript", { exact: true }).waitFor()

    const savedMeetingName = meetingTitle ?? meetingBrief?.title ?? saved.name
    const trashMeeting = button(`Delete ${savedMeetingName}`)
    const trashDialog = page.getByRole("alertdialog", { name: `Move “${savedMeetingName}” to Trash?` })
    await trashMeeting.click()
    await trashDialog.waitFor()
    await trashDialog.getByRole("button", { name: "Cancel" }).click()
    assert.equal(trashMeetingRequests, 0)
    await trashMeeting.click()
    const confirmTrash = trashDialog.getByRole("button", { name: "Move to Trash" })
    await confirmTrash.click()
    await trashDialog.getByRole("alert").getByText("Wait for the notes to finish.").waitFor()
    assert.equal(meetingTrashed, false)
    await confirmTrash.click()
    await page.getByRole("status").getByText("Meeting archived. Jaro will retry moving it to the Trash.").waitFor()
    await trashMeeting.waitFor({ state: "hidden" })
    await button("Delete Ordinary recording").click()
    await page.getByRole("alertdialog", { name: "Delete “Ordinary recording”?" }).getByRole("button", { name: "Delete permanently" }).waitFor()
    await page.keyboard.press("Escape")

    await page.setViewportSize({ width: 430, height: 730 })
    await page.waitForFunction(() => document.documentElement.scrollWidth <= innerWidth)
    assert.deepEqual(errors, [])
    console.log("Dictation onboarding, named Output steps, updates, meetings, tab spacing and My Notes failure recovery passed in WebKit.")
  } finally {
    await browser.close()
  }
}

run().catch(error => { console.error(error); process.exitCode = 1 })
