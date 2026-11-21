# Jaro transformations

Keep one `jaro.config.ts`, `jaro.config.js`, `jaro.config.mts`, or `jaro.config.mjs`. Export named functions under `transformations`. Each function receives text and available app or browser-host context and returns text or a promise of text. Use stable lowercase IDs and keep unrelated transformations intact.

Use static relative imports within this directory for JavaScript, erasable TypeScript, and JSON. The runtime provides `@jaro/commands`, `node:child_process`, and `node:util`. It has no npm packages or general-purpose Node environment. See `jaro.d.ts` for the supported APIs.

Run `jardctl transforms check --directory .` to validate the config. Run `tsc -p tsconfig.json` for editor type checking if TypeScript is installed. Choose the configuration in **Dictation → Output** and review its version before running its steps. Network and program access require separate user grants. Use `execFile` with the `input` option to pass dictation to a program. Never interpolate dictated text into a shell command.
