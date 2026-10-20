# Configuration

All settings live in one file: `data/config.json` on docker-host (`/app/data/config.json` inside the app container). The app loads it at start. Its shape and defaults are defined by `app/config.py` (pydantic `Config`). Every key is listed below.

Start from `config.example.json` on a new host. It has the same shape with empty credentials and a `change-me` qBittorrent password.

The **UI** column says whether the Settings page can change the key. Keys marked "no" are changed by editing `data/config.json` and restarting the app: `docker restart dtdl-app`.

## Keys

### `dreamingtree`

Site login and address.

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `dreamingtree.base_url` | string | `https://dreamingtree.org` | no | Site base URL. Use `https://dreamingtree.org`; the `www.` host hangs during TLS. Read at start. |
| `dreamingtree.username` | string | `""` | yes | Login name. Required: without it the app cannot log in. |
| `dreamingtree.password` | string | `""` | yes (write-only) | Login password. The API never returns it. A blank value in the UI keeps the stored password. |

Example:

```json
"dreamingtree": {
  "base_url": "https://dreamingtree.org",
  "username": "<username>",
  "password": "<password>"
}
```

### `qbittorrent`

How the app reaches the qBittorrent Web API. Inside compose, the container is reached by its service name.

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `qbittorrent.url` | string | `http://qbittorrent:8080` | no | Web API address as seen from the app container. Read at start. |
| `qbittorrent.username` | string | `admin` | no | WebUI login. |
| `qbittorrent.password` | string | `""` | no (never shown) | WebUI password. Must match the qBittorrent WebUI login. Preflight checks it. |
| `qbittorrent.category` | string | `dreamingtree-dl` | no | Category given to every torrent the app adds. Torrents in other categories are not touched by the app. |

Example:

```json
"qbittorrent": {
  "url": "http://qbittorrent:8080",
  "username": "admin",
  "password": "<qbittorrent webui password>",
  "category": "dreamingtree-dl"
}
```

### `paths`

Paths as seen from inside the app container. These match the volume mounts in `docker/docker-compose.yml`. Change them only if the mounts change.

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `paths.staging` | string | `/staging` | no | Staging volume (nas). Torrents go under `torrents/`, work copies under `work/`. |
| `paths.library_root` | string | `/library` | no | Library volume (media-server). The library index scans under `DMB_Live`, `Dave_Tim` and `davematthews` below it. |

Example:

```json
"paths": {
  "staging": "/staging",
  "library_root": "/library"
}
```

### `library_mounts`

Maps a host-style path prefix to where that path is mounted in the container. The destinations are written in host style, so this translates them. See the worked example below.

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `library_mounts` | object: host prefix to container path | `{"/srv/music/": "/library/"}` | no | Each key is a host path prefix, each value is its container path. The app adds the trailing `/` to both, so either spelling works. The longest matching prefix wins. |

Example:

```json
"library_mounts": {
  "/srv/music/": "/library/"
}
```

A destination path that starts with none of these prefixes fails at publish time with `is not under any library_mounts prefix`. The UI does not check this when you save.

### `destinations`

The places you can queue a show to. The Browse page's destination menu lists their labels.

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `destinations` | list of objects | one destination, see below | yes | Each entry is one destination. A job stores the label it was queued with. |
| `destinations[].label` | string | (required) | yes | Name shown in the menu. Must be unique. Required for the Settings save. |
| `destinations[].path` | string | (required) | yes | Host-style path, for example `/srv/music/`. Must fall under a `library_mounts` prefix. |
| `destinations[].folder_template` | object: artist code to template | `{"dmb": "DMB_Live/{torrent_name}", "default": "Dave_Tim/{torrent_name}"}` | yes | Folder to create under `path`, per artist code. See below. |

The default destination:

```json
"destinations": [
  {
    "label": "Dave Matthews Band",
    "path": "/srv/music/",
    "folder_template": {
      "dmb": "DMB_Live/{torrent_name}",
      "default": "Dave_Tim/{torrent_name}"
    }
  }
]
```

#### `folder_template`

- Keys are artist codes taken from the torrent name: `dmb`, `d&t` (also written `d+t`), `dm`, and `other`. Lookup tries the exact code, then the `d&t`/`d+t` alias, then `default`. If neither exists the job fails with `no folder_template for '<code>' and no 'default'`, so keep a `default`.
- Placeholders: `{torrent_name}` (the torrent's folder name, unchanged), `{album}` (the ALBUM tag), `{artist}` (the artist tag), `{year}` (show year). Unknown placeholders are an error.
- Characters that are not safe in a folder name (`\ / : * ? " < > |` and control characters) are replaced with `_`. So an album `2025-07-12: Mountain View, CA` becomes `2025-07-12_ Mountain View, CA` as a folder name. The tags keep the real text.
- The result must be relative, with no `..`. Otherwise the job fails with `unsafe folder template result`.

### Worked example: where a show lands

Take three torrents queued to the default destination (`path` `/srv/music/`, `library_mounts` as above):

| Torrent name | Artist code | Template used | Container path | Path on media-server |
|---|---|---|---|---|
| `dmb2025-07-12.ca-11.flac16` | `dmb` | `DMB_Live/{torrent_name}` | `/library/DMB_Live/dmb2025-07-12.ca-11.flac16` | `/srv/music/DMB_Live/dmb2025-07-12.ca-11.flac16` |
| `d&t2022-07-17ck61.gf` | `d&t` | no `d&t` key, so `default`: `Dave_Tim/{torrent_name}` | `/library/Dave_Tim/d&t2022-07-17ck61.gf` | `/srv/music/Dave_Tim/d&t2022-07-17ck61.gf` |
| `dm2020-07-03.siriusxm.web-dl.flac` | `dm` | no `dm` key, so `default` | `/library/Dave_Tim/dm2020-07-03.siriusxm.web-dl.flac` | `/srv/music/Dave_Tim/dm2020-07-03.siriusxm.web-dl.flac` |

The steps, for the first row:

1. The destination `path` is `/srv/music/`. The only `library_mounts` prefix that matches is `/srv/music/`, which maps to `/library/`.
2. The artist code is `dmb`. The `dmb` template is `DMB_Live/{torrent_name}`, which fills in to `DMB_Live/dmb2025-07-12.ca-11.flac16`.
3. The job writes to `/library/DMB_Live/dmb2025-07-12.ca-11.flac16`, which is the media-server folder `/srv/music/DMB_Live/dmb2025-07-12.ca-11.flac16`.

Note the third row. Solo Dave shows (`dm`) land in `Dave_Tim` because there is no `dm` template. If you want them elsewhere, add a `dm` key to the template.

A second destination for a different folder layout works the same way. For example, `label` "Dave Solo", `path` `/srv/music/Dave_Solo/`, template `{"default": "{album}"}` writes to `/srv/music/Dave_Solo/2025-07-12_ Mountain View, CA/`.

The folder is created as `.<name>.dtdl-tmp` and renamed into place. If the final folder already exists and is not empty, the job stops with `destination already exists and is not empty`.

### `retention_days`

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `retention_days` | integer | `90` | yes | Days after the download completes before the torrent and its data are removed from qBittorrent and staging. The purge check runs at most once a day. `0` means the next daily check purges every completed torrent. |

Keep this long enough for the tracker's ratio rules. See [OPERATIONS.md](OPERATIONS.md#ratio-and-seeding).

### `stall_after_hours`

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `stall_after_hours` | number | `24` | no | Hours without download progress before a job is marked `stalled`. A stalled job is still polled, and goes back to `downloading` if progress resumes. |

### `abandon_after_days`

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `abandon_after_days` | number | `14` | no | Days without progress before a job is `abandoned`. The torrent and its partial data are removed. Retry starts it over. `0` means never give up. |

### `art`

Cover art sources. Priority and cache are described in [TAGGING.md](TAGGING.md#cover-art).

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `art.use_torrent_image` | boolean | `true` | yes | Use an image inside the torrent folder, if one is found. |
| `art.use_dmbalmanac` | boolean | `true` | yes | If there is no image in the torrent, use the dmbalmanac poster for the date. |

Turning both off means no cover, and a warning on the job.

```json
"art": {
  "use_torrent_image": true,
  "use_dmbalmanac": true
}
```

### `convert_to_flac`

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `convert_to_flac` | boolean | `true` | yes | Convert SHN, APE and WAV to FLAC with ffmpeg. When off, those files are copied as they are, and each gets a warning. |

### `post_publish_command`

| Key | Type | Default | UI | Meaning |
|---|---|---|---|---|
| `post_publish_command` | string | `""` | yes | Shell command run after a show is published. Empty means off. See below. |

The command runs with `/bin/sh -c`, inside the `dtdl-app` container, as uid 997. The library folder's container path is passed as `$1`. Its output is recorded on the job as `post_publish_command exit <code>`. It has a 300-second timeout. A failure never fails the job.

The image has `sh` and Python 3, but no `curl`. To call an HTTP endpoint from the command, use Python's `urllib`. The owner's Plex refresh script goes here later.

## Secrets

`data/config.json` holds two secrets: the dreamingtree password and the qBittorrent WebUI password.

- **Mode 660, owned by your user and group.** The app container runs as uid 997 with your group (`HOST_GID`) added in `docker-compose.yml`. It reads and writes the file through the group. Other users have no access.
- **Saves are atomic.** The app writes `data/config.tmp`, sets mode 660, then renames it over `config.json`.
- **Check the mode after a hand edit.** Some editors create a new file and lose the mode. Run `chmod 660 data/config.json` after you edit it. `preflight.sh` fails if the mode is not 660, and fails if uid 997 cannot read the file.
- **The API never returns passwords.** `GET /api/settings` gives `dreamingtree.password_set` instead of the value. The qBittorrent password is not returned at all. A save with a blank password keeps the stored one.
- **Nothing prints them.** Preflight prints only PASS and FAIL lines, and the code never logs a password. The Settings page shows only whether one is set.
- **Do not put a password in the repo, in a fixture or in a doc.** `.gitignore` and `.dockerignore` both exclude `data/`.
- `data/dreamingtree.cookies` (mode 600, owned by uid 997) holds the site session cookies. It is not a secret you type, but treat it the same way: back up `config.json` carefully, and the cookies file is safe to delete.

To read a value without printing the file:

```sh
jq -r '.qbittorrent.username' data/config.json
```

Avoid `cat data/config.json` on a shared screen.

## Environment variables

| Variable | Default | Where set | Meaning |
|---|---|---|---|
| `DTDL_CONFIG` | `/app/data/config.json` | compose: `/app/data/config.json` | Path of the config file in the app container. |
| `DTDL_FAKE` | `1` in code, `0` in compose | compose sets `0` | `1` serves in-memory demo data and makes no network calls. `0` runs the real backend. |

## Full example

This matches `config.example.json` with a real destination and the default templates:

```json
{
  "dreamingtree": {
    "base_url": "https://dreamingtree.org",
    "username": "<username>",
    "password": "<password>"
  },
  "qbittorrent": {
    "url": "http://qbittorrent:8080",
    "username": "admin",
    "password": "<qbittorrent webui password>",
    "category": "dreamingtree-dl"
  },
  "paths": {
    "staging": "/staging",
    "library_root": "/library"
  },
  "library_mounts": {
    "/srv/music/": "/library/"
  },
  "destinations": [
    {
      "label": "Dave Matthews Band",
      "path": "/srv/music/",
      "folder_template": {
        "dmb": "DMB_Live/{torrent_name}",
        "default": "Dave_Tim/{torrent_name}"
      }
    }
  ],
  "retention_days": 90,
  "stall_after_hours": 24,
  "abandon_after_days": 14,
  "art": {
    "use_torrent_image": true,
    "use_dmbalmanac": true
  },
  "convert_to_flac": true,
  "post_publish_command": ""
}
```

`stall_after_hours` and `abandon_after_days` are not in `config.example.json`, but the app uses them with the defaults shown.
