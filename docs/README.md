# Documentation index

A map for deciding **what to read and when**. Most readers only need
`user/`. The `developer/` folder is for firmware reverse-engineering and
bridge internals — a regular user can ignore it entirely.

> Setup, CLI commands, and autostart start at the project
> [README](../README.md) and [user/getting-started.md](user/getting-started.md).

## I want to… → read

| I want to… | Read |
|---|---|
| Understand what the project is before touching anything | [../README.md](../README.md) |
| Install it and get the first bridge running | [user/getting-started.md](user/getting-started.md) |
| See all page YAML syntax fast | [user/pages-cheatsheet.md](user/pages-cheatsheet.md) |
| Create or edit a `page_*.yaml` in depth (slots, `host_action`, `fw_action`) | [user/pages-guide.md](user/pages-guide.md) |
| Import my Windows Ulanzi profiles | [user/importing-windows-profiles.md](user/importing-windows-profiles.md) |
| Set up Spotify from scratch (developer app + OAuth) | [user/spotify-setup.md](user/spotify-setup.md) |
| Auto-switch pages when an app gets focus | [user/focus-pages.md](user/focus-pages.md) |
| Make or reference custom icons | [user/icons.md](user/icons.md) |
| Fix something that does not work | [user/troubleshooting.md](user/troubleshooting.md) |
| Get the project flow in 1 minute (profiles → YAML → ZIP → HID → press → host) | [developer/architecture.md](developer/architecture.md) |
| Touch the HID protocol, decode new messages, or understand the firmware | [developer/firmware-protocol.md](developer/firmware-protocol.md) |
| See which hypotheses were already tried and discarded (do not repeat) | [developer/firmware-dead-ends.md](developer/firmware-dead-ends.md) |
| Understand how `d200h convert` works internally | [developer/convert-internals.md](developer/convert-internals.md) |
| Know what is in progress, pending, or not supported | [../ROADMAP.md](../ROADMAP.md) |

## docs/user/ — for users

- [getting-started.md](user/getting-started.md)
- [pages-cheatsheet.md](user/pages-cheatsheet.md)
- [pages-guide.md](user/pages-guide.md)
- [importing-windows-profiles.md](user/importing-windows-profiles.md)
- [spotify-setup.md](user/spotify-setup.md)
- [focus-pages.md](user/focus-pages.md)
- [icons.md](user/icons.md)
- [troubleshooting.md](user/troubleshooting.md)

## docs/developer/ — for developers

- [architecture.md](developer/architecture.md)
- [firmware-protocol.md](developer/firmware-protocol.md)
- [firmware-dead-ends.md](developer/firmware-dead-ends.md)
- [convert-internals.md](developer/convert-internals.md)

## Maintenance convention

- **Closed** items move to the official docs (user/ or developer/ as
  appropriate).
- **Pending** items or findings under investigation live in
  [../ROADMAP.md](../ROADMAP.md) until resolved.
- Things that were **tried and failed** go to
  [developer/firmware-dead-ends.md](developer/firmware-dead-ends.md) so
  they are not repeated.
