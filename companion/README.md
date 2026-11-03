# Forma MakerWorld companion

Download the companion ZIP from **Forma → MakerWorld import**. Extract it to a permanent folder, then load that folder as an unpacked extension in Chrome or Edge's extensions page with Developer mode enabled. The ZIP includes the app icon needed by the manifest.

1. In Forma, create a pairing code. In the companion, enter your Forma URL and the code and approve access to your server's host.
2. Sign in normally at `https://makerworld.com` in that same browser. The companion reads MakerWorld's `token` cookie locally when you ask it to load collections. It uses that token only for authenticated read/download requests to Bambu Lab's API. It never saves that token or sends it to Forma.
3. Load collections, select the ones you want, or paste a collection URL. Optionally include liked models. Review up to 500 distinct models per batch.
4. Choose catalog-only or opt in to the default 3MF profile for each selected model, then send. File downloads run sequentially and stop on rate limits; unavailable files remain catalog-only with a warning.
5. Follow the review link and approve selected models in Forma. No model enters the catalog before this approval. Source photos are downloaded by your Forma server, and new source memberships are added without overwriting existing tags, notes or print status.

## Permissions and data

- `cookies`: read the MakerWorld session cookie, only for the MakerWorld host covered by host permissions. No cookies are written or removed.
- MakerWorld, Bambu Lab API, and Bambu's model-download CDN host permissions: load collections/details/files using the existing session. No MakerWorld likes or saves are changed.
- Optional server-host permission: requested when you pair with your own Forma address.
- `storage`: keeps the Forma URL and a revocable, import-only Forma token. MakerWorld session credentials are never persisted.
- No content scripts, password prompts, injected code, third-party analytics, remote executable code or unattended background synchronization.

HTTP access to a LAN Forma server has the same transport limits as the rest of that LAN app; use your HTTPS reverse proxy or VPN for remote access. Do not expose an unencrypted pairing code or companion token to the public internet.

Internal MakerWorld endpoints can change. Authentication errors, site verification and download limits are reported to the user; the companion does not bypass them. Private-account behavior needs validation in your signed-in browser. Expired/partial pending batches are removed when another batch is received after seven days; you can dismiss them sooner in Forma.
