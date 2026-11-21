//! Private protocol between QuickJS and the desktop bridge. Native operations stay in the bridge.

use std::cell::RefCell;
use std::collections::HashMap;
use std::io::{self, Write};
use std::rc::Rc;
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use rquickjs::{Array, ArrayBuffer, Ctx, Exception, Function, IntoJs, Object, Value};
use serde::Deserialize;
use serde_json::json;

use crate::capabilities::Grants;

const MAX_SPEC: usize = 256 << 10;
const MAX_PENDING: usize = 64;
const MAX_LOGS: usize = 32;
const MAX_LOG_BYTES: usize = 4096;

#[derive(Clone, Copy)]
enum Operation {
    Fetch,
    Exec,
}

#[derive(Deserialize)]
pub struct Reply {
    id: u32,
    error: Option<String>,
    response: Option<serde_json::Value>,
}

#[derive(Default)]
pub struct Operations {
    grants: Grants,
    loopback: bool,
    console: bool,
    logged: usize,
    denied: Option<&'static str>,
    serial: u32,
    pending: HashMap<u32, Operation>,
}

impl Operations {
    pub fn enable_console(&mut self, enabled: bool) {
        self.console = enabled;
    }

    pub fn allow(&mut self, grants: Grants, loopback: bool) {
        self.grants = grants;
        self.loopback = loopback;
        self.denied = None;
    }

    pub fn denied(&self) -> Option<&'static str> {
        self.denied
    }

    pub fn active(&self) -> bool {
        !self.pending.is_empty()
    }
}

pub fn install<'js>(ctx: &Ctx<'js>, io: Rc<RefCell<Operations>>) -> rquickjs::Result<()> {
    let bindings = Object::new(ctx.clone())?;
    let starting = io.clone();
    bindings.set(
        "start",
        Function::new(
            ctx.clone(),
            move |ctx: Ctx<'js>, kind: String, request: String| -> rquickjs::Result<u32> {
                let mut io = starting.borrow_mut();
                let operation = match kind.as_str() {
                    "fetch" if io.grants.network => Operation::Fetch,
                    "exec" if io.grants.programs && !io.loopback => Operation::Exec,
                    _ => {
                        let reason = match kind.as_str() {
                            "fetch" => {
                                "Allow network requests for this version in Dictation Output."
                            }
                            "exec" if io.loopback => {
                                "Your organization has disabled transformation programs."
                            }
                            "exec" => "Allow programs for this version in Dictation Output.",
                            _ => "Unknown transformation operation.",
                        };
                        io.denied = Some(reason);
                        return Err(Exception::throw_message(&ctx, reason));
                    }
                };
                if request.len() > MAX_SPEC || io.pending.len() >= MAX_PENDING {
                    return Err(Exception::throw_range(
                        &ctx,
                        "The transformation operation limit was exceeded.",
                    ));
                }
                let spec: serde_json::Value = serde_json::from_str(&request).map_err(|_| {
                    Exception::throw_type(&ctx, "Invalid transformation operation.")
                })?;
                io.serial = io.serial.checked_add(1).ok_or_else(|| {
                    Exception::throw_range(&ctx, "Too many transformation operations.")
                })?;
                let id = io.serial;
                send(&json!({"op": kind, "id": id, "spec": spec})).map_err(|_| {
                    Exception::throw_message(&ctx, "The transformation connection closed.")
                })?;
                io.pending.insert(id, operation);
                Ok(id)
            },
        )?,
    )?;
    let logging = io.clone();
    bindings.set(
        "log",
        Function::new(ctx.clone(), move |level: String, message: String| {
            let mut io = logging.borrow_mut();
            if io.console && io.logged < MAX_LOGS {
                io.logged += 1;
                let bounded = &message[..message.floor_char_boundary(MAX_LOG_BYTES)];
                let _ = send(&json!({"op": "console", "level": level, "message": bounded}));
            }
        })?,
    )?;
    bindings.set(
        "cancel",
        Function::new(ctx.clone(), move |id: u32| {
            if io.borrow_mut().pending.remove(&id).is_some() {
                let _ = send(&json!({"op": "cancel", "id": id}));
            }
        })?,
    )?;
    bindings.set(
        "encode",
        Function::new(ctx.clone(), |ctx: Ctx<'js>, value: String| {
            ArrayBuffer::new(ctx, value.into_bytes())
        })?,
    )?;
    bindings.set(
        "decode",
        Function::new(ctx.clone(), |value: ArrayBuffer<'js>| {
            // The bytes are copied before running JavaScript again.
            String::from_utf8_lossy(unsafe { value.as_bytes() }.unwrap_or_default()).into_owned()
        })?,
    )?;
    ctx.globals().set("__jaroIo", bindings)
}

pub fn send(message: &serde_json::Value) -> io::Result<()> {
    let mut output = io::stdout().lock();
    serde_json::to_writer(&mut output, message)?;
    output.write_all(b"\n")?;
    output.flush()
}

pub fn service<'js>(
    ctx: &Ctx<'js>,
    io: &RefCell<Operations>,
    incoming: &Receiver<Reply>,
    deliver: &Function<'js>,
    wait: Duration,
) -> rquickjs::Result<()> {
    let reply = match incoming.recv_timeout(wait) {
        Ok(reply) => reply,
        Err(RecvTimeoutError::Timeout) => return Ok(()),
        Err(RecvTimeoutError::Disconnected) => {
            return Err(Exception::throw_message(
                ctx,
                "The transformation connection closed.",
            ));
        }
    };
    let Some(operation) = io.borrow_mut().pending.remove(&reply.id) else {
        return Ok(());
    };
    if let Some(error) = reply.error {
        deliver.call((reply.id, error, Value::new_null(ctx.clone())))
    } else {
        let response = reply
            .response
            .ok_or_else(|| Exception::throw_message(ctx, "Invalid transformation response."))?;
        let value = match operation {
            Operation::Fetch => response_object(ctx, response)?.into_value(),
            Operation::Exec => process_object(ctx, response)?.into_js(ctx)?,
        };
        deliver.call((reply.id, Value::new_null(ctx.clone()), value))
    }
}

fn binary(ctx: &Ctx<'_>, value: &serde_json::Value) -> rquickjs::Result<Vec<u8>> {
    let invalid = || Exception::throw_message(ctx, "Invalid transformation response.");
    STANDARD
        .decode(value.as_str().ok_or_else(invalid)?)
        .map_err(|_| invalid())
}

fn process_object(ctx: &Ctx<'_>, mut value: serde_json::Value) -> rquickjs::Result<String> {
    for name in ["stdout", "stderr"] {
        value[name] = json!(String::from_utf8_lossy(&binary(ctx, &value[name])?));
    }
    Ok(value.to_string())
}

fn response_object<'js>(ctx: &Ctx<'js>, value: serde_json::Value) -> rquickjs::Result<Object<'js>> {
    let result = Object::new(ctx.clone())?;
    result.set("status", value["status"].as_u64().unwrap_or_default())?;
    result.set(
        "statusText",
        value["statusText"].as_str().unwrap_or_default(),
    )?;
    result.set("url", value["url"].as_str().unwrap_or_default())?;
    let headers = Array::new(ctx.clone())?;
    if let Some(items) = value["headers"].as_array() {
        for (index, item) in items.iter().enumerate() {
            headers.set(
                index,
                vec![
                    item[0].as_str().unwrap_or_default().to_owned(),
                    item[1].as_str().unwrap_or_default().to_owned(),
                ],
            )?;
        }
    }
    result.set("headers", headers)?;
    result.set(
        "body",
        ArrayBuffer::new(ctx.clone(), binary(ctx, &value["body"])?)?,
    )?;
    Ok(result)
}
