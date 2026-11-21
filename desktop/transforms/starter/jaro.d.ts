/** Type definitions for the Jaro transformation runtime. */
declare module "@jaro/commands" {
  export interface TransformContext {
    readonly application?: string
    readonly browserHost?: string
    /** Available with the approved page and window context permission. */
    readonly browserUrl?: string
    readonly windowTitle?: string
    readonly source?: "dictation"
  }

  export interface Transformation {
    readonly name?: string
    readonly description?: string
    /** Whole milliseconds between 1 and 60000. */
    readonly timeout?: number
    transform(text: string, context: TransformContext): string | Promise<string>
  }

  export interface Configuration {
    readonly transformations: Readonly<Record<string, Transformation>>
  }

  export function defineJaroConfig<T extends Configuration>(configuration: T): T
}

/** Writes explicitly supplied messages to Jaro's local diagnostic log. */
interface Console {
  log(...values: unknown[]): void
  info(...values: unknown[]): void
  debug(...values: unknown[]): void
  warn(...values: unknown[]): void
  error(...values: unknown[]): void
}
// eslint-disable-next-line no-var -- The host also exposes console on globalThis.
declare var console: Console

// Host APIs. `fetch` and `node:child_process` work only after the person
// allows the corresponding capability for this version in Jaro's Output tab.

type HeadersInit = Headers | readonly (readonly [string, string])[] | Readonly<Record<string, string>>

/** WHATWG Headers. Names are case-insensitive. */
declare class Headers implements Iterable<[string, string]> {
  constructor(init?: HeadersInit)
  append(name: string, value: string): void
  delete(name: string): void
  get(name: string): string | null
  has(name: string): boolean
  set(name: string, value: string): void
  forEach(callback: (value: string, name: string, headers: Headers) => void, thisArg?: unknown): void
  entries(): IterableIterator<[string, string]>
  keys(): IterableIterator<string>
  values(): IterableIterator<string>
  [Symbol.iterator](): IterableIterator<[string, string]>
}

interface RequestInit {
  /** GET by default. */
  readonly method?: string
  readonly headers?: HeadersInit
  /** Strings only; send JSON with `JSON.stringify`. Not allowed with GET or HEAD. */
  readonly body?: string | null
  readonly signal?: AbortSignal | null
}

/** The whole response, read before `fetch` resolves; bodies are not streamed. */
declare class Response {
  constructor(body?: string | null, init?: { status?: number; statusText?: string; headers?: HeadersInit })
  readonly ok: boolean
  readonly status: number
  readonly statusText: string
  readonly headers: Headers
  /** The final URL, after redirects. */
  readonly url: string
  readonly bodyUsed: boolean
  text(): Promise<string>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Match the platform's Response.json type.
  json(): Promise<any>
  arrayBuffer(): Promise<ArrayBuffer>
}

/**
 * WHATWG fetch over http and https. Rejects with a TypeError on network
 * errors (not on HTTP error statuses), or with the signal's reason on abort.
 */
declare function fetch(input: string | { readonly url: string }, init?: RequestInit): Promise<Response>

declare class AbortSignal {
  readonly aborted: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Match the platform's AbortSignal.reason type.
  readonly reason: any
  onabort: ((event: { readonly type: "abort" }) => void) | null
  throwIfAborted(): void
  addEventListener(type: "abort", listener: (event: { readonly type: "abort" }) => void, options?: { once?: boolean }): void
  removeEventListener(type: "abort", listener: (event: { readonly type: "abort" }) => void): void
  static abort(reason?: unknown): AbortSignal
  static timeout(milliseconds: number): AbortSignal
  static any(signals: readonly AbortSignal[]): AbortSignal
}

declare class AbortController {
  readonly signal: AbortSignal
  abort(reason?: unknown): void
}

declare class DOMException extends Error {
  constructor(message?: string, name?: string)
}

declare function setTimeout<Args extends unknown[]>(callback: (...args: Args) => void, delay?: number, ...args: Args): number
declare function clearTimeout(id: number | undefined): void
declare function queueMicrotask(callback: () => void): void

declare module "node:child_process" {
  export interface ExecOptions {
    /** Defaults to the config folder. */
    readonly cwd?: string
    /** Added to a minimal environment with PATH, HOME, locale, and a temp folder. */
    readonly env?: Readonly<Record<string, string | undefined>>
    /** Milliseconds before Jaro ends the program and its children. */
    readonly timeout?: number
    /** Bytes of stdout or stderr to keep; 1 MiB by default, 16 MiB at most. */
    readonly maxBuffer?: number
    readonly signal?: AbortSignal
    /** Written to standard input, which closes after the write. */
    readonly input?: string
  }

  /** Node's error for a failed run: `code` is the exit code, or a string like "ENOENT". */
  export interface ExecException extends Error {
    code?: number | string | null
    killed?: boolean
    signal?: string | null
    cmd?: string
    stdout?: string
    stderr?: string
  }

  export type ExecCallback = (error: ExecException | null, stdout: string, stderr: string) => void

  export interface ChildProcess {
    readonly pid: undefined
    killed: boolean
    /** Ends the program and its children. */
    kill(): boolean
  }

  /** Runs `file` with `args`, no shell. Prefer it for anything containing dictated text. */
  export function execFile(file: string, callback?: ExecCallback): ChildProcess
  export function execFile(file: string, args: readonly string[] | undefined | null, callback?: ExecCallback): ChildProcess
  export function execFile(file: string, options: ExecOptions, callback?: ExecCallback): ChildProcess
  export function execFile(
    file: string,
    args: readonly string[] | undefined | null,
    options: ExecOptions | undefined | null,
    callback?: ExecCallback,
  ): ChildProcess
  export namespace execFile {
    function __promisify__(
      file: string,
      args?: readonly string[] | null,
      options?: ExecOptions | null,
    ): Promise<{ stdout: string; stderr: string }> & { child: ChildProcess }
  }

  /** Runs `command` through `/bin/sh -c` on macOS. */
  export function exec(command: string, callback?: ExecCallback): ChildProcess
  export function exec(command: string, options: ExecOptions | undefined | null, callback?: ExecCallback): ChildProcess
  export namespace exec {
    function __promisify__(
      command: string,
      options?: ExecOptions | null,
    ): Promise<{ stdout: string; stderr: string }> & { child: ChildProcess }
  }

  const childProcess: { readonly exec: typeof exec; readonly execFile: typeof execFile }
  export default childProcess
}

declare module "node:util" {
  type Custom<T> = T extends { __promisify__: infer P } ? P : never
  export function promisify<T extends { __promisify__: (...args: never[]) => unknown }>(original: T): Custom<T>
  export function promisify<Result>(
    original: (callback: (error: unknown, result: Result) => void) => void,
  ): () => Promise<Result>
  export function promisify<A, Result>(
    original: (a: A, callback: (error: unknown, result: Result) => void) => void,
  ): (a: A) => Promise<Result>
  export function promisify<A, B, Result>(
    original: (a: A, b: B, callback: (error: unknown, result: Result) => void) => void,
  ): (a: A, b: B) => Promise<Result>
  export namespace promisify {
    const custom: unique symbol
  }
  const util: { readonly promisify: typeof promisify }
  export default util
}
