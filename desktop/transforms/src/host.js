// Runs in the isolated JavaScript context. The native runner owns its deadlines.
import io from "jaro:io"

const timers = new Map()
const clearTimers = timers.clear.bind(timers)
const clock = Date.now
const parse = JSON.parse
const stringify = JSON.stringify
const freeze = Object.freeze
let next = 0

function consoleMethod(level) {
  return (...values) => {
    try {
      const parts = values.slice(0, 32).map(value => {
        if (typeof value === "string") return value.slice(0, 4096)
        try { return (stringify(value) ?? String(value)).slice(0, 4096) }
        catch { return "[Unserializable value]" }
      })
      io.log(level, parts.join(" "))
    } catch {}
  }
}

globalThis.console = freeze({
  log: consoleMethod("info"), info: consoleMethod("info"), debug: consoleMethod("debug"),
  warn: consoleMethod("warn"), error: consoleMethod("error"),
})
globalThis.queueMicrotask = callback => { Promise.resolve().then(callback) }
globalThis.setTimeout = (callback, delay = 0, ...args) => {
  if (typeof callback !== "function") throw new TypeError("setTimeout expects a function")
  if (timers.size >= 256) throw new RangeError("Too many pending transformation timers")
  const id = ++next
  const milliseconds = Math.max(0, Math.min(Number(delay) || 0, 60_000))
  timers.set(id, { callback, time: clock() + milliseconds, args })
  return id
}
globalThis.clearTimeout = id => { timers.delete(id) }

export function resetTimers() {
  clearTimers()
}

export function serviceTimer() {
  const now = clock()
  let nextDelay = -1
  for (const [id, { callback, time, args }] of timers) {
    const delay = time - now
    if (delay <= 0) {
      timers.delete(id)
      callback(...args)
      return 0
    }
    if (nextDelay < 0 || delay < nextDelay) nextDelay = delay
  }
  return nextDelay
}

const isObject = value => value !== null && typeof value === "object" && !Array.isArray(value)

export function register(config) {
  const rows = config?.transformations
  if (!isObject(rows) || Object.keys(rows).length < 1 || Object.keys(rows).length > 32) {
    throw new TypeError("Define between one and 32 transformations in the default export")
  }
  const transforms = new Map()
  const definitions = Object.entries(rows).map(([id, row]) => {
    if (!/^[a-z][a-z0-9-]{0,47}$/.test(id) || !isObject(row)) {
      throw new TypeError("Each transformation needs a lowercase identifier")
    }
    const { name = id, description = "", timeout = null, transform } = row
    if (typeof name !== "string" || !name.trim() || name.length > 80 ||
        typeof description !== "string" || description.length > 240 ||
        (timeout !== null && (!Number.isInteger(timeout) || timeout < 1 || timeout > 60_000)) ||
        typeof transform !== "function") {
      throw new TypeError(`Check the name, description, deadline, and function for ${id}`)
    }
    transforms.set(id, transform)
    return { id, name: name.trim(), description: description.trim(), timeout }
  })
  return {
    definitions: JSON.stringify(definitions),
    async transform(id, text, context) {
      const callback = transforms.get(id)
      const result = await callback(text, freeze(parse(context)))
      if (typeof result !== "string") throw new TypeError("The transformation must return text")
      return result
    },
  }
}
