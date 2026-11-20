# voice-checks

The check tools every claude-voice plugin shares. Each plugin used to carry its own copy of
them, and the copies drifted apart. They now live here once, are tested here once, and each
plugin depends on one pinned version. The plan is
`no-duplicates-v1`; this is its first step.

## Use it in a plugin

Add it to the plugin's dev group, pinned to a commit of claude-voice:

<!-- not run: it is a line for another repository's pyproject.toml -->
```toml
"voice-checks @ git+https://github.com/roykollensvendsen/claude-voice@<40-character sha>#subdirectory=packages/voice-checks",
```

Then run its checks in the plugin's own folder:

<!-- not run: it needs a plugin checkout -->
```bash
uv run voice-checks temp-files
```

## What it checks

- **`temp-files`.** No code makes a temporary file or folder it may leave behind: a bare
  `mkdtemp`, an `mkstemp` whose name is never removed or moved, or
  `NamedTemporaryFile(delete=False)`. A module under `src/` keeps no temporary path as a
  constant, because it would be read before the test run moves the temporary folder. No test
  calls `monkeypatch.undo()` part way through, because it also undoes the guards that every
  test's fixtures set; `monkeypatch.context()` is the way. These are the plugin rules' R24, and
  the note n6 in no-duplicates-v1.

  A folder that is made in one function and removed in another, such as a test run's own
  folder, can say so on its line or the line above:

  <!-- not run: a line of Python in a plugin's code -->
  ```python
  # temp-files: removed by end_run, remove
  ```

  The check then proves it. Each named function must be in the same module, and must remove
  something: it calls `rmtree`, `unlink`, `rmdir` or `os.remove`, itself or through another
  of the module's functions. A list's `remove` does not count. A marker that names nothing, an
  unknown name, or a function that removes nothing still fails, and says which.

  Two ways of removing are not seen, so their marker is refused even when the removal is real.
  The check errs on the safe side; write the removal so it can see it:
  - **`remove(path)` after `from os import remove`.** Only `os.remove` counts, because a bare
    `remove` cannot be told from another one. Write `os.remove(path)`.
  - **A method, such as `self.cleanup(path)`.** Only the module's own functions, called by
    their bare names, are followed. Move the removal into a module function, and name that.

## Move the pin

<!-- not run: it rewrites the plugin's pyproject.toml -->
```bash
uv run voice-checks bump
uv lock
```

`bump` moves the pin to the newest `voice-checks-v*` release tag of claude-voice. With
`--sha`, it moves it to that commit instead.
