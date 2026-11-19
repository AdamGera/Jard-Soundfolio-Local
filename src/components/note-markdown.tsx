import { memo } from "react"
import Markdown from "react-markdown"
import type { PhrasingContent, Root } from "mdast"
import { visit } from "unist-util-visit"
import { clock } from "@/speaker-activity"

// Only explicit, server-validated citation markers receive a seek target.
function passageCitations() {
  return (tree: Root) => {
    visit(tree, "text", (node, index, parent) => {
      if (!parent || index === undefined || !node.value.includes("⟦")) return
      let from = 0
      const parts: PhrasingContent[] = []
      for (const match of node.value.matchAll(/⟦(\d+(?:\.\d+)?)⟧/g)) {
        parts.push({ type: "text", value: node.value.slice(from, match.index) })
        const seconds = Number(match[1])
        parts.push({ type: "link", url: "#", data: { hProperties: { dataSeconds: seconds } },
          children: [{ type: "text", value: clock(seconds) }] })
        from = match.index + match[0].length
      }
      if (!parts.length) return
      parts.push({ type: "text", value: node.value.slice(from) })
      parent.children.splice(index, 1, ...parts)
      return index + parts.length
    })
  }
}

export const NoteMarkdown = memo(function NoteMarkdown({ text, cite, ready = true }: {
  text: string; cite?: (seconds: number) => void; ready?: boolean
}) {
  return <div className="note-markdown" dir="auto"><Markdown
    allowedElements={["p","ul","ol","li","strong","em","code","pre","blockquote","h1","h2","h3","h4","a","br","hr"]}
    unwrapDisallowed remarkPlugins={cite ? [passageCitations] : []}
    components={{
      a: ({ node, children }) => {
        const seconds = node?.properties?.dataSeconds
        if (!cite || typeof seconds !== "number") return <>{children}</>
        return <button className="chat-citation" dir="ltr" disabled={!ready} onClick={() => cite(seconds)}
          aria-label={`Play transcript at ${clock(seconds)}`}>{children}</button>
      },
    }}
  >{text}</Markdown></div>
})
