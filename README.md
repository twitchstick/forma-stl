# Forma

A personal, self-hosted STL library for Unraid with dark, light and system appearance modes. Keep the **original photos** beside your model files, organize with tags and collections, and track the designs you want to print.

## What works

- Photo-first grid and list views; photo galleries and selectable covers.
- A page for every model at its own address (`/models/<id>`), so Back, bookmarks and shared links work. Turn STL, 3MF, OBJ and zipped models in 3D right in the browser; the server sends a simplified copy (at most 150,000 triangles, cached in `/data/meshes`) so large files stay quick on phones.
- Automatic previews for existing and new entries: use embedded 3MF creator photos first, then slicer thumbnails, with software-rendered STL/OBJ/3MF geometry as a fallback. Model ZIPs can supply an embedded photo or a model preview too. Uploaded/source-page cover photos take priority.
- Search titles, creators, descriptions, collections, and tags.
- Bulk edit selected models: add/remove tags, assign/clear collections, change print status and favorite state.
- A read-only folder watcher checks every 15 minutes by default and queues new/changed folders in the Import inbox for approval.
- Duplicate finder compares SHA-256 content hashes, including files with different names. Results are advisory; nothing is deleted or merged.
- Filter by source, tags, and print status; favorites and a print queue.
- Drop files anywhere to add a model: Forma names it after the model file and asks once before saving. Drop files on a model page to add them to it. Paste files, screenshots or a model page link anywhere to do the same.
- Upload STL, 3MF, OBJ, STEP, STP and ZIP files, plus JPG, PNG, WebP and GIF images. ZIP files are stored for download, not extracted.
- Import public page metadata (title, description, creator, tags and Open Graph cover image when provided).
- Scan an existing server folder, read-only. Each containing folder becomes a model with its files and pictures. Root-level files are grouped by filename stem. Repeated scans skip existing imported folders.
- SQLite persistence, duplicate-source detection, duplicate-attachment detection, JSON catalog export, and downloadable database/upload backups.
- MakerWorld browser companion: choose collections or liked models, stage metadata and optional default-profile 3MF files, then approve selected items inside Forma. Multiple collection memberships are preserved.
- Account settings with password changes, profile pictures, display names and browser session management. Core catalog features need no cloud account or API key.

**Website imports are best effort.** Some model sites block automated requests or require sign-in. In that case, save the source URL and enter the title and photo manually. Link imports do not download STL files, bypass access controls, or synchronize accounts. Upload files you have downloaded yourself. STL files generally do not contain product photos; Forma keeps images from source pages, uploads, or neighboring files in your model folder.

## Install on Unraid

Forma is published as a ready-made image, `ghcr.io/twitchstick/forma-stl:latest`, rebuilt automatically whenever `main` passes its tests. Unraid's **Check for updates / Apply update** pulls new versions; nothing needs to be built on the server.

```sh
mkdir -p /mnt/user/appdata/forma
```

Change `/mnt/user/3d-printing` below to the **existing share holding your model files**. Keep the `:ro` suffix. The example port is 8086; change the left side if it is in use.

```sh
docker run -d \
  --name forma \
  --restart unless-stopped \
  -p 8086:8080 \
  -v /mnt/user/appdata/forma:/data \
  -v /mnt/user/3d-printing:/library:ro \
  -e FORMA_USERNAME=admin \
  -e FORMA_PASSWORD='replace-with-a-strong-password' \
  --label 'net.unraid.docker.managed=dockerman' \
  --label 'net.unraid.docker.webui=http://[IP]:[PORT:8080]/' \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  ghcr.io/twitchstick/forma-stl:latest
```

Open `http://YOUR-UNRAID-IP:8086` and sign in with the username/password you set. Use **Import server folder** to catalog your existing share, **Add model** for uploads, or **Import from URL** for a source page.

### Unraid Docker template

Copy `unraid/forma.xml` to `/boot/config/plugins/dockerMan/templates-user/my-forma.xml`. In Unraid, use **Docker → Add Container** and select the Forma user template. Set the share path and password before applying. Use either the template or the terminal command to create the container, not both.

### Compose (optional)

Unraid does not include Compose by default. If you already have a Compose plugin installed, edit `compose.yaml` to use your share, copy `.env.example` to `.env`, set a password, then run:

```sh
docker compose up -d
```

### Storage and access

| Container path | Unraid example | Purpose |
| --- | --- | --- |
| `/data` | `/mnt/user/appdata/forma` | Database, uploads, imported cover photos. Read/write. |
| `/library` | `/mnt/user/3d-printing` | Your existing models and images. Read-only. |

The SQLite database should live on a local Unraid pool/disk, not a remote SMB/NFS mount. The container runs as root by default for simple Unraid volume compatibility. If you set a Docker `--user` or Compose `user`, make the `/data` folder writable by that UID/GID first. There are no `PUID`/`PGID` variables.

Keep the app on your trusted LAN, VPN, or HTTPS reverse proxy. Use HTTPS for remote access. On first startup, `FORMA_USERNAME` and `FORMA_PASSWORD` seed the owner account. Forma refuses to start without a password. To run without sign-in on a fully trusted network anyway, set `FORMA_ALLOW_NO_PASSWORD=1`. Once saved, the account in `/data/forma.sqlite3` takes precedence over these environment variables; restarting or updating the container does not restore the old password. CSRF checks require same-origin browser requests and the `X-Forma-Request: 1` header for API writes.

## App icon and font

Forma uses the layered cube icon in its sidebar and browser tab. The interface typeface is Geist, bundled in `static/fonts` under the SIL Open Font License (`static/fonts/Geist-OFL.txt`), so pages make no requests to outside font services. The Unraid container label `net.unraid.docker.icon` and template `Icon` point to the copy of `static/app-icon.png` on GitHub, so they work for any server address. Set `FORMA_ICON_URL` for Compose to use a different icon. This one static logo is readable without signing in so Unraid can cache it; catalog data and other routes still require sign-in when a password is configured.

## MakerWorld collections and saved models

Open **MakerWorld import** in Forma.

### Safari (no extension needed)

1. Drag **Send MakerWorld to Forma** from the import dialog to Safari’s Favorites bar.
2. Sign in normally on MakerWorld and open your collections or a collection page. Click the bookmark.
3. Use **Load my collections → Read selected collections**, or **Capture loaded models**. The latter captures only page content already loaded: scroll and capture again to include more cards. Partial captures are marked in the review.
4. Select models, optionally enable default 3MF profiles, and choose **Send selected to Forma**. Review and approve the batch in Forma.
5. If site policies isolate the tabs or prevent transfer, use **Export selected JSON**, then **Import Safari JSON** in Forma. JSON exports contain catalog metadata and collection memberships, not binary model files. Attach downloaded files separately in this fallback mode.

The Safari bookmark uses only same-origin MakerWorld requests with the existing browser session; it does not read cookies or tokens. The direct handoff verifies the MakerWorld origin, the sending window, and a random request nonce, then stages data through the signed-in Forma tab. No import is automatically approved. Safari transfers up to 256 MB of model files per send. MakerWorld may require verification or block an API request; the loaded-card capture is available without an API call.

### Chrome / Edge (optional companion)

Download the companion ZIP, extract it to a permanent folder, and load it as an unpacked extension (Extensions → Developer mode → Load unpacked). The packaged folder includes the icon and all required scripts; no build step is needed.

1. In Forma, create a pairing code. In the extension tab, enter your Forma address and code, then approve access to that server host.
2. Sign in normally on makerworld.com in that browser. Click **Load my collections**, choose collections (or paste a collection link), and optionally include liked models.
3. Review the model list. Choose catalog-only or opt in to each model's default 3MF print profile. Click **Send selected to Forma**.
4. Follow **Review and approve in Forma**. Review the staged items and warnings, then add the selected models to your library.

Imports are one-way and additive. MakerWorld model IDs and source links prevent repeated catalog entries; indexed exact file hashes can link a downloaded file to an existing local model too. Existing local titles, notes, tags and print state are kept. Source collection memberships are additive and separate from your manually assigned primary collection. Removing a save on MakerWorld does not delete anything in Forma.

The extension reads MakerWorld's session cookie locally only when you request an import. It uses it for authenticated requests to Bambu Lab's API; that session token is never saved by the extension or sent to Forma. Forma stores only hashed pairing/device tokens and normalized import data. A paired device may stage imports and files for its own batches; it cannot approve imports, read the catalog, delete entries or edit existing models through its token. Revoke paired browsers in **MakerWorld import**.

Pairing codes expire after ten minutes and work once. Batches accept up to 500 distinct models, with 256 MB per model file. Downloads are sequential and respect authentication failures and rate limits. Unavailable files are reported and can remain catalog-only. Partial batches can be dismissed; pending or partial batches older than seven days are cleaned up when a new batch arrives. Source photos are loaded server-side from public MakerWorld/Bambu image hosts.

This uses MakerWorld's internal read/download interfaces, which can change. Signed-in private-collection access and Safari site-policy behavior must be validated in the user's signed-in browser. The importer reports site verification/authentication errors and does not bypass them. It does not ask for or change your MakerWorld password, likes, saves or collections. See [companion details and permissions](companion/README.md).

## Memory

Thumbnails, 3D previews, measurements and slicer copies load whole model files, which can need far more memory than the file size. Forma does this work in a separate, short-lived process, one file at a time, and the memory is returned to the system when it finishes. Each job may use up to `FORMA_WORKER_MEMORY_MB` (default 2048). A file that needs more is shown as too large to preview; the server keeps running. Large binary STL and 3MF files are read with streaming loaders, so a 130 MB STL or a 3MF with 2.6 million triangles stays under 1 GB.

The Unraid template also caps the whole container with `--memory=3g` in Extra Parameters, so Forma can never use all of the server's memory. Keep the cap at least 1 GB above `FORMA_WORKER_MEMORY_MB`. Run `docker stats forma` to see current use. If your server ever stops responding, turn on **Settings → Syslog Server → Mirror syslog to flash** so the next crash leaves a log; look for `Out of memory` lines.

## Backups and recovery

In **Library settings**, download a full backup. It contains a consistent SQLite snapshot and all uploaded files. Existing read-only share files remain on the share and must be backed up separately. A JSON export contains catalog metadata only.

To restore: stop the container, preserve a copy of its current appdata, extract `forma.sqlite3` and `files/` from the backup into an **empty** appdata directory, then restart with the same `/data` and `/library` mappings. Backups contain absolute container paths; keep these mappings unchanged. Do not mix a restored database with old SQLite `-wal` or `-shm` files. When an update changes the database, Forma first saves a copy in `/data/backups` (the five most recent are kept). To undo an update, stop the container, replace `forma.sqlite3` with the newest copy, and run the previous image. Images and files scanned from your model share remain linked to their original locations. Moving or renaming those original files breaks those links.

The folder watcher is enabled by default and checks every 15 minutes, including when the browser is closed. Each check lists every file on the share, so very short intervals can keep Unraid array disks from spinning down. Open **Import inbox** to change the interval (30 seconds to one hour), pause watching, or scan immediately. New folders and changes to linked files wait for approval. Approving a change synchronizes only server-share file references; your titles, tags, notes, collections, print state and uploaded images stay in place. Missing files are flagged and acknowledged without removing catalog entries. Dismissed changes stay dismissed until that folder changes again. Originals are always read-only.

**Find duplicates** runs a background content-hash index when requested and saves hashes for unchanged files. It finds exact file copies, not geometrically similar models or equivalent STL/3MF exports. Changed and unavailable files are excluded from stale matches. File contents are never deleted or altered. Large libraries may take time to hash on their first scan.

Use **Select** above the catalog to select cards, then **Edit selected**. Select visible applies to the current filtered view; the selected count includes selections retained from other filters. Bulk changes are atomic and do not change file attachments or titles.

Automatic thumbnails are generated on demand and cached in `/data/thumbnails`. Existing entries gain previews automatically without reimporting or changing originals. The cache can be removed safely while the container is stopped; it is rebuilt as cards are viewed. Geometry rendering is limited to 256 MB of model/archive data; embedded images are limited to 16 MB and can still be read from larger 3MF/ZIP files. STEP files and invalid/unsupported models need a manual cover photo. Generated thumbnails are disposable and are not included in full backups.

## Update

Back up first. In Unraid, use **Docker → Check for updates**, then **Apply update** on Forma. The data stays in your appdata folder. With Compose, run `docker compose pull && docker compose up -d`. To run your own changes instead, build locally with `docker build -t forma:local .` and use `forma:local` as the repository.

## Local development and tests

Python 3.9+ is supported; the Docker image uses Python 3.13.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
FORMA_DATA=./data FORMA_LIBRARY=/path/to/models HOST=127.0.0.1 python app.py
# http://127.0.0.1:8080
python -m unittest discover -s tests -v
```

Forma runs Flask behind Waitress, without a JavaScript build step. No data is stored in browser local storage. External metadata/image fetches are size-limited, validate public IP addresses, and pin the validated address for every redirect hop. Page text is escaped before display; model files download as attachments.

### Reference documentation

- [Unraid container management and volume mappings](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/managing-and-customizing-containers/)
- [Unraid Docker overview](https://docs.unraid.net/unraid-os/using-unraid-to/run-docker-containers/overview/)
- [Python SQLite documentation](https://docs.python.org/3.13/library/sqlite3.html)

## Printables, Cults3D, and Thangs (Safari)

Open **Import from website** in Forma and drag **Send website to Forma** to Safari’s Favorites bar. Open a signed-in collection, saved list, or model page on Printables, Cults3D, or Thangs and use the bookmark. Name the collection, scroll/load more cards, and click **Capture loaded models** again. Captures accumulate in that website tab until cleared; uncheck unwanted models. Send the selection to Forma, then review and approve the batch. Safari popup isolation or site security settings can block the direct handoff: use **Export selected JSON** and upload it in Forma instead.

The new bookmark captures browser-visible metadata, not a full account sync. Model pages can provide a description, creator, tags and cover photo when present in their HTML metadata. Collection cards typically provide a title, photo and source link. No credentials are copied. Source photos are fetched without your website session, so private or protected images can fail; add a cover manually in that case. Website layout changes can affect card recognition.

These three importers do not download model files or spend membership credits. Download files normally from the site, then attach them in Forma or use the watched server folder. Imports preserve existing edits and add collection memberships. Duplicate matching is scoped to each website, including Thangs short/full URL variants and Printables URL slug changes. Cults language variants normalize to English; renamed Cults slugs can need manual reconciliation. The optional Chrome/Edge companion remains MakerWorld-only.

## Saved views, printers, and print records

Use **+** beside **Saved views** to save the current search, tag, source, print status, collection/library view, sort order, and grid/list preference. Saved views live in the database and work across devices. Rename or remove them from the sidebar; mobile users can open **Library settings → Saved views**.

Use **My printers** (also in Library settings) to enter a printer’s name, usable X/Y/Z build volume in millimeters, optional nozzle diameter, and notes. Open a model to compare each attached mesh’s bounding box against a printer. Checks allow a 90-degree rotation on the plate, keep Z fixed, and do not account for supports, brims, reserved areas, or slicer restrictions. Confirm the final arrangement in your slicer. Profiles do not connect to or control printers.

Model details show editable labels for attached file variants, STL/OBJ dimensions (millimeters assumed), 3MF build-layout dimensions (declared units converted to mm), and selected embedded slicer settings when readable. ZIPs list up to 20 contained mesh variants without extracting files. Analysis is limited to 128 MB per file and uncompressed archive; unsupported or damaged files remain downloadable. Measurements load on demand and are cached by file path, size, and modification time in `/data/model-details`. This disposable cache can be cleared while Forma is stopped.

Choose **Record a print** in a model’s details to keep separate attempts with date, result, printer, filament, settings, and notes. Upload finished-print photos through **Edit model**, then select a gallery photo for the record. Removing a printer keeps its recorded name in previous print records. Recording a print does not change the model’s catalog status automatically. Database backups include saved views, printers, variant labels, and print records; JSON catalog exports retain the model catalog format.

## Source galleries, attribution, and retries

Open a model and choose **Retry source import** under **Source details & photos**. A background job reads explicit Open Graph and model JSON-LD metadata, fills empty creator/description/tags/license/attribution fields, and adds up to 20 available source image URLs to its gallery. Progress, per-photo failures, filled fields, and missing metadata appear in the model view. A retry also reattempts failed photos and skips images already present, including identical photo content at different URLs. Existing photos and the chosen cover stay in place; use the gallery’s **Use this photo as cover** action to choose another.

Nonempty fields are never replaced by a source retry. Fields changed or deliberately cleared through Edit model, and tags changed through bulk editing, are protected from later enrichment. License and attribution can be edited in the model form. Forma records only the source’s explicit terms; an absent license stays unknown. Check the original source before relying on its terms.

Public-page imports and the Safari bookmarks now retain photo lists and explicit credit metadata when provided. Collection cards normally expose only a cover; open the individual model page and capture again to get additional metadata. Signed-in pages or protected image downloads may be unavailable to the server. Use the Safari bookmark/JSON handoff or upload photos and details manually in that case. No website session credentials are sent to Forma. Background jobs use two workers and public-only bounded fetches, normalize images, and preserve progress in SQLite. Interrupted jobs require a retry after restart. Existing entries are not crawled automatically during an upgrade.

## Daily collection checks

Open **Collection schedules** from the sidebar, Library settings, or Import from website. Daily checks default to **04:00 America/New_York**. Change the time/time zone, pause the whole schedule or individual collections, and use **Check now** to try a collection immediately. The scheduler runs inside Forma on Unraid while the container is running; it does not need an open browser. It handles local daylight-saving time and runs one overdue check after downtime instead of replaying every missed day.

Imported website collections are registered automatically. Existing MakerWorld collection IDs supply their URLs. Older Printables/Cults3D/Thangs imports often lack a recoverable collection URL: paste the original link once in the schedule card. Updated Safari/companion imports retain collection links for future scheduling. Saved/liked lists and private collections may require signed-in Safari captures rather than unattended checks.

Checks use public requests without website credentials. MakerWorld's public collection response is paginated, up to 500 models. The other providers use publicly rendered model cards/ItemList metadata and available next-page links (up to 25 pages/500 models); those checks are labeled **Partial public check** because private/lazy-loaded content and recommendations cannot be verified as a complete collection snapshot. A blocked, empty/unreadable, or changed response is labeled **Browser refresh needed**, never “up to date.” Open that collection normally in Safari and use the importer to refresh it. No sign-in challenges or access restrictions are bypassed.

Newly discovered models go to a **Collection update** batch in **Import from website**, where you choose which entries to approve. No model files are downloaded by the daily checker. Existing imported memberships form the initial baseline, and previously seen entries (including dismissed suggestions) are not repeatedly staged. A complete read may report models not returned by the source, but local models and memberships are never removed automatically. Partial reads never infer removals. Last check, next check, results, and the latest review batch are shown per collection. Schedule settings, tracked links, and seen IDs live in SQLite and are included in database backups.

## Your printers and desktop slicers

**My printers** includes starter presets for Bambu Lab H2D, H2S, P1S, and Snapmaker U1. Choose a preset to fill the form, then save it and adjust the nozzle and build limits to match your hardware. Each printer has a preferred slicer: Bambu Studio or Snapmaker Orca. Existing custom profiles and print records are preserved.

Fit presets use a conservative shared dual-nozzle envelope for H2D (300 × 320 × 320 mm), H2S's 340 × 320 × 340 mm envelope, the installed Bambu Studio P1S profile's 250 mm usable height on its 256 × 256 mm bed, and U1's nominal 270 mm cube. The H2D has larger single-nozzle and combined-bed reaches; the P1S also has a reserved bed corner. Notes in each profile explain those distinctions. Confirm the final layout in the slicer. These are editable fit estimates, not automatically selected native slicer configurations.

In a model's file list, choose **Open in slicer**, select a printer/slicer and your computer's operating system, then **Prepare handoff**. Click the resulting **Open Bambu Studio** or **Open Snapmaker Orca** button to launch the installed desktop app. The printer selection chooses a preferred slicer; select the actual printer, nozzle and tools inside the slicer before slicing. Forma does not send commands to a printer or start a print.

Bambu Studio's web downloader accepts 3MF projects. Existing 3MF files are handed off unchanged. For STL and OBJ files up to 128 MB, Forma prepares a geometry-only 3MF copy, assumes millimeters, and translates it onto the positive build axes without scaling. Original files stay untouched. OBJ textures and external materials are not included in that geometry-only copy; download the original when needed. Check scale and use Arrange in Bambu Studio. STEP/STP files for Bambu Studio use the normal download/manual-open fallback. Bambu's web download limit is 500 MB. Snapmaker Orca's handler receives the original STL, OBJ, 3MF, STEP or STP file. ZIP archives must be downloaded and extracted first.

The native apps fetch from a random, file-scoped capability link that expires after ten minutes. The link carries no Forma username or password; its hash is stored in SQLite. Native HEAD/GET/Range retries are supported until expiry. Changed/missing original files invalidate the link, and the rest of the catalog still requires normal sign-in. Temporary converted meshes live in `/data/slicer-cache`; old copies are cleaned on subsequent handoffs and may be cleared while Forma is stopped. No cloud relay or desktop helper is installed. The slicer must be able to reach the same Forma address as the browser. App/browser prompts, URL-handler registration, and native slicer download-directory setup may require your interaction; the original **Download** action remains available.

Protocol references: [Bambu Studio macOS handler](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/GUI_App.cpp), [Bambu project downloader](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/Plater.cpp), [Snapmaker Orca downloader](https://github.com/Snapmaker/OrcaSlicer/blob/main/src/slic3r/GUI/Downloader.cpp). Preset limits were checked against the installed vendor profiles and [Snapmaker's U1 specifications](https://wiki.snapmaker.com/en/FAQ/u1).

## Account and profile

Click your avatar at the top right, **Account** in the sidebar, or **Manage account** in workspace settings. You can change your display name, username, password, and profile photo. Username and password changes require your current password. New passwords must contain 12–256 characters. Photos may be PNG, JPG, WebP or GIF up to 5 MB; Forma crops and stores a 256-pixel square copy.

Browser sign-in lasts up to 14 days. Account settings lists active browsers and lets you sign out other browsers or the current browser. Changing your password or username keeps this browser signed in, signs out other browsers, invalidates old slicer download links and revokes browser companion pairings; pair the companion again afterward. Passwords are stored as salted hashes. HTTP Basic remains supported for API clients using the current account credentials. `/health` exposes only the service health status without sign-in so container health checks keep working after a password change.

Account credentials and profile photos persist in the data volume and are included in database backups. JSON catalog exports contain catalog records, not the account. If you forget your password, use Unraid Terminal for an interactive reset (the password is prompted privately, never passed as a command argument):

```sh
docker exec -it forma python account_admin.py reset-password
```

Add `--username NEWNAME` only if you also need to recover the username. Recovery signs out all browsers and revokes existing companion pairings and slicer links.

## 3D viewer

The viewer is built from `viewer/src/viewer.js` with three.js (MIT licence, notice kept in the bundle) into `static/vendor/forma-viewer.js`, which is committed so the Docker image needs no build step. After changing the source, rebuild it:

```sh
cd viewer && npm ci --ignore-scripts && npm run build
```

CI fails if the committed bundle does not match its source.
