import { createContext, useContext, useEffect, useRef, useState } from "react"
import type { DesktopBridge, DesktopInterfaceLanguage } from "@/desktop"

type Substitutions = Record<string, string | number>
function translate(messages: Record<string, string>, text: string, values?: Substitutions) {
  const translated = Object.hasOwn(messages, text) ? messages[text] : text
  return values ? translated.replace(/\{([A-Za-z]+)\}/g, (match, key) => Object.hasOwn(values, key) ? String(values[key]) : match) : translated
}

export function useDesktopInterfaceLanguage(native: DesktopBridge | undefined) {
  const [language, setLanguage] = useState<DesktopInterfaceLanguage>()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const inFlight = useRef(false)

  useEffect(() => {
    if (!native) return
    let active = true
    let received = false
    const receive = (event: WindowEventMap["native-sdk:jard-interface-language"]) => {
      received = true
      setLanguage(event.detail)
    }
    window.addEventListener("native-sdk:jard-interface-language", receive)
    void native.request("interfaceLanguage", { action: "status" }).then(value => {
      if (active && !received) setLanguage(value)
    }).catch(() => { if (active && !received) setError("Could not load the interface language.") })
    return () => { active = false; window.removeEventListener("native-sdk:jard-interface-language", receive) }
  }, [native])

  const locale = language?.resolved ?? "en"
  useEffect(() => { document.documentElement.lang = locale }, [locale])

  async function selectLanguage(selected: string) {
    if (!native || inFlight.current) return
    inFlight.current = true
    setPending(true)
    setError("")
    try {
      setLanguage(await native.request("interfaceLanguage", { action: "set", selected }))
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not change the interface language.")
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }

  const t = (source: string, values?: Substitutions) => translate(language?.messages ?? {}, source, values)
  return { language, locale, pending, error, t, selectLanguage }
}

export const InterfaceLanguage = createContext<ReturnType<typeof useDesktopInterfaceLanguage>>({
  language: undefined, locale: "en", pending: false, error: "", t: (source, values) => translate({}, source, values),
  selectLanguage: async () => {},
})

export function useInterfaceLanguage() { return useContext(InterfaceLanguage) }
