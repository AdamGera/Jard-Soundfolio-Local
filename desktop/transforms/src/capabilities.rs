use std::collections::BTreeSet;
use std::path::Path;
use std::sync::LazyLock;

use regex::Regex;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Grants {
    pub network: bool,
    pub programs: bool,
    #[serde(rename = "pageContext")]
    pub page_context: bool,
}

#[derive(Default, Serialize)]
pub struct Review {
    pub network: bool,
    pub programs: bool,
    pub hosts: Vec<String>,
    pub commands: Vec<String>,
    pub partial: bool,
}

static FETCH: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bfetch\b").unwrap());
static FETCH_LITERAL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"\bfetch\s*\(\s*["'`](https?://[^/"'`\s?#$]+)"#).unwrap());
static EXEC: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\bexec(?:File)?\b").unwrap());
static EXEC_LITERAL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"\bexec(?:File)?\s*\(\s*["'`]([^"'`$\r\n]+)"#).unwrap());

static PROCESS_IMPORT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"import\s*\{[^}]*\}\s*from\s*["'](?:node:)?child_process["']"#).unwrap()
});

impl Review {
    pub fn from_sources<'a>(programs: bool, sources: impl Iterator<Item = &'a String>) -> Self {
        let mut result = Self {
            programs,
            ..Self::default()
        };
        let mut hosts = BTreeSet::new();
        let mut commands = BTreeSet::new();
        for source in sources {
            let fetch = FETCH.find_iter(source).count();
            result.network |= fetch != 0;
            let host_count = FETCH_LITERAL
                .captures_iter(source)
                .filter_map(|literal| {
                    url::Url::parse(&literal[1])
                        .ok()?
                        .host_str()
                        .map(str::to_owned)
                })
                .inspect(|host| {
                    hosts.insert(display_name(host));
                })
                .count();
            result.partial |= host_count < fetch;
            if programs {
                let code = PROCESS_IMPORT.replace_all(source, "");
                let count = EXEC_LITERAL
                    .captures_iter(source)
                    .filter_map(|literal| {
                        let path = literal[1].split_whitespace().next()?;
                        Path::new(path).file_name()?.to_str().map(display_name)
                    })
                    .inspect(|program| {
                        commands.insert(program.clone());
                    })
                    .count();
                result.partial |= count < EXEC.find_iter(&code).count();
            }
        }
        result.partial |= hosts.len() > 12 || commands.len() > 12;
        result.partial |= (result.network && hosts.is_empty()) || (programs && commands.is_empty());
        result.hosts = hosts.into_iter().take(12).collect();
        result.commands = commands.into_iter().take(12).collect();
        result
    }

    pub fn allowed(&self, requested: Grants) -> Grants {
        Grants {
            network: self.network && requested.network,
            programs: self.programs && requested.programs,
            page_context: requested.page_context,
        }
    }
}

fn display_name(value: &str) -> String {
    value
        .chars()
        .filter(|char| !char.is_control())
        .take(64)
        .collect()
}
