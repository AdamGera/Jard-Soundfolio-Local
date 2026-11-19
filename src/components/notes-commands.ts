import { Extension, type Editor } from "@tiptap/core"
import Suggestion, { exitSuggestion, type SuggestionProps } from "@tiptap/suggestion"

export const NOTES_COMMAND_EVENT = "soundfolio-notes-command"

function requestAction(editor: Editor, action: "dictate" | "link") {
  editor.view.dom.dispatchEvent(new CustomEvent(NOTES_COMMAND_EVENT, { detail: action }))
}

const commands = [
  { label: "Heading", icon: "H", search: "title", run: (editor: Editor) => editor.commands.setHeading({ level: 2 }) },
  { label: "Bullet list", icon: "•", search: "bullets unordered", run: (editor: Editor) => editor.commands.toggleBulletList() },
  { label: "Checklist", icon: "☑", search: "checkbox task todo", run: (editor: Editor) => editor.commands.toggleTaskList() },
  { label: "Numbered list", icon: "1.", search: "ordered", run: (editor: Editor) => editor.commands.toggleOrderedList() },
  { label: "Quote", icon: "“", search: "blockquote", run: (editor: Editor) => editor.commands.toggleBlockquote() },
  { label: "Link", icon: "↗", search: "url hyperlink", run: (editor: Editor) => requestAction(editor, "link") },
  { label: "Dictate", icon: "●", search: "microphone voice record", run: (editor: Editor) => requestAction(editor, "dictate") },
]
type Command = (typeof commands)[number]

export const NotesCommands = Extension.create<{ link: boolean; checklist: boolean }>({
  name: "notesCommands",
  addOptions() { return { link: true, checklist: true } },
  // Handle command navigation before the editor's list and Enter shortcuts.
  priority: 110,
  addKeyboardShortcuts() {
    return {
      "Mod-k": () => {
        if (!this.options.link) return false
        exitSuggestion(this.editor.view)
        requestAction(this.editor, "link")
        return true
      },
    }
  },
  addProseMirrorPlugins() {
    return [Suggestion<Command, Command>({
      editor: this.editor,
      char: "/",
      startOfLine: true,
      allow: ({ state, range }) => state.doc.resolve(range.from).parentOffset === 0,
      allowSpaces: true,
      floatingUi: { strategy: "fixed" },
      offset: { mainAxis: 8 },
      decorationClass: "notes-command-query",
      items: ({ query, editor }) => commands.filter(item =>
        (item.label !== "Link" || this.options.link) &&
        (item.label !== "Checklist" || this.options.checklist) &&
        (item.label !== "Dictate" || editor.view.dom.closest('[data-can-dictate="true"]')) &&
        (item.label.toLowerCase() + " " + item.search).includes(query.trim().toLowerCase())),
      command: ({ editor, range, props }) => {
        editor.chain().focus().deleteRange(range).run()
        props.run(editor)
      },
      render: () => {
        const list = document.createElement("div")
        list.className = "notes-command-menu"
        list.id = `notes-commands-${crypto.randomUUID()}`
        list.setAttribute("role", "listbox")
        list.setAttribute("aria-label", "Insert in My notes")
        let current: SuggestionProps<Command, Command> | undefined
        let unmount: (() => void) | undefined
        let selected = 0

        function highlight(index: number) {
          selected = index
          for (const [position, item] of Array.from(list.children).entries()) {
            item.setAttribute("aria-selected", String(position === selected))
          }
          const item = list.children[selected]
          if (item?.id) current?.editor.view.dom.setAttribute("aria-activedescendant", item.id)
        }

        function update(props: SuggestionProps<Command, Command>) {
          current = props
          list.replaceChildren()
          props.items.forEach((item, index) => {
            const row = document.createElement("button")
            row.type = "button"
            row.tabIndex = -1
            row.id = `${list.id}-${index}`
            row.setAttribute("role", "option")
            const icon = document.createElement("span")
            icon.setAttribute("aria-hidden", "true")
            icon.textContent = item.icon
            row.append(icon, document.createTextNode(item.label))
            row.onmousedown = event => event.preventDefault()
            row.onpointermove = () => highlight(index)
            row.onclick = () => current?.command(item)
            list.append(row)
          })
          if (!props.items.length) {
            const empty = document.createElement("div")
            empty.className = "notes-command-empty"
            empty.textContent = "No matching commands"
            list.append(empty)
            props.editor.view.dom.removeAttribute("aria-activedescendant")
            return
          }
          highlight(0)
        }

        return {
          onStart(props) {
            update(props)
            props.editor.view.dom.setAttribute("aria-controls", list.id)
            unmount = props.mount(list)
          },
          onUpdate: update,
          onKeyDown({ event }) {
            if (event.isComposing || !current?.items.length) return false
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              const count = current.items.length
              highlight((selected + count + (event.key === "ArrowDown" ? 1 : -1)) % count)
              return true
            }
            if (event.key === "Enter") {
              current.command(current.items[selected])
              return true
            }
            return false
          },
          onExit({ editor }) {
            editor.view.dom.removeAttribute("aria-activedescendant")
            editor.view.dom.removeAttribute("aria-controls")
            unmount?.()
            current = undefined
          },
        }
      },
    })]
  },
})
