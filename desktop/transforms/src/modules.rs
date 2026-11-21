//! Reads local ES modules and records the exact source used by the runtime.

use std::cell::RefCell;
use std::collections::BTreeMap;
use std::fmt::Write;
use std::fs::File;
use std::io::Read;
use std::os::fd::OwnedFd;
use std::path::{Component, Path, PathBuf};
use std::rc::Rc;

use rquickjs::loader::{ImportAttributes, Loader, Resolver};
use rquickjs::{Ctx, Exception, Module};
use rustix::fs::{CWD, FileType, Mode, OFlags, fstat, openat};
use rustix::io::Errno;
use sha2::{Digest, Sha256};

use crate::{capabilities::Review, typescript};

pub const ENTRY: &str = "jaro:entry";
pub const HOST: &str = "jaro:host";
pub const WEB: &str = "jaro:web";
pub const IO: &str = "jaro:io";
const PROCESS: &str = "node:child_process";
const UTIL: &str = "node:util";
const COMMANDS: &str = "@jaro/commands";
const MAX_FILE: usize = 1 << 20;
const MAX_SOURCES: usize = 64;
const MAX_TOTAL: usize = 8 << 20;
const EXTENSIONS: &[&str] = &["js", "mjs", "ts", "mts", "json"];
const SEARCH: &[&str] = &["", ".ts", ".js", ".mts", ".mjs", ".json"];

#[derive(Clone)]
pub struct Workspace {
    root: Rc<OwnedFd>,
    files: Rc<RefCell<Modules>>,
}

#[derive(Default)]
struct Modules {
    sources: BTreeMap<String, String>,
    total: usize,
    sealed: bool,
    programs: bool,
}

impl Workspace {
    pub fn open() -> Result<Self, String> {
        let root = openat(CWD, ".", directory_flags(), Mode::empty())
            .map_err(|_| "The transformation folder is unavailable.")?;
        Ok(Self {
            root: Rc::new(root),
            files: Rc::new(RefCell::new(Modules::default())),
        })
    }

    pub fn seal(&self, entry: &str) -> String {
        let mut files = self.files.borrow_mut();
        files.sealed = true;
        let mut digest = Sha256::new();
        digest.update(b"Jaro ES modules v1\0");
        digest.update(entry.as_bytes());
        digest.update([0]);
        for (name, source) in &files.sources {
            digest.update(name.as_bytes());
            digest.update([0]);
            digest.update(Sha256::digest(source.as_bytes()));
        }
        let mut hex = String::with_capacity(64);
        for byte in digest.finalize() {
            write!(&mut hex, "{byte:02x}").expect("formatting a string cannot fail");
        }
        hex
    }

    pub fn review(&self) -> Review {
        let files = self.files.borrow();
        Review::from_sources(files.programs, files.sources.values())
    }

    pub fn files(&self) -> Vec<String> {
        self.files.borrow().sources.keys().cloned().collect()
    }

    fn open_file(&self, name: &Path) -> Result<OwnedFd, Errno> {
        let mut dir = None;
        let mut parts = name.components().peekable();
        while let Some(Component::Normal(part)) = parts.next() {
            let parent = dir.as_ref().unwrap_or(self.root.as_ref());
            if parts.peek().is_none() {
                let descriptor = openat(
                    parent,
                    part,
                    OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,
                    Mode::empty(),
                )?;
                if FileType::from_raw_mode(fstat(&descriptor)?.st_mode) != FileType::RegularFile {
                    return Err(Errno::INVAL);
                }
                return Ok(descriptor);
            }
            dir = Some(openat(parent, part, directory_flags(), Mode::empty())?);
        }
        Err(Errno::INVAL)
    }

    fn resolve_local(&self, base: &str, name: &str) -> Result<String, &'static str> {
        if name.len() > 240 || name.chars().any(char::is_control) {
            return Err("the import path is invalid");
        }
        let parent = if base == ENTRY {
            Path::new("")
        } else {
            Path::new(base).parent().unwrap_or(Path::new(""))
        };
        let mut relative = PathBuf::new();
        for part in parent.join(name).components() {
            match part {
                Component::Normal(name) => relative.push(name),
                Component::CurDir => {}
                Component::ParentDir if relative.pop() => {}
                _ => return Err("imports must stay inside the transformation folder"),
            }
        }
        if relative.as_os_str().is_empty() {
            return Err("an import needs a file name");
        }
        let search = if relative.extension().is_some() {
            &SEARCH[..1]
        } else {
            SEARCH
        };
        let relative = relative.to_str().ok_or("the import path is invalid")?;
        for extension in search {
            let name = format!("{relative}{extension}");
            let path = Path::new(&name);
            match self.open_file(path) {
                Ok(_) if extension_allowed(path) => return Ok(name),
                Ok(_) => return Err("only JavaScript, TypeScript, and JSON modules are supported"),
                Err(Errno::NOENT) => {}
                Err(_) => {
                    return Err(
                        "the import is not a regular file inside the transformation folder",
                    );
                }
            }
        }
        Err("the module was not found")
    }

    fn source(&self, name: &str) -> Result<String, String> {
        let file = self
            .open_file(Path::new(name))
            .map_err(|_| "the module file is unavailable")?;
        let mut source = String::new();
        File::from(file)
            .take((MAX_FILE + 1) as u64)
            .read_to_string(&mut source)
            .map_err(|_| "the module is unreadable or is not UTF-8")?;
        let mut files = self.files.borrow_mut();
        if files.sealed {
            return Err("modules can only load with the configuration".into());
        }
        if source.len() > MAX_FILE
            || files.sources.len() >= MAX_SOURCES
            || files.total + source.len() > MAX_TOTAL
        {
            return Err("the transformation module set exceeds its size limit".into());
        }
        files.total += source.len();
        files.sources.insert(name.to_owned(), source.clone());
        let extension = Path::new(name).extension().and_then(|part| part.to_str());
        match extension {
            Some("json") => {
                serde_json::from_str::<serde_json::Value>(&source)
                    .map_err(|error| error.to_string())?;
                // Parse as JSON at runtime to retain keys such as "__proto__".
                let literal = serde_json::to_string(&source).map_err(|error| error.to_string())?;
                Ok(format!("export default JSON.parse({literal});"))
            }
            Some("ts" | "mts") => typescript::strip(&source),
            _ => Ok(source),
        }
    }
}

fn directory_flags() -> OFlags {
    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC
}

fn extension_allowed(path: &Path) -> bool {
    path.extension()
        .and_then(|part| part.to_str())
        .is_some_and(|part| EXTENSIONS.contains(&part))
}

impl Resolver for Workspace {
    fn resolve<'js>(
        &mut self,
        ctx: &Ctx<'js>,
        base: &str,
        name: &str,
        _attributes: Option<ImportAttributes<'js>>,
    ) -> rquickjs::Result<String> {
        let result = if self.files.borrow().sealed {
            Err("modules can only load with the configuration")
        } else {
            match name {
                HOST | WEB if base == ENTRY => Ok(name.to_owned()),
                IO if matches!(base, ENTRY | HOST | WEB | PROCESS) => Ok(name.to_owned()),
                PROCESS | "child_process" => {
                    self.files.borrow_mut().programs = true;
                    Ok(PROCESS.into())
                }
                UTIL | "util" => Ok(UTIL.into()),
                COMMANDS => Ok(name.to_owned()),
                name if name.starts_with("./") || name.starts_with("../") => {
                    self.resolve_local(base, name)
                }
                _ => Err("import a relative file or a supported transformation module"),
            }
        };
        result.map_err(|error| {
            Exception::throw_message(ctx, &format!("{base}: cannot import {name:?}: {error}"))
        })
    }
}

impl Loader for Workspace {
    fn load<'js>(
        &mut self,
        ctx: &Ctx<'js>,
        name: &str,
        _attributes: Option<ImportAttributes<'js>>,
    ) -> rquickjs::Result<Module<'js>> {
        let source = match name {
            COMMANDS => "export const defineJaroConfig = config => config;".to_owned(),
            HOST => include_str!("host.js").to_owned(),
            WEB => include_str!("web.js").to_owned(),
            IO => include_str!("io.js").to_owned(),
            PROCESS => include_str!("child_process.js").to_owned(),
            UTIL => include_str!("util.js").to_owned(),
            _ => self
                .source(name)
                .map_err(|error| Exception::throw_syntax(ctx, &format!("{name}: {error}")))?,
        };
        Module::declare(ctx.clone(), name, source)
    }
}
