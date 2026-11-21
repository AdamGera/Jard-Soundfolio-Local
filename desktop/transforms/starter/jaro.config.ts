import { defineJaroConfig } from "@jaro/commands"

export default defineJaroConfig({
  transformations: {
    "trim-lines": {
      name: "Trim lines",
      description: "Remove extra space around each line",
      transform: text => text.split("\n").map(line => line.trim()).join("\n"),
    },
  },
})
