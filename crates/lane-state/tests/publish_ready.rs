// SPDX-License-Identifier: MIT OR Apache-2.0
//! What must be true of this crate before it is published to crates.io, as a
//! test, so a later edit cannot quietly undo it. Reads only files inside the
//! crate, so it also passes from the packaged tarball.

const MANIFEST: &str = include_str!("../Cargo.toml");
const README: &str = include_str!("../README.md");
const MIT: &str = include_str!("../LICENSE-MIT");
const APACHE: &str = include_str!("../LICENSE-APACHE");

/// The value of `key = "..."` in the `[package]` table.
fn package_value(key: &str) -> Option<String> {
    let package = MANIFEST
        .split("\n[")
        .find(|t| t.starts_with("package]") || t.starts_with("[package]"))?;
    package
        .lines()
        .find_map(|l| l.strip_prefix(&format!("{key} = ")))
        .map(|v| v.trim().trim_matches('"').to_string())
}

#[test]
fn the_manifest_carries_what_crates_io_asks_for() {
    assert_eq!(package_value("name").as_deref(), Some("lane-state"));
    assert!(
        package_value("description").is_some_and(|d| d.len() > 40),
        "a real description"
    );
    assert_eq!(package_value("readme").as_deref(), Some("README.md"));
    // the licence and repository are the workspace's, inherited
    assert!(MANIFEST.contains("license.workspace = true"));
    assert!(MANIFEST.contains("repository.workspace = true"));
    let rv = package_value("rust-version").expect("rust-version declared");
    assert!(rv.starts_with("1."), "{rv}");
}

#[test]
fn keywords_and_categories_fit_crates_io_limits() {
    let list = |key: &str| -> Vec<String> {
        let line = MANIFEST
            .lines()
            .find(|l| l.starts_with(&format!("{key} = [")))
            .unwrap_or_else(|| panic!("{key} missing"));
        line.split('[')
            .nth(1)
            .unwrap()
            .trim_end_matches(']')
            .split(',')
            .map(|s| s.trim().trim_matches('"').to_string())
            .filter(|s| !s.is_empty())
            .collect()
    };
    let kw = list("keywords");
    assert!(
        (1..=5).contains(&kw.len()),
        "{kw:?}: crates.io allows at most 5"
    );
    assert!(kw.iter().all(|k| k.len() <= 20), "{kw:?}");
    let cats = list("categories");
    assert!(!cats.is_empty() && cats.len() <= 5, "{cats:?}");
}

/// Nothing in the dependency table may be a path or git dependency: the crate
/// must resolve from crates.io alone.
#[test]
fn the_crate_depends_on_published_crates_only() {
    let deps: Vec<&str> = MANIFEST
        .split("\n[dependencies]")
        .nth(1)
        .unwrap()
        .split("\n[")
        .next()
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .collect();
    assert!(deps.len() >= 4, "{deps:?}");
    for d in deps {
        assert!(
            !d.contains("path =") && !d.contains("git ="),
            "not publishable: {d}"
        );
    }
}

/// The README states the honest scope: what it is for, who it is for, and that
/// it is pre-1.0.
#[test]
fn the_readme_states_the_scope_honestly() {
    for needle in [
        "lane-state",
        "Claude Code",
        "pre-1.0",
        "not a general-purpose",
        "MIT OR Apache-2.0",
    ] {
        assert!(README.contains(needle), "README does not say {needle:?}");
    }
}

/// Both licence texts ship inside the crate (the dual licence is a choice of
/// either, and the tarball must carry both).
#[test]
fn both_licence_texts_are_in_the_crate() {
    assert!(MIT.contains("Permission is hereby granted, free of charge"));
    assert!(APACHE.contains("Apache License") && APACHE.contains("Version 2.0"));
}
