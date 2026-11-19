import { useId, useMemo, useRef } from "react"

import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox"

export type Language = { code: string; name: string }

function searchable(value: string) {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().trim()
}

export function LanguagePicker({
  languages,
  selected,
  disabled,
  onChange,
}: {
  languages: Language[]
  selected: Language[]
  disabled: boolean
  onChange: (value: Language[]) => void
}) {
  const inputId = useId()
  const anchor = useRef<HTMLDivElement>(null)
  const nativeNames = useMemo(
    () => new Map(languages.map(({ code }) => [
      code,
      searchable(new Intl.DisplayNames([code], { type: "language" }).of(code) ?? ""),
    ])),
    [languages],
  )

  return (
    <div className="language-picker">
      <label htmlFor={inputId}>Language hints</label>
      <Combobox
        multiple
        autoHighlight
        items={languages}
        value={selected}
        disabled={disabled}
        onValueChange={onChange}
        itemToStringLabel={(language) => language.name}
        itemToStringValue={(language) => language.code}
        filter={(language, query) => {
          const term = searchable(query)
          return searchable(language.name).includes(term)
            || (nativeNames.get(language.code)?.includes(term) ?? false)
            || language.code === term
        }}
      >
        <ComboboxChips ref={anchor} className="language-field" aria-label="Selected language hints">
          {selected.map(({ code, name }) => (
            <ComboboxChip
              key={code}
              removeLabel={`Remove ${name}`}
              aria-description="Press Backspace or Delete to remove"
            >
              {name}
            </ComboboxChip>
          ))}
          <ComboboxChipsInput
            id={inputId}
            className="language-input"
            placeholder={selected.length ? "Add language" : "Auto · search languages"}
            aria-description={selected.length
              ? "Use Left Arrow at the start of the search to focus selected hints."
              : "Leave empty for automatic language detection."}
          />
        </ComboboxChips>
        <ComboboxContent anchor={anchor}>
          <ComboboxEmpty>No matching languages</ComboboxEmpty>
          <ComboboxList>
            {(language: Language) => (
              <ComboboxItem key={language.code} value={language}>
                {language.name}
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
    </div>
  )
}
