# Importing Windows Ulanzi profiles

If you already had the device configured in the official Ulanzi software
(Windows), you can export your profiles from the app and translate them
to the bridge format. This is a user how-to; for what happens internally
(binary format, action mapping, two passes), see
[../developer/convert-internals.md](../developer/convert-internals.md).

---

## Steps

1. **Export from the official app.** Export each profile you want to keep.
   Each one is saved as `<Name>.ulanziDeckProfile` (really a ZIP with a
   12-byte header; the converter understands it).

2. **Copy the exports into the repo.** Drop all the `.ulanziDeckProfile`
   files into `config/ulanzi_deck_profiles/` (the established folder for
   exports; they are only read during import, never again afterwards). It
   is gitignored — your personal profiles never get committed.

3. **Run the conversion pointing at the whole directory** (not a single
   file) — that way the converter can resolve cross-profile references
   (`Switch Profile`, folders, etc.):

   ```bash
   uv run d200h convert "config/ulanzi_deck_profiles/" --dry-run   # see the plan
   uv run d200h convert "config/ulanzi_deck_profiles/"             # writes YAML + icons
   ```

   The command:
   - Auto-detects the profile named **`Default Profile`** and materializes
     it as `page_home.yaml` (the bridge's entry). If your "main" profile
     has a different name, pass `--default-profile NAME`.
   - Resolves `page.next/page.prev` to **explicit** `page.goto` with the
     real next page_id, so navigation within each profile does not depend
     on global alphabetical order.
   - Resolves `Switch Profile`/`Folder` by pointing at the entry page_id
     of the destination profile.
   - Copies referenced icons to `config/icons/<slug>__<name>.png`
     (namespaced per profile to avoid collisions).

4. **Review the TODOs.** The converter **never fails**: anything it cannot
   translate is left as `host_action: stub` with a **commented** YAML line
   right above it indicating the original data. Find the TODOs:

   ```bash
   grep -n '^\s*#' config/pages/user/page_*.yaml
   ```

5. **Fix the stubs.** Press a slot with a `stub` while the bridge runs: a
   Tk popup appears with the command, the args, and a `hint` suggesting
   the recommended Linux translation, so you know what it did on Windows
   and how to configure it here. Policy: **no slot of the original profile
   stays silent** — everything gives feedback on press. Edit the slot in
   `config/pages/user/page_*.yaml` and replace the `stub` with the real
   action (typically `type: app`, `type: keys`, `type: shell`,
   `type: multi`, etc.). Full examples in [pages-guide.md](pages-guide.md)
   and in `config/pages/examples/page_test_*.yaml` (one case per
   problematic action, with its Linux equivalent next to it).

6. **Validate and run:**

   ```bash
   uv run d200h validate
   uv run d200h bridge
   ```

---

## Command flags

| Flag | What for |
|---|---|
| `--dry-run` | Processes everything in memory, prints the plan, writes nothing |
| `--out DIR` | Changes the YAML destination (default `config/pages/user/`) |
| `--icons-out DIR` | Changes the icon destination (default `config/icons/`) |
| `--default-profile NAME` | Which profile to materialize as `home` |
| `--keep-existing` | Does not overwrite destination YAMLs that already exist |

> ⚠️ **Do not re-run `convert` without `--keep-existing`** once you have
> hand-edited the generated YAMLs — it overwrites them. If you invested
> time fixing stubs, always pass `--keep-existing` on a re-conversion.

---

## What is NOT imported

- **App→profile focus rules.** The Windows software supports "activate
  this profile when an app has focus", but that metadata is not in the
  exported profile. After converting, edit
  [`config/focus_rules.yaml`](../../config/focus_rules.yaml) by hand using
  the slugs the converter reported. See [focus-pages.md](focus-pages.md).
- **Spotify**: credentials are auto-extracted to
  `config/secrets/spotify.yaml`, but you still need to authorize. See
  [spotify-setup.md](spotify-setup.md).
- **Sound** (`sound.play`/`sound.stop`): not implemented in the bridge;
  left as stubs.

Full mapping of every Windows Action and the error/stub handling:
[../developer/convert-internals.md](../developer/convert-internals.md).

---

## Cross-references

- [../developer/convert-internals.md](../developer/convert-internals.md) —
  how the converter works internally.
- [pages-guide.md](pages-guide.md) — replacing stubs with real actions.
- [spotify-setup.md](spotify-setup.md) — finishing the Spotify setup.
- [troubleshooting.md](troubleshooting.md) — page does not load, ~196 KB
  limit.
