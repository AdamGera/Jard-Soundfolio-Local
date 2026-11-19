#!/usr/bin/env node
// Run after bun run build, with playwright-core and its WebKit installed.
// Every request is intercepted. This test never opens Settings or a capture device.
const assert = require("node:assert/strict")
const path = require("node:path")
const { webkit } = require("playwright-core")
const dist = path.resolve(__dirname, "../../dist")

async function run() {
  const browser = await webkit.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1050, height: 760 } })
    page.setDefaultTimeout(5000)
    const errors = []
    page.on("pageerror", error => errors.push(error.message))
    await page.route("**/*", route => {
      const url = new URL(route.request().url())
      if (url.pathname.endsWith("/stream")) return route.fulfill({ status: 204 })
      if (url.pathname.startsWith("/api/")) {
        return route.fulfill({ json: url.pathname === "/api/status" ? { status: "ready", languages: [], diarization: true } : [] })
      }
      return route.fulfill({ path: path.join(dist, url.pathname === "/" ? "index.html" : url.pathname) })
    })
    await page.addInitScript(() => {
      const log = []
      const state = { mic: "unavailable", system: "unavailable" }
      const answer = { mic: "denied", system: "granted" }
      window.fixture = { log, state, answer, refuse: null }
      window.zero = { invoke: async (command, args) => {
        log.push([command, args])
        if (command === "jard.devices") return { microphone: "Fixture Mac input", drafts: [] }
        if (command === "jard.permission") {
          if (args.prompt) state[args.source] = answer[args.source]
          return { status: state[args.source] }
        }
        if (command === "jard.record") {
          const detail = window.fixture.refuse
            ? { event: "error", permission: window.fixture.refuse, message: "SCStreamErrorDomain -3801" }
            : { event: "started", mode: args.mode, microphone: "Fixture Mac input" }
          setTimeout(() => window.dispatchEvent(new CustomEvent("native-sdk:jard-capture", { detail })), 10)
        }
        return {}
      }}
    })
    const button = name => page.getByRole("button", { name, exact: true })
    const calls = cmd => page.evaluate(cmd => window.fixture.log.filter(([name]) => cmd === name).map(([,args]) => args), cmd)
    const guide = page.getByRole("dialog")
    await page.goto("http://127.0.0.1:18997/")
    await page.waitForFunction(() => document.querySelector(".record-button")?.disabled === false)

    await button("Recording access").click()
    await guide.waitFor()
    assert.deepEqual(await calls("jard.permission"), [{source:"mic", prompt:false}, {source:"system", prompt:false}])
    assert.equal((await calls("jard.record")).length, 0)
    await guide.getByText("Unconfirmed", {exact:true}).waitFor()
    await button("Verify Mac audio access").click()
    await guide.getByText("Mac audio access is ready.").waitFor()
    assert.equal((await calls("jard.record")).length, 0)
    await page.evaluate(() => { window.fixture.state.system = "unavailable"; window.fixture.log.length = 0 })
    await button("Cancel").click()
    await button("Record").click()
    const settings = button("Open Microphone settings")
    await settings.waitFor()
    await settings.click()
    assert.deepEqual(await calls("jard.openPrivacy"), [{source:"mic"}])
    assert.deepEqual((await calls("jard.permission")).filter(x=>x.prompt).map(x=>x.source), ["mic", "system"])
    assert.equal((await calls("jard.record")).length, 0)
    await page.getByText("Jard isn’t listed in System Settings").click()
    assert(await button("Copy recovery command").isVisible())
    assert.equal(await guide.locator("code").innerText(), "/usr/bin/tccutil reset Microphone local.jardaudio.desktop")
    await page.evaluate(() => { window.fixture.state.mic = "granted"; window.dispatchEvent(new Event("focus")) })
    await button("Retry recording").waitFor()
    assert.equal((await calls("jard.record")).length, 0)

    // Simulate a helper-side TCC refusal despite favorable host preflight.
    await page.evaluate(() => window.fixture.refuse = "system")
    await button("Retry recording").click()
    await button("Open Screen & System Audio Recording settings").click()
    assert(await guide.getByText("If Jard is already on in System Settings", { exact: false }).isVisible())
    assert.deepEqual((await calls("jard.openPrivacy")).at(-1), {source:"system"})
    await page.getByText("Jard isn’t listed in System Settings").click()
    await button("Show this app in Finder").click()
    assert.equal((await calls("jard.revealApp")).length, 1)
    assert(await button("Retry recording").isVisible())
    await button("Cancel").click()

    // Opening an already granted guide is read-only, even after clicking Done.
    await button("Recording access").click()
    await button("Done").click()
    assert.equal((await calls("jard.record")).length, 1)
    const mode = page.getByRole("combobox", { name: "Recording source" })
    await mode.selectOption("mic")
    await page.evaluate(() => { window.fixture.state.mic = "restricted"; window.fixture.log.length = 0 })
    await button("Record").click()
    await page.getByText("Restricted by macOS", {exact:true}).waitFor()
    await page.getByText("Jard isn’t listed in System Settings").click()
    assert(!await button("Copy recovery command").count())
    assert.deepEqual(await calls("jard.permission"), [{source:"mic",prompt:false}])
    await button("Cancel").click()
    await mode.selectOption("system")
    await page.evaluate(() => { window.fixture.state.system = "unavailable"; window.fixture.answer.system = "granted"; window.fixture.refuse = null; window.fixture.log.length = 0 })
    await button("Record").click()
    await button("Stop").waitFor()
    assert.deepEqual(await calls("jard.permission"), [{source:"system",prompt:false},{source:"system",prompt:true}])
    assert.equal((await calls("jard.record")).length,1)
    assert.deepEqual(errors, [])
    console.log("Recording permission checks, first prompt, denial, scoped help, managed restrictions, capture refusal, explicit retry and read-only Done passed in WebKit.")
  } finally { await browser.close() }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
