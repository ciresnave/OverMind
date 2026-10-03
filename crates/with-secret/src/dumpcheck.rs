// SPDX-License-Identifier: MIT OR Apache-2.0
//! Is this an environment dump? Item 81 (d). ⚠️ A pattern check for ACCIDENTS -
//! the 2026-09-28 incident was a bare `env`. It is trivially bypassed on
//! purpose (`python -c ...`), and is not the gate; the vault is.

const DOTENV_SAFE_SUFFIXES: [&str; 3] = [".example", ".sample", ".template"];

fn is_dotenv(path: &str) -> bool {
    let base = path
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(path)
        .trim_matches(['"', '\'']);
    (base == ".env" || base.starts_with(".env."))
        && !DOTENV_SAFE_SUFFIXES.iter().any(|s| base.ends_with(s))
}

/// A command word's program: quotes, a leading backslash, the directory and
/// `.exe` removed, lowercased. `"printenv"`, `\printenv` and
/// `/usr/bin/printenv` all run `printenv`.
fn image(token: &str) -> String {
    let token = token.trim_matches(['"', '\'']).trim_start_matches('\\');
    let base = token
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or(token)
        .to_ascii_lowercase();
    base.strip_suffix(".exe").unwrap_or(&base).to_string()
}

/// `NAME=VALUE` before a command sets a variable; the command comes after it.
fn is_assignment(token: &str) -> bool {
    token.split_once('=').is_some_and(|(name, _)| {
        !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
    })
}

/// Programs that run a string, an argument list or their stdin as a command.
/// A quoted string anywhere in a command line that has one of these may be
/// code, so quotes are not trusted there (`shell_dump_reason`).
const EVALUATORS: [&str; 27] = [
    "sh",
    "bash",
    "zsh",
    "dash",
    "ksh",
    "fish",
    "busybox",
    "pwsh",
    "powershell",
    "cmd",
    "wsl",
    "env",
    "eval",
    "exec",
    "command",
    "builtin",
    "nohup",
    "time",
    "nice",
    "sudo",
    "doas",
    "su",
    "xargs",
    "iex",
    "invoke-expression",
    "start-process",
    "invoke-command",
];

/// Programs that run the rest of their own command line as a command.
const WRAPPERS: [&str; 15] = [
    "eval",
    "exec",
    "command",
    "builtin",
    "nohup",
    "time",
    "nice",
    "sudo",
    "doas",
    "xargs",
    "iex",
    "invoke-expression",
    "timeout",
    "watch",
    "start-process",
];

/// The quote-unaware split: every separator counts, quoted or not. Fails
/// closed - it can only find MORE commands than a shell would run.
fn loose_segments(cmd: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let chars: Vec<char> = cmd.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        let two: String = chars[i..chars.len().min(i + 2)].iter().collect();
        if two == "&&" || two == "||" {
            out.push(std::mem::take(&mut cur));
            i += 2;
            continue;
        }
        if matches!(c, '|' | ';' | '\n' | '&' | '(' | ')' | '{' | '}' | '`') {
            out.push(std::mem::take(&mut cur));
            i += 1;
            continue;
        }
        cur.push(c);
        i += 1;
    }
    out.push(cur);
    out.into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

fn loose_reason(cmd: &str) -> Option<String> {
    loose_segments(cmd).iter().find_map(|s| segment_reason(s))
}

/// Is this one command (no separators left) an environment dump?
fn segment_reason(seg: &str) -> Option<String> {
    let all: Vec<&str> = seg.split_whitespace().collect();
    let start = all.iter().position(|t| !is_assignment(t))?;
    let toks = &all[start..];
    let first = toks[0];
    let rest = &toks[1..];
    let name = image(first);
    let first_command = |from: &[&str]| {
        from.iter()
            .position(|t| !t.starts_with('-') && !is_assignment(t))
            .map(|i| from[i..].join(" "))
    };
    let blocked = match name.as_str() {
        "env" => match first_command(rest) {
            None => true,
            Some(cmd) => return loose_reason(&cmd),
        },
        "printenv" => true,
        "set" | "export" | "declare" | "typeset" => {
            rest.is_empty() || rest.iter().all(|t| *t == "-p" || *t == "-x")
        }
        "get-childitem" | "gci" | "ls" | "dir" | "get-item" | "gi" => rest.iter().any(|t| {
            t.trim_matches(['"', '\''])
                .to_ascii_lowercase()
                .starts_with("env:")
        }),
        "cat" | "type" | "gc" | "get-content" | "more" | "less" | "head" | "tail" | "bat" => {
            rest.iter().any(|t| is_dotenv(t))
        }
        "cmd"
            if rest
                .first()
                .is_some_and(|f| f.eq_ignore_ascii_case("/c") || f.eq_ignore_ascii_case("/k")) =>
        {
            return loose_reason(rest[1..].join(" ").trim_matches(['"', '\'']));
        }
        "bash" | "sh" | "zsh" | "dash" | "ksh" | "pwsh" | "powershell"
            if rest.first().is_some_and(|f| {
                let f = f.to_ascii_lowercase();
                f == "-c" || f == "-command"
            }) =>
        {
            return loose_reason(rest[1..].join(" ").trim_matches(['"', '\'']));
        }
        w if WRAPPERS.contains(&w) => {
            return first_command(rest).and_then(|cmd| loose_reason(&cmd));
        }
        _ => false,
    };
    blocked.then(|| format!("`{seg}` would print environment variables or a .env file"))
}

fn lists_all_variables(cmd: &str) -> bool {
    cmd.to_ascii_lowercase()
        .contains("[environment]::getenvironmentvariables")
}

/// For `with-secret run`: its child's argv joined with spaces, which has
/// already lost its quoting, so every separator counts (`loose_segments`).
pub fn command_dump_reason(cmd: &str) -> Option<String> {
    if lists_all_variables(cmd) {
        return Some("lists every environment variable".into());
    }
    loose_reason(cmd)
}

/// Which shell parses a hook's command; it decides what quotes and escapes
/// mean (`\` in POSIX shells, a backtick in PowerShell).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Shell {
    Posix,
    PowerShell,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Code,
    /// `( ... )`, `$( ... )`, `@( ... )`: code, ended by `)`.
    Sub,
    /// POSIX `` `...` ``: code, ended by a backtick.
    Tick,
    Single,
    /// POSIX `$'...'`, where `\` escapes.
    AnsiC,
    Double,
    /// PowerShell `@'` here-string, ended by `'@` at the start of a line.
    HereSingle,
    /// PowerShell `@"` here-string, ended by `"@` at the start of a line.
    HereDouble,
}

/// The commands `shell` would run, honouring its quotes. Substitutions
/// (`$(...)`, `(...)`, POSIX backticks), even inside double quotes, come out
/// as commands of their own, and the command around one gets `$()` in its
/// place. `None` when the quoting cannot be followed to the end (an
/// unterminated quote or substitution, or an unmatched `)`): the caller then
/// falls back to `loose_segments`.
fn shell_segments(cmd: &str, shell: Shell) -> Option<Vec<String>> {
    let c: Vec<char> = cmd.chars().collect();
    let at = |i: usize, s: &str| {
        s.chars()
            .enumerate()
            .all(|(k, ch)| c.get(i + k) == Some(&ch))
    };
    let posix = shell == Shell::Posix;
    let esc = if posix { '\\' } else { '`' };
    let mut out: Vec<String> = Vec::new();
    // One buffer per open code context: the outer command keeps building
    // while a substitution's own command is collected separately.
    let mut curs: Vec<String> = vec![String::new()];
    let mut stack: Vec<Mode> = vec![Mode::Code];
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        let mode = *stack.last()?;
        let cur = curs.last_mut()?;
        match mode {
            Mode::Single => {
                cur.push(ch);
                if ch == '\'' {
                    if !posix && c.get(i + 1) == Some(&'\'') {
                        cur.push('\'');
                        i += 2;
                        continue;
                    }
                    stack.pop();
                }
                i += 1;
            }
            Mode::AnsiC => {
                cur.push(ch);
                if ch == '\\' {
                    cur.extend(c.get(i + 1));
                    i += 2;
                    continue;
                }
                if ch == '\'' {
                    stack.pop();
                }
                i += 1;
            }
            Mode::HereSingle => {
                if ch == '\n' && at(i + 1, "'@") {
                    cur.push_str("\n'@");
                    stack.pop();
                    i += 3;
                } else {
                    cur.push(ch);
                    i += 1;
                }
            }
            Mode::Double | Mode::HereDouble => {
                if ch == esc {
                    cur.push(ch);
                    cur.extend(c.get(i + 1));
                    i += 2;
                } else if ch == '$' && c.get(i + 1) == Some(&'(') {
                    cur.push_str("$()");
                    curs.push(String::new());
                    stack.push(Mode::Sub);
                    i += 2;
                } else if posix && ch == '`' {
                    cur.push_str("$()");
                    curs.push(String::new());
                    stack.push(Mode::Tick);
                    i += 1;
                } else if mode == Mode::Double && ch == '"' {
                    cur.push('"');
                    if !posix && c.get(i + 1) == Some(&'"') {
                        cur.push('"');
                        i += 2;
                    } else {
                        stack.pop();
                        i += 1;
                    }
                } else if mode == Mode::HereDouble && ch == '\n' && at(i + 1, "\"@") {
                    cur.push_str("\n\"@");
                    stack.pop();
                    i += 3;
                } else {
                    cur.push(ch);
                    i += 1;
                }
            }
            Mode::Code | Mode::Sub | Mode::Tick => {
                if ch == esc {
                    cur.push(ch);
                    cur.extend(c.get(i + 1));
                    i += 2;
                } else if posix && ch == '$' && c.get(i + 1) == Some(&'\'') {
                    cur.push_str("$'");
                    stack.push(Mode::AnsiC);
                    i += 2;
                } else if ch == '\'' {
                    cur.push(ch);
                    stack.push(Mode::Single);
                    i += 1;
                } else if ch == '"' {
                    cur.push(ch);
                    stack.push(Mode::Double);
                    i += 1;
                } else if !posix && (at(i, "@'\n") || at(i, "@'\r\n")) {
                    cur.push_str("@'");
                    stack.push(Mode::HereSingle);
                    i += 2;
                } else if !posix && (at(i, "@\"\n") || at(i, "@\"\r\n")) {
                    cur.push_str("@\"");
                    stack.push(Mode::HereDouble);
                    i += 2;
                } else if ch == '(' || (ch == '$' && c.get(i + 1) == Some(&'(')) {
                    cur.push_str("$()");
                    curs.push(String::new());
                    stack.push(Mode::Sub);
                    i += if ch == '$' { 2 } else { 1 };
                } else if ch == ')' {
                    if mode != Mode::Sub {
                        return None;
                    }
                    out.extend(curs.pop());
                    stack.pop();
                    i += 1;
                } else if posix && ch == '`' {
                    if mode == Mode::Tick {
                        out.extend(curs.pop());
                        stack.pop();
                    } else {
                        cur.push_str("$()");
                        curs.push(String::new());
                        stack.push(Mode::Tick);
                    }
                    i += 1;
                } else if at(i, "&&") || at(i, "||") {
                    out.push(std::mem::take(cur));
                    i += 2;
                } else if matches!(ch, '|' | ';' | '\n' | '&' | '{' | '}') {
                    out.push(std::mem::take(cur));
                    i += 1;
                } else {
                    cur.push(ch);
                    i += 1;
                }
            }
        }
    }
    if stack.len() != 1 {
        return None;
    }
    out.extend(curs.pop());
    Some(
        out.into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect(),
    )
}

/// May this command's quotes be trusted? Only when its command word is a
/// plain, fixed program name - not a variable or a substitution, whatever
/// it expands to is unknown - and no word in it names an evaluator, which
/// covers one passed as an argument (`find . -exec sh -c '...'`). A
/// PowerShell string or here-string as a pipeline's first element is a
/// value, not a command.
fn quotes_are_trusted(seg: &str, shell: Shell) -> bool {
    let toks: Vec<&str> = seg.split_whitespace().collect();
    let Some(first) = toks.iter().find(|t| !is_assignment(t)) else {
        return true;
    };
    if shell == Shell::PowerShell && (first.starts_with("@'") || first.starts_with("@\"")) {
        return true;
    }
    let word = first.trim_matches(['"', '\'']);
    let fixed = !word.is_empty()
        && word.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '\\' | ':' | '+' | '-')
        });
    fixed
        && !words(seg, shell)
            .iter()
            .any(|w| EVALUATORS.contains(&image(w).as_str()))
}

/// A command's words as its shell reads them: split at whitespace outside
/// quotes, with quotes and escapes removed - so `"sh"` and `$'sh'` are the
/// word `sh`, and `"x | env"` is one word, not `env`.
fn words(seg: &str, shell: Shell) -> Vec<String> {
    let posix = shell == Shell::Posix;
    let esc = if posix { '\\' } else { '`' };
    let mut out = Vec::new();
    let mut cur = String::new();
    // `'` single, `"` double, `$` POSIX `$'...'` (where `\` escapes).
    let mut quote: Option<char> = None;
    let mut chars = seg.chars().peekable();
    while let Some(ch) = chars.next() {
        match quote {
            Some('\'') => {
                if ch != '\'' {
                    cur.push(ch);
                } else if !posix && chars.peek() == Some(&'\'') {
                    cur.push('\'');
                    chars.next();
                } else {
                    quote = None;
                }
            }
            Some('$') => match ch {
                '\\' => cur.extend(chars.next()),
                '\'' => quote = None,
                _ => cur.push(ch),
            },
            Some(_) => {
                if ch == esc {
                    cur.extend(chars.next());
                } else if ch == '"' {
                    quote = None;
                } else {
                    cur.push(ch);
                }
            }
            None => {
                if ch == esc {
                    cur.extend(chars.next());
                } else if posix && ch == '$' && matches!(chars.peek(), Some('\'') | Some('"')) {
                    quote = if chars.next() == Some('\'') {
                        Some('$')
                    } else {
                        Some('"')
                    };
                } else if ch == '\'' || ch == '"' {
                    quote = Some(ch);
                } else if ch.is_whitespace() {
                    if !cur.is_empty() {
                        out.push(std::mem::take(&mut cur));
                    }
                } else {
                    cur.push(ch);
                }
            }
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

/// For the hook: `cmd` as the tool's own shell parses it. Quotes are
/// honoured - `git grep -E 'a|printenv|b'` is one `git` command - only when
/// the quoting can be followed and every command's word is trusted
/// (`quotes_are_trusted`); otherwise the quote-unaware check decides, as
/// before.
pub fn shell_dump_reason(cmd: &str, shell: Shell) -> Option<String> {
    if lists_all_variables(cmd) {
        return Some("lists every environment variable".into());
    }
    match shell_segments(cmd, shell) {
        Some(segs) if segs.iter().all(|s| quotes_are_trusted(s, shell)) => {
            segs.iter().find_map(|s| segment_reason(s))
        }
        _ => loose_reason(cmd),
    }
}

pub fn dump_reason(tool_name: &str, input: &serde_json::Value) -> Option<String> {
    match tool_name {
        "Bash" => shell_dump_reason(input.get("command")?.as_str()?, Shell::Posix),
        "PowerShell" => shell_dump_reason(input.get("command")?.as_str()?, Shell::PowerShell),
        "Read" => {
            let p = input.get("file_path")?.as_str()?;
            is_dotenv(p).then(|| format!("{p} is a .env file"))
        }
        _ => None,
    }
    .map(|why| {
        format!(
            "with-secret hook: blocked - {why}. Secrets are not read from the \
                        environment here; use `with-secret NAME --reason ... -- <command>`."
        )
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn bash(c: &str) -> Option<String> {
        dump_reason("Bash", &json!({"command": c}))
    }
    fn ps(c: &str) -> Option<String> {
        dump_reason("PowerShell", &json!({"command": c}))
    }

    #[test]
    fn env_dumps_are_blocked() {
        for c in [
            "env",
            "env | sort",
            "cd x && env",
            "printenv",
            "printenv PATH",
            "set",
            "export",
            "export -p",
            "declare -x",
            "/usr/bin/env",
            "cmd /c set",
            "bash -c \"env\"",
            "npm run check; env",
            "cmd /c echo hi; env",
        ] {
            assert!(bash(c).is_some(), "{c:?} was allowed");
        }
        for c in [
            "Get-ChildItem env:",
            "gci Env:\\",
            "ls env:*",
            "dir env:",
            "Get-Item env:*",
            "[Environment]::GetEnvironmentVariables()",
            "pwsh -Command \"Get-ChildItem env:\"",
        ] {
            assert!(ps(c).is_some(), "{c:?} was allowed");
        }
    }

    #[test]
    fn ordinary_commands_pass() {
        for c in [
            "env FOO=1 cargo test",
            "env -u HOME ls",
            "cargo test",
            "git status",
            "echo $PATH",
            "set -o pipefail && cargo test",
            "grep -n env src/main.rs",
            "cat README.md",
            "cat .env.example",
        ] {
            assert!(bash(c).is_none(), "{c:?} was blocked");
        }
        for c in ["$env:PATH", "Get-ChildItem src", "Get-Content README.md"] {
            assert!(ps(c).is_none(), "{c:?} was blocked");
        }
    }

    #[test]
    fn dotenv_reads_are_blocked() {
        assert!(bash("cat .env").is_some());
        assert!(bash("cat apps/api/.env.local").is_some());
        assert!(ps("Get-Content .env").is_some());
        assert!(ps("type C:\\x\\.env.production").is_some());
        assert!(dump_reason("Read", &json!({"file_path": "C:\\Projects\\X\\.env"})).is_some());
        assert!(dump_reason(
            "Read",
            &json!({"file_path": "C:\\Projects\\X\\.env.sample"})
        )
        .is_none());
        assert!(dump_reason(
            "Read",
            &json!({"file_path": "C:\\Projects\\X\\src\\env.rs"})
        )
        .is_none());
    }

    // -- quote-aware splitting (PM task, 2026-10-03) ----------------------- //
    //
    // A `|` or `;` inside quotes is not a separator, so a search pattern such
    // as `'a|printenv|b'` is not a dump. ⚠️ SECURITY: quotes are honoured only
    // when nothing in the command could run a quoted string as code; every
    // case below that is expected to DENY is a bypass attempt that must keep
    // failing.

    #[test]
    fn quoted_separators_in_ordinary_commands_pass() {
        for c in [
            "git grep -E 'a|printenv|b'",
            "git grep -n -i -E 'fail.?open|printenv|x' origin/main -- crates",
            "grep -E \"env|set\" src",
            "rg 'printenv|export' -n",
            "echo 'a;printenv'",
            "echo \"x | env\"",
            "git commit -m \"fix: env | set; export\"",
            "echo 'x' && cargo test",
            "cat 'notes.txt' | grep 'env|set'",
            "git commit -m \"use bash | sh here\"",
        ] {
            assert!(bash(c).is_none(), "{c:?} was blocked");
        }
        for c in [
            "Select-String -Pattern 'env:|printenv' file.txt",
            "Write-Output \"Get-ChildItem env:\"",
            "Write-Output 'a;gci env:'",
            "Write-Output 'it''s; gci env:'",
            "Write-Output \"a`\" ; gci env:\"",
            "@'\nGet-ChildItem env:\n'@ | Out-File notes.txt",
        ] {
            assert!(ps(c).is_none(), "{c:?} was blocked");
        }
    }

    #[test]
    fn real_dumps_still_deny_around_quotes() {
        for c in [
            // a separator outside quotes
            "echo a | printenv",
            "\"echo a\" ; printenv",
            "echo 'x'|env",
            "echo 'a|b' | env",
            "git grep foo && printenv",
            "echo 'x' & printenv",
            // an escaped quote is not a quote
            "echo \\\"a|printenv",
            "echo \"a\\\"\" ; printenv",
            // ANSI-C quoting
            "echo $'a\\'' ; printenv",
            // substitution runs code, even inside double quotes
            "echo $(printenv)",
            "echo \"$(printenv)\"",
            "echo `printenv`",
            "echo \"`printenv`\"",
            "echo \"$(echo 'x' | printenv)\"",
            "cat <(printenv)",
            "(printenv)",
            "{ printenv; }",
            // the command word itself disguised
            "\"printenv\"",
            "'env'",
            "\\printenv",
            "A=1 printenv",
            // something that runs a quoted string as code
            "bash -c 'echo a | printenv'",
            "sh -c \"x; printenv\"",
            "\"bash\" -c 'x;printenv '",
            "echo 'a; printenv ' | bash",
            "eval 'x; printenv'",
            "eval printenv",
            "sudo printenv",
            "nohup printenv",
            "echo x | xargs printenv",
            "$(which bash) -c 'a;printenv '",
            "$SHELL -c 'a;printenv '",
            "find . -exec sh -c 'x; printenv ' \\;",
            "find . -exec \"sh\" -c 'x; printenv ' \\;",
            "find . -exec $'sh' -c 'x; printenv ' \\;",
            "find . -exec s\\h -c 'x; printenv ' \\;",
            // an unterminated quote fails closed to the old split
            "echo 'unterminated | printenv",
            "echo \"unterminated ; printenv",
            "echo `unterminated ; printenv",
            "echo $(unterminated ; printenv",
            // .env reads, quoted or not
            "cat \".env\"",
            "cat 'apps/api/.env.local' | grep x",
        ] {
            assert!(bash(c).is_some(), "{c:?} was allowed");
        }
        for c in [
            "Write-Output 'x' | gci env:",
            "Write-Output \"a\"; Get-ChildItem env:",
            "Write-Output 'it''s' ; gci env:",
            "Write-Output `\"a ; gci env:",
            "Write-Output \"$(Get-ChildItem env:)\"",
            "Write-Output \"a $(gci env:) b\"",
            "(Get-ChildItem env:)",
            "& { Get-ChildItem env: }",
            "iex 'Get-ChildItem env:'",
            "Invoke-Expression \"gci env:\"",
            "@'\nGet-ChildItem env:\n'@ | iex",
            "@\"\n$(Get-ChildItem env:)\n\"@ | Out-File x.txt",
            "pwsh -Command \"Write-Output 'a'; gci env:\"",
            "Write-Output 'unterminated ; gci env:",
            "Write-Output \"unterminated ; gci env:",
            "Get-Content '.env'",
        ] {
            assert!(ps(c).is_some(), "{c:?} was allowed");
        }
    }

    /// `with-secret run` checks its child's argv joined with spaces, which has
    /// already lost its quoting, so it keeps the quote-unaware check.
    #[test]
    fn the_argv_check_stays_quote_unaware() {
        assert!(command_dump_reason("git grep -E 'a|printenv|b'").is_some());
        assert!(command_dump_reason("cargo test").is_none());
    }

    #[test]
    fn other_tools_and_bad_input_pass() {
        assert!(dump_reason("Write", &json!({"file_path": ".env"})).is_none());
        assert!(dump_reason("Bash", &json!({})).is_none());
    }
}
