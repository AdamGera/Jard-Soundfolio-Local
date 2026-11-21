// This script exists only in the isolated QA copy of index.html.
(async () => {
  const token = new URL(document.currentScript.src).searchParams.get("token")
  const request = (path, options = {}) => fetch("/__parity/" + path, {
    ...options, headers: { "x-parity-token": token, "content-type": "application/json" },
  })
  const config = await (await request("config")).json()
  if (config.nativeLayout) {
    document.documentElement.dataset.nativeWindow = "true"
    for (const [key, value] of Object.entries(config.nativeLayout)) document.documentElement.style.setProperty(key,value)
  }
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
  async function until(test, message) {
    for (let i = 0; i < 150; i++) {
      const value = test()
      if (value) return value
      await sleep(100)
    }
    throw new Error(message)
  }
  const one = selector => document.querySelector(selector)
  const byText = (selector, value) => [...document.querySelectorAll(selector)].find(el => el.textContent.trim() === value)
  const click = async (find, label) => {
    const element = await until(find, "Missing " + label)
    element.dispatchEvent(new PointerEvent("pointerdown", {bubbles:true, pointerType:"mouse", button:0}))
    element.dispatchEvent(new MouseEvent("mousedown", {bubbles:true, button:0}))
    element.focus({ preventScroll: true })
    element.dispatchEvent(new PointerEvent("pointerup", {bubbles:true, pointerType:"mouse", button:0}))
    element.dispatchEvent(new MouseEvent("mouseup", {bubbles:true, button:0}))
    element.click()
  }
  const selectors = {
    header: ".soundfolio-header", brand: ".wordmark", logo: ".wordmark-icon", panel: ".transcript-panel",
    tabs: '[role="tablist"]', activeTab: '[role="tab"][data-state="active"]', dock: ".control-dock",
    record: ".record-button", live: ".live-button", speakers: ".speaker-toggle", hints: ".language-picker",
    title: ".session-heading h1", meta: ".session-meta", heading: ".session-heading", sidebar: "#workspace-sidebar", palette: ".session-search-dialog", searchInput: ".session-search-input", toolbar: ".sidebar-tools",
    turn: ".turn-bubble", note: ".note-body", conversation: ".conversation-scroll",
  }
  // Snapshot resting controls independently of the physical cursor on the test Mac.
  function ignorePointerHover(rules) {
    for (const rule of rules) {
      if (rule.selectorText?.includes(":hover")) {
        rule.selectorText = rule.selectorText.replaceAll(":hover", ":hover[data-qa-hover]")
      }
      if (rule.cssRules) ignorePointerHover(rule.cssRules)
    }
  }
  const results = {}
  async function frame(name) {
    await document.fonts.ready
    await new Promise(requestAnimationFrame)
    const transitions = document.getAnimations().filter(animation => animation instanceof CSSTransition)
    await Promise.all(transitions.map(animation => animation.finished.catch(() => {})))
    await sleep(180)
    window.scrollTo({ top: 0, behavior: "instant" })
    await new Promise(requestAnimationFrame)
    const items = {}
    for (const [label, selector] of Object.entries(selectors)) {
      const el = one(selector)
      if (!el) continue
      const box = el.getBoundingClientRect()
      if (!box.width || !box.height) continue
      const css = getComputedStyle(el)
      items[label] = { x:box.x, y:box.y, width:box.width, height:box.height, font:css.fontFamily,
        size:css.fontSize, weight:css.fontWeight, color:css.color, fill:css.backgroundColor, radius:css.borderRadius }
    }
    results[name] = items
    if (config.snapshots) {
      document.documentElement.dataset.parityFrame = name
      await until(() => document.documentElement.dataset.parityCaptured === name, "Snapshot missing for " + name)
    }
  }
  async function tab(name) {
    await click(() => byText('[role="tab"]',name), name)
    await until(() => byText('[role="tab"][data-state="active"]',name), name + " selection")
  }
  try {
    await until(() => one(".record-button:not(:disabled)") || one(".session-heading h1"), "First launch")
    if (window.jardDesktop) {
      const dictation = await window.jardDesktop.request("systemDictation", { action: "status" }).catch(() => null)
      if (dictation?.onboarding && !["done", "later"].includes(dictation.onboarding)) {
        const later = await until(() => [...document.querySelectorAll(".dictation-tour button")].find(
          button => button.textContent.trim() === "Set up later"), "First-run deferral")
        later.click()
        await until(() => !document.querySelector(".dictation-tour"), "Tour dismissed")
      }
    }
    if (one(".session-heading h1")) {
      await click(() => one('button[aria-label="New session"]'), "Return from restored session")
    }
    await until(() => one(".record-button:not(:disabled)"), "Recorder never became ready")
    const key = (el, key, modifiers = {}) =>
      el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...modifiers }))
    const type = (input, text) => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, text)
      input.dispatchEvent(new Event("input", {bubbles:true}))
    }
    await until(() => one(".library-open"), "Saved sidebar row")
    for (const sheet of document.styleSheets) ignorePointerHover(sheet.cssRules)
    await frame("recorder")
    await click(() => one('[aria-label="Hide sidebar"]'), "Hide sidebar")
    await until(() => one('button[aria-label="Show sidebar"][aria-expanded="false"]'), "Collapsed sidebar")
    await frame("collapsed")
    await click(() => one('[aria-label="Show sidebar"]'), "Show sidebar")
    await click(() => one(".sidebar-section"), "Collapse session list")
    await until(() => one("#sidebar-sessions").hidden, "Collapsed session list")
    await click(() => one(".sidebar-section"), "Expand session list")
    await until(() => !one("#sidebar-sessions").hidden && one(".library-open"), "Saved sidebar session")
    await frame("sessions")
    const remove = one('button[aria-label="Delete Listening Test Review"]')
    await click(() => remove, "Open delete confirmation")
    const cancel = await until(() => byText('[role="alertdialog"] button', "Cancel"), "Delete confirmation")
    await until(() => document.activeElement === cancel, "Cancel receives default focus")
    key(cancel, "Escape")
    await until(() => !one('[role="alertdialog"]') && document.activeElement === remove, "Delete cancellation restores focus")

    const trigger = one('button[aria-label="Search sessions"]')
    await click(() => trigger, "Search sessions")
    const field = () => one('[role="combobox"][aria-label="Search sessions"]')
    let input = await until(() => field() && document.activeElement === field() && field(), "Focused session search")
    const active = () => document.getElementById(input.getAttribute("aria-activedescendant"))?.textContent
    const option = () => one(".session-search-name")?.textContent
    await until(() => option() === "Listening Test Review" && active() === "New session", "Recent sessions")
    key(input, "ArrowDown")
    await until(() => active()?.includes("Listening Test Review"), "ArrowDown to recent session")
    key(input, "ArrowUp")
    await until(() => active() === "New session", "ArrowUp to new session")
    await frame("sessionSearch")
    key(input, "Escape")
    await until(() => !field() && document.activeElement === trigger, "Restore search trigger focus")
    key(window, "k", {metaKey:true})
    input = await until(field, "Command-K opens search")
    key(input, "Enter")
    await until(() => !field() && one(".record-button"), "New session action opens recorder")
    // Let the preceding action finish restoring focus before issuing another shortcut.
    await new Promise(requestAnimationFrame)
    await new Promise(requestAnimationFrame)
    key(window, "k", {ctrlKey:true})
    input = await until(() => field() && document.activeElement === field() && field(), "Ctrl-K opens search")
    for (const term of ["Listening Test", "Design review", "headphones"]) {
      type(input, "unmatched fixture")
      await until(() => one(".session-search-message")?.textContent === "No matching sessions.", "Empty search results")
      type(input, term)
      await until(() => option() === "Listening Test Review" && active()?.includes("Listening Test Review"), "Search for " + term)
    }
    key(input, "Enter")
    await until(() => one(".session-heading h1") && one(".note-body")?.textContent.includes("Recording controls"), "Overview")
    await frame("overview")
    await tab("Timeline")
    await until(() => byText("strong", "Friday review"), "Timeline chapter rows")
    await frame("timeline")
    await tab("Transcript")
    await until(() => one(".turn-bubble"), "Speaker turns")
    await frame("transcript")
    await click(() => one(".find-trigger"), "Transcript search")
    input = await until(() => one('input[aria-label="Search transcript"]'), "Search field")
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(input,"recording")
    input.dispatchEvent(new Event("input",{bubbles:true}))
    await until(() => one("mark[data-match]"),"Highlighted search hit")
    await frame("search")
    await tab("Speaker analytics")
    await until(() => one(".speaker-shares li"), "Speaker analytics loaded")
    await frame("analytics")
    await click(() => one(".chat-launcher"),"Open chat")
    await until(() => one(".conversation-scroll"),"Chat messages")
    await frame("chat")
    let permissions
    if (config.checkPermissions) {
      permissions=Object.fromEntries(await Promise.all(["mic","system"].map(async source=>{
        const {status}=await window.zero.invoke("jard.permission",{source,prompt:false})
        if (!["granted","denied","restricted","unavailable"].includes(status)) {
          throw Error("Unexpected "+source+" permission status: "+status)
        }
        return [source,status]
      })))
    }
    await request("report",{method:"POST",body:JSON.stringify({
      permissions, desktop: typeof window.jardDesktop?.request === "function", scheme: matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light",
      nativeLayout: document.documentElement.hasAttribute("data-native-window") ? Object.fromEntries(
        ["--window-controls-end","--window-controls-center-y"].map(key=>[key,getComputedStyle(document.documentElement).getPropertyValue(key)])) : null,
      viewport:[innerWidth,innerHeight], frames:results, assets:[...document.querySelectorAll("script[src],link[rel=stylesheet]")].map(el=>el.src || el.href).filter(v=>v.includes("/assets/")).map(v=>new URL(v).pathname),
    })})
  } catch(error) {
    await request("report",{method:"POST",body:JSON.stringify({error:String(error),url:location.href,text:document.body.innerText.slice(0,2500)})})
  }
})()
