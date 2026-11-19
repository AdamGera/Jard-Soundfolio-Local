import { Combobox as Primitive } from "@base-ui/react/combobox"
import { cn } from "cn"
import { CheckIcon, XIcon } from "lucide-react"

import { Button } from "@/components/ui/button"

const Combobox = Primitive.Root

function ComboboxContent({
  anchor,
  ...props
}: Primitive.Popup.Props & Pick<Primitive.Positioner.Props, "anchor">) {
  return (
    <Primitive.Portal>
      <Primitive.Positioner anchor={anchor} sideOffset={7} align="start" className="isolate z-50">
        <Primitive.Popup
          data-slot="combobox-content"
          className="group/combobox-content w-(--anchor-width) max-w-(--available-width) overflow-hidden rounded-3xl border border-border bg-popover text-popover-foreground shadow-lg"
          {...props}
        />
      </Primitive.Positioner>
    </Primitive.Portal>
  )
}

function ComboboxList(props: Primitive.List.Props) {
  return (
    <Primitive.List
      className="max-h-[min(16rem,var(--available-height))] overflow-y-auto overscroll-contain p-1.5 data-empty:p-0"
      {...props}
    />
  )
}

function ComboboxItem({ children, ...props }: Primitive.Item.Props) {
  return (
    <Primitive.Item
      className="relative flex cursor-default items-center rounded-2xl py-2 pr-8 pl-3 text-sm font-medium outline-hidden select-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
      {...props}
    >
      {children}
      <Primitive.ItemIndicator className="pointer-events-none absolute right-2.5">
        <CheckIcon className="size-4" />
      </Primitive.ItemIndicator>
    </Primitive.Item>
  )
}

function ComboboxEmpty(props: Primitive.Empty.Props) {
  return (
    <Primitive.Empty
      className="hidden px-3 py-3 text-center text-sm text-muted-foreground group-data-empty/combobox-content:block"
      {...props}
    />
  )
}

function ComboboxChips({ className, ...props }: Primitive.Chips.Props) {
  return (
    <Primitive.Chips
      className={cn("flex min-h-9 flex-wrap items-center gap-1.5 rounded-3xl border px-2.5 py-1.5", className)}
      {...props}
    />
  )
}

function ComboboxChip({
  children,
  removeLabel,
  ...props
}: Primitive.Chip.Props & { removeLabel: string }) {
  return (
    <Primitive.Chip data-slot="combobox-chip" className="language-chip" {...props}>
      {children}
      <Primitive.ChipRemove
        aria-label={removeLabel}
        render={<Button variant="ghost" size="icon-xs" className="language-chip-remove" />}
      >
        <XIcon />
      </Primitive.ChipRemove>
    </Primitive.Chip>
  )
}

function ComboboxChipsInput({ className, ...props }: Primitive.Input.Props) {
  return (
    <Primitive.Input
      className={cn("min-w-16 flex-1 outline-none", className)}
      {...props}
    />
  )
}

export {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxItem,
  ComboboxList,
}
