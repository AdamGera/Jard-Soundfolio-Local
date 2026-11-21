import { useInterfaceLanguage } from "@/interface-language"
import { useRef, useState } from "react"
import { Dialog } from "radix-ui"
import { Combobox } from "@base-ui/react/combobox"
import { Check, ChevronDown, Globe2, Search } from "lucide-react"
import catalog from "../../engine/overview_languages.json"
import autonyms from "../../engine/overview_autonyms.json"
import { Button } from "./ui/button"

const nativeNames: Record<string, string> = autonyms
const names: Record<string, string> = catalog
export const languageName = (code: string) => names[code] || code
const auto = { code: "auto", name: "Auto", native: "" }
const items = [auto, ...Object.entries(names).map(([code, name]) => ({ code, name, native: nativeNames[code] ?? "" }))
  .sort((a,b) => a.name.localeCompare(b.name))]
type Item = typeof auto

export function OverviewLanguage({ value, detected, disabled, onSelect }: {
  value: string; detected: string; disabled?: boolean; onSelect: (code: string) => void
}) {
  const { t } = useInterfaceLanguage()
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState<Item | null>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const cancel = useRef<HTMLButtonElement>(null)
  const selected = items.find(i => i.code === value) ?? auto
  const label = value === "auto" ? (detected && detected !== "und" ? `Auto · ${languageName(detected)}` : "Auto") : selected.name
  return <>
    <Combobox.Root<Item> items={items} value={selected} disabled={disabled} open={open} onOpenChange={setOpen}
    itemToStringLabel={item => item.name}
    filter={(item, search) => (item.name + " " + item.native + " " + item.code).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase())}
    onValueChange={item => {
      setOpen(false)
      if (item && item.code !== value) setPending(item)
    }}>
    <Combobox.Trigger ref={trigger} render={<Button size="sm" variant="outline" className="note-language-trigger" />}
      aria-label={`Overview language: ${label}`}><Globe2 /><span className="language-caption">{label}</span><ChevronDown /></Combobox.Trigger>
    <Combobox.Portal><Combobox.Positioner className="note-language-positioner" sideOffset={6} align="start">
      <Combobox.Popup className="note-language-menu">
        <div className="note-language-search"><Search /><Combobox.Input aria-label="Search overview languages"
          placeholder="Search languages" /></div>
        <Combobox.Empty className="note-language-empty">No matching languages</Combobox.Empty>
        <Combobox.List className="note-language-options">{(item: Item) => <Combobox.Item key={item.code} value={item}>
          <span>{item.name}{item.native && item.native.toLocaleLowerCase() !== item.name.toLocaleLowerCase() &&
            <small dir="auto">{item.native}</small>}</span><Combobox.ItemIndicator><Check /></Combobox.ItemIndicator>
        </Combobox.Item>}</Combobox.List>
      </Combobox.Popup>
    </Combobox.Positioner></Combobox.Portal>
    </Combobox.Root>
    <Dialog.Root open={pending !== null} onOpenChange={next => { if (!next) setPending(null) }}>
      <Dialog.Portal>
        <Dialog.Overlay className="note-language-scrim" />
        <Dialog.Content className="note-language-confirm"
          onOpenAutoFocus={event => { event.preventDefault(); cancel.current?.focus() }}
          onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus({ preventScroll: true }) }}>
          <Dialog.Title>{pending?.code === "auto" ? "Use the detected language?" : `Switch to ${pending?.name ?? ""}?`}</Dialog.Title>
          <Dialog.Description>
            Jaro will open the saved overview and timeline in this language, or generate them locally if needed.
          </Dialog.Description>
          <div className="note-language-actions">
            <Dialog.Close asChild><Button ref={cancel} size="sm" variant="outline">{t("Cancel")}</Button></Dialog.Close>
            <Button size="sm" onClick={() => {
              if (pending) onSelect(pending.code)
              setPending(null)
            }}>Switch language</Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>
}
