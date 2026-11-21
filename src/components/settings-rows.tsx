import type { ReactNode } from "react"
import { ChevronDown, type LucideIcon } from "lucide-react"

export function SettingRow({ icon: Icon, title, detail, label = true, find, children }: {
  icon: LucideIcon; title: string; detail?: ReactNode; label?: boolean; find?: string | false; children: ReactNode
}) {
  const Element = label ? "label" : "div"
  return <Element className="dictation-option dictation-setting-row" data-setting-title={find === false ? undefined : title}
    data-setting-description={find || undefined}>
    <span className="dictation-setting-label"><Icon aria-hidden="true" />
      <span><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
    </span>
    {children}
  </Element>
}

export function SettingGroup({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return <section className="dictation-preference-section" aria-labelledby={id} data-setting-group={title}>
    <h3 id={id}>{title}</h3>
    <div className="dictation-preference-group">{children}</div>
  </section>
}

export function SettingDisclosure({ icon: Icon, title, openInitially, onToggle, children }: {
  icon: LucideIcon; title: string; openInitially?: boolean; onToggle?: (open: boolean) => void; children: ReactNode
}) {
  return <details className="dictation-preference-disclosure" data-setting-group={title} open={openInitially}
    onToggle={onToggle && (event => onToggle(event.currentTarget.open))}>
    <summary><Icon aria-hidden="true" /><span>{title}</span><ChevronDown aria-hidden="true" /></summary>
    <div className="dictation-preference-group">{children}</div>
  </details>
}
