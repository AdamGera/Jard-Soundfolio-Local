#[path = "io.rs"]
mod broker;
mod capabilities;
mod modules;
mod typescript;

use std::cell::{Cell, RefCell};
use std::io::{self, BufRead, BufReader, Read};
use std::rc::Rc;
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

use rquickjs::{CatchResultExt, Context, Ctx, Function, Module, Object, Promise, Runtime};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use capabilities::Grants;
use modules::{ENTRY, HOST, IO, WEB, Workspace};

const MEMORY: usize = 64 << 20;
const STACK: usize = 1 << 20;
const TEXT: usize = 48 << 10;
const REQUEST: usize = TEXT * 6 + 4096;
const REPLY: usize = 46 << 20;
const LOAD_TIME: Duration = Duration::from_secs(2);

#[derive(Deserialize)]
struct Request {
    entry: String,
    #[serde(default)]
    log_setup: bool,
    #[serde(default)]
    run: Option<Take>,
}

#[derive(Deserialize)]
struct Take {
    digest: String,
    step: String,
    text: String,
    context: Value,
    #[serde(default)]
    grants: Grants,
    #[serde(default)]
    loopback: bool,
}

#[derive(Deserialize, Serialize)]
struct Definition {
    id: String,
    name: String,
    description: String,
    #[serde(skip_serializing)]
    timeout: Option<u64>,
}

fn main() -> Result<(), io::Error> {
    let result = (|| {
        let mut input = BufReader::new(io::stdin());
        let mut first = Vec::new();
        input
            .by_ref()
            .take((REQUEST + 1) as u64)
            .read_until(b'\n', &mut first)
            .map_err(|_| "Could not read the transformation request.")?;
        if first.len() > REQUEST {
            return Err("The transformation request is too large.".into());
        }
        let request =
            serde_json::from_slice(&first).map_err(|_| "Invalid transformation request.")?;
        let (sender, replies) = mpsc::sync_channel(8);
        thread::spawn(move || {
            loop {
                let mut frame = Vec::new();
                match input
                    .by_ref()
                    .take((REPLY + 1) as u64)
                    .read_until(b'\n', &mut frame)
                {
                    Ok(1..=REPLY) => {}
                    _ => break,
                }
                let Ok(reply) = serde_json::from_slice(&frame) else {
                    break;
                };
                if sender.send(reply).is_err() {
                    break;
                }
            }
        });
        execute(request, &replies)
    })();
    broker::send(&result.unwrap_or_else(|error| json!({ "error": error })))
}

fn execute(request: Request, replies: &Receiver<broker::Reply>) -> Result<Value, String> {
    if !matches!(
        request.entry.as_str(),
        "jaro.config.js" | "jaro.config.mjs" | "jaro.config.ts" | "jaro.config.mts"
    ) {
        return Err("Select a Jaro JavaScript or TypeScript configuration.".into());
    }
    let workspace = Workspace::open()?;
    let runtime = Runtime::new().map_err(|_| "Could not start the transformation runtime.")?;
    runtime.set_memory_limit(MEMORY);
    runtime.set_max_stack_size(STACK);
    let deadline = Rc::new(Cell::new(Instant::now() + LOAD_TIME));
    let interrupt = deadline.clone();
    runtime.set_interrupt_handler(Some(Box::new(move || Instant::now() >= interrupt.get())));
    runtime.set_loader(workspace.clone(), workspace.clone());
    let context = Context::full(&runtime).map_err(|_| "Could not create a JavaScript context.")?;
    context.with(|ctx| {
        let expected = request.run.as_ref().map(|take| take.digest.as_str());
        let operations = Rc::new(RefCell::new(broker::Operations::default()));
        operations.borrow_mut().enable_console(request.log_setup);
        broker::install(&ctx, operations.clone()).map_err(|_| "Could not open transformation operations.")?;
        let loaded = load(&ctx, &workspace, &request.entry, expected, deadline.get())?;
        let review = workspace.review();
        let Some(take) = request.run else {
            return Ok(json!({ "digest": loaded.digest, "transforms": loaded.definitions, "permissions": review, "files": workspace.files() }));
        };
        let grants = review.allowed(take.grants);
        operations.borrow_mut().allow(grants, take.loopback);
        operations.borrow_mut().enable_console(true);
        if take.text.len() > TEXT {
            return Err("This take is too long for a JavaScript transformation.".into());
        }
        let step = loaded.definitions
            .iter()
            .find(|item| item.id == take.step)
            .ok_or("The selected transformation is no longer available.")?;
        let timeout = step.timeout.unwrap_or(if review.network || review.programs { 30_000 } else { 1000 });
        let limit = Instant::now() + Duration::from_millis(timeout);
        deadline.set(limit);
        let context = approved_context(take.context, grants.page_context);
        let result = (|| {
            let run: Function = loaded.host.get("transform")?;
            let pending: Promise = run.call((take.step, take.text, context.to_string()))?;
            settle(&ctx, &pending, &loaded, &operations, replies, limit)?.ok_or(rquickjs::Error::WouldBlock)
        })();
        if Instant::now() >= limit {
            return Err(format!(
                "The transformation exceeded its {} ms deadline.",
                timeout
            ));
        }
        let text: String = result.map_err(|_| {
            operations.borrow().denied().unwrap_or("The JavaScript transformation could not return text.")
        })?;
        if text.len() > TEXT {
            return Err("The transformation returned more than 48 KB of text.".into());
        }
        Ok(json!({ "text": text }))
    })
}

struct Loaded<'js> {
    host: Object<'js>,
    run_timer: Function<'js>,
    deliver: Function<'js>,
    definitions: Vec<Definition>,
    digest: String,
}

fn load<'js>(
    ctx: &Ctx<'js>,
    workspace: &Workspace,
    name: &str,
    expected: Option<&str>,
    limit: Instant,
) -> Result<Loaded<'js>, String> {
    let entry = format!(
        concat!(
            "import {{ register, resetTimers, serviceTimer }} from {host};\n",
            "import {{ deliver }} from {io};\nimport {web};\nimport config from './{entry}';\n",
            "export {{ resetTimers, serviceTimer, deliver }};\nexport const host = register(config);",
        ),
        host = format_args!("{HOST:?}"),
        io = format_args!("{IO:?}"),
        web = format_args!("{WEB:?}"),
        entry = name
    );
    let compiled = Module::declare(ctx.clone(), ENTRY, entry)
        .catch(ctx)
        .map_err(|error| describe(error, limit))?;
    // QuickJS resolves the static module tree during compile-only evaluation.
    // Seal it before user code or any top-level promise runs.
    let digest = workspace.seal(name);
    if expected.is_some_and(|expected| expected != digest) {
        return Err(
            "The transformation configuration changed. Review its new version in Dictation Output."
                .into(),
        );
    }
    let (host, run_timer, deliver, definitions) = (|| {
        let (module, promise) = compiled.eval()?;
        promise.finish::<()>()?;
        while Instant::now() < limit && ctx.execute_pending_job() {}
        // Loading can prepare definitions and local state, but cannot leave work
        // waiting for the grants made available to the transformation itself.
        let reset: Function = module.get("resetTimers")?;
        reset.call::<_, ()>(())?;
        let host: Object = module.get("host")?;
        let definitions: String = host.get("definitions")?;
        let run_timer: Function = module.get("serviceTimer")?;
        let deliver: Function = module.get("deliver")?;
        Ok((host, run_timer, deliver, definitions))
    })()
    .catch(ctx)
    .map_err(|error| describe(error, limit))?;
    if Instant::now() >= limit {
        return Err("The transformation configuration exceeded its loading deadline.".into());
    }
    let definitions = read_definitions(&definitions)?;
    Ok(Loaded {
        host,
        run_timer,
        deliver,
        definitions,
        digest,
    })
}

fn read_definitions(registration: &str) -> Result<Vec<Definition>, String> {
    const MAX_REGISTRATION: usize = 64 << 10;
    let invalid = "Invalid transformation definitions.";
    if registration.len() > MAX_REGISTRATION {
        return Err(invalid.into());
    }
    let definitions: Vec<Definition> = serde_json::from_str(registration).map_err(|_| invalid)?;
    let mut ids = std::collections::HashSet::new();
    if !(1..=32).contains(&definitions.len())
        || definitions.iter().any(|step| {
            !ids.insert(step.id.as_str())
                || !(1..=48).contains(&step.id.len())
                || !step.id.as_bytes()[0].is_ascii_lowercase()
                || !step
                    .id
                    .bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
                || step.name.trim().is_empty()
                || step.name.encode_utf16().count() > 80
                || step.description.encode_utf16().count() > 240
                || step
                    .timeout
                    .is_some_and(|timeout| !(1..=60_000).contains(&timeout))
        })
    {
        return Err(invalid.into());
    }
    Ok(definitions)
}

fn describe(error: rquickjs::CaughtError<'_>, limit: Instant) -> String {
    if Instant::now() >= limit {
        return "The transformation configuration exceeded its loading deadline.".into();
    }
    error.to_string().chars().take(400).collect()
}

fn settle<'js>(
    ctx: &Ctx<'js>,
    promise: &Promise<'js>,
    loaded: &Loaded<'js>,
    operations: &RefCell<broker::Operations>,
    replies: &Receiver<broker::Reply>,
    limit: Instant,
) -> rquickjs::Result<Option<String>> {
    loop {
        if let Some(result) = promise.result() {
            return result.map(Some);
        }
        if Instant::now() >= limit {
            return Ok(None);
        }
        if ctx.execute_pending_job() {
            continue;
        }
        let delay: i64 = loaded.run_timer.call(())?;
        let active = operations.borrow().active();
        if delay < 0 && !active {
            return Ok(None);
        }
        let remaining = limit.saturating_duration_since(Instant::now());
        let wait = if delay < 0 {
            remaining
        } else {
            Duration::from_millis(delay as u64).min(remaining)
        };
        if active {
            broker::service(ctx, operations, replies, &loaded.deliver, wait)?;
        } else {
            thread::sleep(wait);
        }
    }
}

fn approved_context(context: Value, page_context: bool) -> Value {
    let mut metadata = serde_json::Map::new();
    for (key, limit) in [("source", 16), ("application", 240), ("browserHost", 253)] {
        if let Some(value) = context
            .get(key)
            .and_then(Value::as_str)
            .filter(|value| value.len() <= limit)
        {
            metadata.insert(key.into(), json!(value));
        }
    }
    if page_context {
        if let Some(title) = context.get("windowTitle").and_then(Value::as_str)
            .filter(|value| !value.is_empty() && value.len() <= 1024)
        {
            metadata.insert("windowTitle".into(), json!(title));
        }
        if let Some(address) = context.get("browserUrl").and_then(Value::as_str)
            .filter(|value| value.len() <= 4096)
            .and_then(|value| url::Url::parse(value).ok())
            .filter(|url| matches!(url.scheme(), "https" | "http") && url.host().is_some()
                && url.username().is_empty() && url.password().is_none())
        {
            metadata.insert("browserUrl".into(), json!(address.as_str()));
        }
    }
    Value::Object(metadata)
}
