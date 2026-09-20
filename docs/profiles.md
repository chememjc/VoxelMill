# Profile library

`voxelmill.profiles` turns a printer or resin reference — on the command line
or in the editor — into a resolved file, and adds discovery, provenance,
comparison and writing on top of `config.resolve_settings`. It never relaxes
that resolver: discovery only *locates* a `.ptr`/`.res` file, and
`resolve_settings` remains the single validator and the single resolution
order.

## Search path and priority

`--printer`/`--resin` (and the editor's profile selectors) can name a bare
library identifier instead of a path. An identifier is looked up in four
layers, highest priority first:

| Layer | Location |
| --- | --- |
| `VOXELMILL_PROFILE_PATH[i]` | Each colon-separated directory in `$VOXELMILL_PROFILE_PATH`, in order. Empty entries are skipped. |
| `user` | `$XDG_CONFIG_HOME/voxelmill/profiles`, or `~/.config/voxelmill/profiles` if that variable is unset or blank. |
| `system` | `/etc/voxelmill/profiles`. |
| `builtin` | The profiles packaged with the installed VoxelMill (`voxelmill/data`). |

A directory that does not exist is still listed — `profile list` and the
editor's search-path line show where a profile *would* be found, not only
where one was.

Printer and resin profiles have separate identifier namespaces (a `.ptr` and
a `.res` can share an `id` without conflict). Within one kind, a higher layer
shadows a lower one that declares the same `id`: the higher file is used, and
`profile list` names every shadowed file rather than hiding it, because a
silently preferred profile is the same hazard as a silently preferred
setting. A malformed profile keeps its filename stem as its identifier and is
still listed, with its parse error attached, rather than being dropped from
the listing.

```sh
.venv/bin/voxelmill profile list
```

```json
{
  "schema_version": 1,
  "search_path": [
    {"layer": "user", "directory": "/home/…/.config/voxelmill/profiles", "exists": false},
    {"layer": "system", "directory": "/etc/voxelmill/profiles", "exists": false},
    {"layer": "builtin", "directory": "/…/voxelmill/data", "exists": true}
  ],
  "profiles": [
    {"id": "mars5-ultra", "name": "Elegoo Mars 5 Ultra", "kind": "printer",
     "path": "/…/voxelmill/data/mars5-ultra.ptr", "layer": "builtin",
     "error": null, "shadows": []},
    "…"
  ]
}
```

(No `VOXELMILL_PROFILE_PATH` entries appear above because the variable is
unset in this shell; each set entry gets its own row, indexed from 0.)

## Path or identifier: `--printer`/`--resin`

Every command that resolves settings, and the editor's profile selectors,
accept either an explicit filesystem path or a bare library identifier for
`--printer`/`--resin`. The two are told apart by shape (`_looks_like_path`),
never by trying one and falling back to the other:

- A reference is a **path** if it contains a path separator, starts with
  `.`, or its suffix (case-insensitively) matches the profile kind's
  extension (`.ptr` for a printer, `.res` for a resin). A path reference is
  never searched for in the library — `~` is expanded, and a missing file is
  the whole error.
- Anything else is a **library identifier**. It is never guessed at as a
  path, even if a same-named file happens to sit in the current directory.

One consequence worth knowing: a bare filename that ends in `.ptr`/`.res`
with no directory component — `mars5-ultra.ptr` typed from a directory other
than `profiles/` — is still a *path* reference by this rule, resolved
relative to the current directory, and fails with "No printer profile at
mars5-ultra.ptr" rather than falling through to the identifier `mars5-ultra`.
Drop the extension to get the library lookup:

```sh
.venv/bin/voxelmill profile --printer mars5-ultra          # library identifier, found
.venv/bin/voxelmill profile --printer mars5-ultra.ptr       # path, fails unless it exists here
.venv/bin/voxelmill profile --printer profiles/mars5-ultra.ptr  # path, found
```

An unresolvable identifier reports the identifiers that *were* found:

```
No printer profile named 'bogus-id'; known identifiers: ['mars5-ultra']
```

## `voxelmill profile [show|list|diff|save]`

```sh
voxelmill profile --printer mars5-ultra --resin sunlu-abs-like-gray
voxelmill profile list [--kind printer|resin]
voxelmill profile diff --printer mars5-ultra --against defaults
voxelmill profile diff --against sunlu-abs-like-gray --kind resin
voxelmill profile save --output my-printer.ptr [--name "My Mars"] [--hardware-only]
```

The action is the first positional argument; default is `show`.

- **`show`** (default) — prints the same resolved-settings payload
  `voxelmill profile` always has, plus `resin_usage_per_ml`: `resin_usage`
  called with exactly 1000 mm³ (1 mL), so `mass_g`/`cost` there are the
  per-milliliter figures, `null` when the resin profile supplies no density
  or price. `--provenance` adds a `provenance` key; see below.
- **`list`** — the library report above (`search_path`, `profiles`).
  `--kind printer` or `--kind resin` restricts it to one kind.
- **`diff`** — `--against PROFILE` (required) is a profile identifier, a
  path, or the literal word `defaults`. The left side is the settings
  resolved from the command's own `--printer`/`--resin`/`--set` stack; the
  right side is `defaults` resolved with no printer or resin file, or the
  named profile resolved *alone* — a printer profile against the built-in
  defaults, or a resin profile against the built-in default printer, since a
  resin's process blocks are printer-scoped and cannot be resolved without
  one. `--kind` selects which kind `--against` names when it is not a path
  (default `printer`). `differences` is leaf-by-leaf; a leaf present on only
  one side is reported as `"<absent>"` rather than omitted, because a
  missing key and an equal key are different answers.
- **`save`** — `--output PATH` (required) writes the command's resolved
  settings as a `.ptr`. `--name` overrides `printer.name` in the written file.
  `--hardware-only` writes only the `printer` table so process, support, and
  other settings inherit when the file is used. The writer validates a staged
  file before replacing the destination and reports its `omitted_sections`.

`--printer`/`--resin` accept identifiers here exactly as described above.

## Provenance

`--provenance` (on `profile show`) reports which resolution layer last set
each resolved leaf value. It is computed by calling `resolve_settings`
repeatedly with one more layer applied each time —

```
defaults → resolve_settings(printer, None, None)
         → resolve_settings(printer, resin, None)
         → resolve_settings(printer, resin, overrides)
```

— and, for every leaf, walking that sequence and recording the last stage at
which the value changed from the one before it. The reported layer names are
`default`, `printer`, `resin`, `override`. Because this reuses
`resolve_settings` itself rather than reimplementing its merge order, the
provenance answer cannot drift from the settings it describes — the two are
computed by the exact same function calls.

One consequence: a value a profile *sets* but which happens to equal the
default is still reported as `default`, since provenance tracks the last
stage a value *changed*, not the last stage that named it. The supplied
example profiles currently match the built-in defaults field-for-field, so
`voxelmill profile --printer profiles/mars5-ultra.ptr --provenance` reports
`"default"` everywhere; editing one field in a copy of the profile is the
way to see `"printer"` or `"resin"` appear.

These provenance layer names (`default`/`printer`/`resin`/`override`) are a
different axis from the search-path layer names above
(`VOXELMILL_PROFILE_PATH`/`user`/`system`/`builtin`): the search-path layers
say *where a profile file was found on disk*; the provenance layers say
*which stage of settings resolution last touched a value*. Both happen to be
called "layer" because each is a step in a priority order, but they answer
different questions.

## `profile save`: round trip and the TOML writer's one limitation

`save_printer_profile` writes the `.ptr`, then immediately reads it back
with `resolve_settings` and requires it to resolve to exactly the settings it
was written from (`diff_settings` between the two must be empty). A profile
that would not reload identically is a silent configuration change, so this
check is part of saving, not a test that could be skipped; a mismatch fails
the save with the differences included in the error.

The TOML writer's one limitation: TOML has no null literal. The only nullable
field in the resolved schema is `resources.scratch_dir` (default `null`,
meaning "use the system default"). A `null` value is written as a comment
(`# scratch_dir is unset`) instead of a key, and on reload the loader falls
back to the same default — which is `null` — so this specific field's round
trip is correct today only because its default already matches what a
`null` means. There is no other nullable field in the current schema.

## `voxelmill resin [list|show|save|bind]`

```sh
voxelmill resin list
voxelmill resin show sunlu-abs-like-gray
voxelmill resin bind sunlu-abs-like-gray --from mars5-ultra --to my-other-printer \
  --output profiles/sunlu-for-my-other-printer.res
voxelmill resin save --printer mars5-ultra --resin sunlu-abs-like-gray \
  --output profiles/my-resin.res --name "My resin"
```

The action is the first positional argument; default is `list` (`profile`'s
default is `show` — the two commands differ here). A second positional
argument, or `--resin`, names the resin identifier or path; `bind` and `show`
both accept it either way.

- **`list`** — the library report restricted to resin profiles
  (`voxelmill resin list` is `voxelmill profile list --kind resin` plus a
  different `command` label).
- **`show`** — reads the resin file directly (not through
  `resolve_settings`, so it works without a matching printer) and reports
  `resin` identity, `bound_printers` (the sorted printer ids its
  `processes` table covers), and the full `processes` table.
- **`save`** — writes a standalone `.res` from the command's fully resolved
  settings. It contains the resin metadata and the current printer's
  `process` and `support` blocks, keyed by that printer id. `--name` changes
  only the saved resin display name. The file is validated against the current
  printer before publication; copied process and support values are a starting
  point, not a hardware calibration.
- **`bind`** — copies one printer's process block onto another printer id
  within the same resin file and writes the result to `--output`
  (required). `--from PRINTER_ID` names the source; if omitted, the resin
  must bind exactly one printer or the command fails naming the ones it does
  bind. `--to PRINTER_ID` (required) names the destination and must differ
  from `--from`.

Be explicit about what `bind` does and does not establish:

- The copy is **verbatim** — the entire `process` and `support` blocks are
  deep-copied onto the new printer id unchanged. Nothing is rescaled or
  re-derived for the new machine.
- The resulting exposure times and support geometry are a **starting
  point**, **not a calibration for the target machine**. The command's own
  output says so in a `calibration` field: `"copied verbatim from
  <source>; exposures are a starting point, not a calibration for the
  target machine"`.
- `bind` **refuses to overwrite its own source file** — if `--output`
  resolves to the same path as the resin profile being read, it fails with
  `"Refusing to overwrite the source resin profile; choose another
  --output"` rather than silently appending to the file it just read.

The written file is reloaded and re-validated through the same reader before
`bind` returns, so a copy that cannot actually be parsed back fails at bind
time rather than at the next slice.

## The editor: Configuration > Profile library...

**Configuration → Profile library...** opens `ProfileLibraryDialog`
(`src/voxelmill/gui/profiles.py`), which calls the same `voxelmill.profiles`
functions the CLI does — a printer or resin chosen in the editor and one
chosen with `--printer`/`--resin` cannot resolve to different settings. The
dialog shows the search-path line, a table of every discovered profile
(kind, id, name, layer, path, and shadowed files or a parse error), and two
combo boxes to pick a printer and a resin profile from that table.

| Button | Effect |
| --- | --- |
| Apply to editor | Resolves the selected printer/resin pair (no CLI-style overrides) and hands it to the document as one undoable edit. Closing the dialog after an apply refreshes the Setup tab from the new settings. |
| Diff against editor | Resolves the selected pair and diffs it against the editor's current settings; shows the leaf differences (empty means identical). |
| Provenance | Shows `library.provenance` for the selected pair. The dialog passes no overrides, so `override` never appears from this button — only `default`, `printer`, or `resin`. |
| Save printer profile... | Writes the **editor's current settings** (not the selected pair) as a self-contained `.ptr`, via a file-save dialog. Same round-trip check as `profile save`. |
| Bind resin to printer... | Takes the resin profile from the selected table row if it is a resin row, else from the Resin profile combo; prompts for the target printer id (defaulting to the editor's current printer id) and an output path; then calls the same `bind_resin_process` as `voxelmill resin bind`, with the same verbatim-copy and self-overwrite refusal. |
| Refresh | Re-runs discovery and repopulates the table and both combos. |

The dialog has no field to pick a source printer for **Bind resin to
printer...** — it always calls `bind_resin_process` with
`source_printer=None`, the same as the CLI's `bind` with `--from` omitted.
For a resin that already binds exactly one printer this is the common case
and needs no extra input; for a resin that already binds more than one, the
button fails with the same `--from is required` error the CLI would give,
and the dialog has no way to supply `--from` — use `voxelmill resin bind
--from PRINTER_ID` from the command line in that case.

Results and errors from every button are shown as JSON in the dialog's
output pane.
