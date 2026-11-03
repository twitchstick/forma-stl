# Forma

A personal, self-hosted STL library for Unraid with dark, light and system appearance modes. Keep the **original photos** beside your model files, organize with tags and collections, and track the designs you want to print.

## What works

- Photo-first grid and list views; photo galleries and selectable covers.
- Automatic previews for existing and new entries: use embedded 3MF creator photos first, then slicer thumbnails, with software-rendered STL/OBJ/3MF geometry as a fallback. Model ZIPs can supply an embedded photo or a model preview too. Uploaded/source-page cover photos take priority.
- Search titles, creators, descriptions, collections, and tags.
- Bulk edit selected models: add/remove tags, assign/clear collections, change print status and favorite state.
- A read-only folder watcher checks every minute by default and queues new/changed folders in the Import inbox for approval.
- Duplicate finder compares SHA-256 content hashes, including files with different names. Results are advisory; nothing is deleted or merged.
- Filter by source, tags, and print status; favorites and a print queue.
- Upload STL, 3MF, OBJ, STEP, STP and ZIP files, plus JPG, PNG, WebP and GIF images. ZIP files are stored for download, not extracted.
- Import public page metadata (title, description, creator, tags and Open Graph cover image when provided).
- Scan an existing server folder, read-only. Each containing folder becomes a model with its files and pictures. Root-level files are grouped by filename stem. Repeated scans skip existing imported folders.
- SQLite persistence, duplicate-source detection, duplicate-attachment detection, JSON catalog export, and downloadable database/upload backups.
- Optional HTTP Basic sign-in. No cloud account or API key required.

**Website imports are best effort.** Some model sites block automated requests or require sign-in. In that case, save the source URL and enter the title and photo manually. Link imports do not download STL files, bypass access controls, or synchronize accounts. Upload files you have downloaded yourself. STL files generally do not contain product photos; Forma keeps images from source pages, uploads, or neighboring files in your model folder.

## Install on Unraid

Use a terminal on your Unraid server. Clone this repository to a persistent share, then enter the checkout directory.

```sh
# Run from your cloned Forma repository:
docker build -t forma:local .
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
  forma:local
```

Open `http://YOUR-UNRAID-IP:8086` and sign in with the username/password you set. Use **Import server folder** to catalog your existing share, **Add model** for uploads, or **Import from URL** for a source page.

### Unraid Docker template

After building `forma:local`, copy `unraid/forma.xml` to `/boot/config/plugins/dockerMan/templates-user/my-forma.xml`. In Unraid, use **Docker → Add Container** and select the Forma user template. Set the share path and password before applying. Use either the template or the terminal command to create the container, not both.

### Compose (optional)

Unraid does not include Compose by default. If you already have a Compose plugin installed, edit `compose.yaml` to use your share, copy `.env.example` to `.env`, set a password, then run:

```sh
docker compose up -d --build
```

### Storage and access

| Container path | Unraid example | Purpose |
| --- | --- | --- |
| `/data` | `/mnt/user/appdata/forma` | Database, uploads, imported cover photos. Read/write. |
| `/library` | `/mnt/user/3d-printing` | Your existing models and images. Read-only. |

The SQLite database should live on a local Unraid pool/disk, not a remote SMB/NFS mount. The container runs as root by default for simple Unraid volume compatibility. If you set a Docker `--user` or Compose `user`, make the `/data` folder writable by that UID/GID first. There are no `PUID`/`PGID` variables.

Keep the app on your trusted LAN, VPN, or HTTPS reverse proxy. HTTP Basic authentication does not encrypt traffic; use HTTPS for remote access. Leaving `FORMA_PASSWORD` empty disables sign-in. CSRF checks require same-origin browser requests and the `X-Forma-Request: 1` header for API writes.

## App icon

Forma uses the layered cube icon in its sidebar and browser tab. The Unraid container label `net.unraid.docker.icon` and template `Icon` point to `http://192.168.1.194:8086/static/app-icon.png`. If your server IP or web port changes, update this URL too (or set `FORMA_ICON_URL` for Compose). This one static logo is readable without signing in so Unraid can cache it; catalog data and other routes still require sign-in when a password is configured.

## Backups and recovery

In **Library settings**, download a full backup. It contains a consistent SQLite snapshot and all uploaded files. Existing read-only share files remain on the share and must be backed up separately. A JSON export contains catalog metadata only.

To restore: stop the container, preserve a copy of its current appdata, extract `forma.sqlite3` and `files/` from the backup into an **empty** appdata directory, then restart with the same `/data` and `/library` mappings. Backups contain absolute container paths; keep these mappings unchanged. Do not mix a restored database with old SQLite `-wal` or `-shm` files. Images and files scanned from your model share remain linked to their original locations. Moving or renaming those original files breaks those links.

The folder watcher is enabled by default and checks every minute, including when the browser is closed. Open **Import inbox** to change the interval (30 seconds to one hour), pause watching, or scan immediately. New folders and changes to linked files wait for approval. Approving a change synchronizes only server-share file references; your titles, tags, notes, collections, print state and uploaded images stay in place. Missing files are flagged and acknowledged without removing catalog entries. Dismissed changes stay dismissed until that folder changes again. Originals are always read-only.

**Find duplicates** runs a background content-hash index when requested and saves hashes for unchanged files. It finds exact file copies, not geometrically similar models or equivalent STL/3MF exports. Changed and unavailable files are excluded from stale matches. File contents are never deleted or altered. Large libraries may take time to hash on their first scan.

Use **Select** above the catalog to select cards, then **Edit selected**. Select visible applies to the current filtered view; the selected count includes selections retained from other filters. Bulk changes are atomic and do not change file attachments or titles.

Automatic thumbnails are generated on demand and cached in `/data/thumbnails`. Existing entries gain previews automatically without reimporting or changing originals. The cache can be removed safely while the container is stopped; it is rebuilt as cards are viewed. Geometry rendering is limited to 256 MB of model/archive data; embedded images are limited to 16 MB and can still be read from larger 3MF/ZIP files. STEP files and invalid/unsupported models need a manual cover photo. Generated thumbnails are disposable and are not included in full backups.

## Update

Back up first. In your repository checkout, run `git pull`, then rebuild `docker build -t forma:local .`. Recreate the container using the same ports, environment, and volume mappings. The data stays in your appdata folder. With Compose, run `docker compose up -d --build`.

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
