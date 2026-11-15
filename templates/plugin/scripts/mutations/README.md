# One row per rule

A rule nothing catches is a rule nothing protects. `scripts/mutate.py` turns each rule off in
turn and runs the test file that holds the rule's own test. A rule whose removal turns no test
red has no test. `tests/test_rules_have_evidence.py` counts the rules in the code against the
rows here, so this folder cannot fall behind.

Each row is a file of its own, named after the rule. It sits in a folder that is the whole path
of the file it switches off. So `# RULE: a read is kept` in `src/gizmo_mcp/service.py` goes in
`src/gizmo_mcp/service.py/a-read-is-kept.toml` here:

```toml
[[rule]]
name = "a read is kept"
file = "src/gizmo_mcp/service.py"
find = 'self\._kept = reading'
replace = 'pass'
```

`find` is a regular expression matching the one line to replace. `replace` is the whole new
line, and must leave the file valid code. The line's own indentation is kept, so write none
here. Two pull requests that each add a rule never meet here, unless they add the same one.

A rule in `scripts/` is proven the same way, the mutation runner's own included. The runner has
loaded its own code before it switches a line off, and the suite it starts loads the
switched-off file.

A rule inside repair(), or in any function in the runner it calls by name, could not be proven
like this, since a killed run repairs with the switched-off code.
`test_no_rule_lives_inside_repair` refuses one there, following every call by name, however deep.

A test that runs the runner must point `RECOVERY` at its own temporary file.
`tests/conftest.py` fails the whole run if the real recovery file changed.

When the code marks rules and this folder has no rows, the run fails. A folder emptied by
mistake would otherwise leave every rule unproven and the run green.
