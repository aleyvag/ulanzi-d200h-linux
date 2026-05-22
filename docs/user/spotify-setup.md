# Spotify setup — from a developer app to working playback control

The bridge controls Spotify natively through the **Web API** (OAuth2 +
PKCE), not through `playerctl`. That means each user authorizes their own
Spotify developer app. This guide walks through it from scratch.

> **Requirements**: a **Spotify Premium** account (the Web API rejects
> playback control on Free accounts → HTTP 403), and at least one Spotify
> player open (desktop app, mobile, or web player) so there is a device to
> target.

If you imported a Windows profile that used Spotify slots, `d200h convert`
already pre-filled your `client_id`/`client_secret` — jump to
[step 4](#4-where-the-credentials-go). Otherwise start at step 1.

---

## 1. Create an app in the Spotify Developer Dashboard

1. Go to <https://developer.spotify.com/dashboard> and log in.
2. **Create app**. Give it any name and description.
3. Once created, open its settings and copy the **Client ID** and the
   **Client Secret**.

---

## 2. Configure the EXACT Redirect URI

In the app settings, add this Redirect URI **exactly**:

```
http://127.0.0.1:30901/oauth2callback
```

This is hardcoded in [src/d200h/spotify_auth.py](../../src/d200h/spotify_auth.py)
(`DEFAULT_PORT = 30901`, path `/oauth2callback`) — the same port the
Windows app uses. If it does not match character-for-character in the
dashboard, the OAuth flow fails with a redirect-URI-mismatch error. Save
the settings.

---

## 3. Scopes (informational)

The bridge requests these scopes; they are hardcoded, you do not configure
them anywhere:

```
user-modify-playback-state
user-read-playback-state
user-library-modify
user-library-read
```

(`playback-state` for play/pause/next/volume, `library` for the
`like`/save toggle.)

---

## 4. Where the credentials go

The bridge reads credentials from `config/secrets/spotify.yaml`:

```yaml
client_id: "<your Client ID>"
client_secret: "<your Client Secret>"
refresh_token: ""   # left empty; filled by spotify-auth in step 5
```

- If you imported a Windows profile, `d200h convert` already created this
  file with your `client_id`/`client_secret` (it never overwrites an
  existing one).
- If not, copy the template and fill it in:

  ```bash
  cp config/secrets/spotify.yaml.example config/secrets/spotify.yaml
  $EDITOR config/secrets/spotify.yaml
  ```

The file lives under `config/secrets/`, which is **gitignored** (only
`*.example` templates are committed), and `spotify-auth` writes it with
`0600` permissions.

---

## 5. Authorize

```bash
uv run d200h spotify-auth
```

This opens your browser, you approve the app, the local callback on
`http://127.0.0.1:30901/oauth2callback` captures the code, exchanges it
for tokens, and saves the `refresh_token` back into
`config/secrets/spotify.yaml` (mode 0600). The bridge then refreshes the
access token automatically and persists the rotating refresh token on its
own.

---

## 6. Verify

```bash
uv run d200h spotify-status
```

This checks the token works and lists the available Spotify devices and
the current track. If you see your devices, you are done — relaunch the
bridge and the `type: spotify` slots work.

---

## 7. Temporarily disabling Spotify

Without deleting your credentials, set the env var to any of
`0|false|off|no`:

```bash
systemctl --user set-environment D200H_SPOTIFY=0
systemctl --user restart d200h.service
```

While disabled, `type: spotify` slots do not crash — they open an
informative popup.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Browser shows "redirect URI mismatch" | The dashboard URI does not match exactly | set it to `http://127.0.0.1:30901/oauth2callback`, save, retry |
| `spotify-auth` says the callback port is in use | Another process holds port 30901 | close it, or wait and retry |
| Slot opens a "no_device" popup | No active Spotify player | open the Spotify app (desktop/mobile/web) and press again |
| Slot opens a "Spotify disabled" popup | Missing `config/secrets/spotify.yaml` or `D200H_SPOTIFY` is set | finish steps 4-5, or unset the env var |
| HTTP 403 on playback control | The account is not Premium | the Web API requires Premium for playback control |
| Token stopped working | Credentials revoked or refresh token invalidated | re-run `uv run d200h spotify-auth` |

---

## Cross-references

- [pages-guide.md §4.15](pages-guide.md) — the `type: spotify` handler and
  every `cmd` it supports.
- [../developer/convert-internals.md §4.1](../developer/convert-internals.md) —
  how credentials are auto-extracted from a Windows profile.
- [getting-started.md](getting-started.md) — base setup.
