# One row per rule

A rule nothing catches is a rule nothing protects. `scripts/mutate.py` turns
each rule off in turn and runs the suite; a rule whose removal breaks no test
has no test. `tests/test_rules_have_evidence.py` counts the rules in the source
against the rows here, so this folder cannot fall behind.

Each row is a file of its own, named after the rule, in a folder that is the
whole path of the file it switches off (CONTRIBUTING, "How a change is made").
`# RULE: a write is kept` in `src/claude_voice/store.py` goes in
`src/claude_voice/store.py/a-write-is-kept.toml` here:

```toml
[[rule]]
name = "a write is kept"
file = "src/claude_voice/store.py"
find = 'if limit_exceeded:'
replace = 'if False:'
```

`find` is a regular expression matching the single line to replace; `replace`
is what replaces it, keeping the file valid. The line's own indentation is
kept, so do not write indentation here.
