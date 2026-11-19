#!/usr/bin/env node
// Optional --browser-driver for web_parity.py --playback. Requires playwright-core.
const { chromium } = require("playwright-core")
const main = async () => {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    headless: true, args: ["--mute-audio"],
  })
  const close = () => browser.close()
  process.once("SIGTERM", close)
  try {
    const page = await browser.newPage({viewport: {width:1050, height:760}})
    await page.goto(process.env.JARD_QA_URL)
    while (browser.isConnected()) {
      const point = await page.evaluate(() => window.qaPoint?.splice(0, 2))
      if (point?.length) await page.mouse.click(...point)
      await page.waitForTimeout(35)
    }
  } finally { await browser.close() }
}
main().catch(error => { console.error(error.message); process.exitCode = 1 })
