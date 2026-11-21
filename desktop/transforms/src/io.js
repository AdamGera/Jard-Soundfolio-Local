// Retain callbacks inside this runtime. Only the private adapter modules can access these bindings.
const native = globalThis.__jaroIo
delete globalThis.__jaroIo

const callbacks = new Map()
export function deliver(id, error, response) {
  const call = callbacks.get(id)
  if (!call) return
  callbacks.delete(id)
  if (error === null) call.resolve(response)
  else call.reject(error)
}

export default Object.freeze({
  start(kind, request, resolve, reject) {
    const id = native.start(kind, request)
    callbacks.set(id, { resolve, reject })
    return id
  },
  cancel(id) {
    if (callbacks.delete(id)) native.cancel(id)
  },
  log: native.log,
  encode: native.encode,
  decode: native.decode,
})
