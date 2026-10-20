# Sample torrents

Downloaded with the app's own client into `nas:/export/dreamingtree-dl/torrents/` (qBittorrent, category
`dreamingtree-dl`). Each folder here holds the non-audio files and `FILE_LISTING.txt` (`ls -lR`).

## Completed

| Folder | Site id | Source | Why |
|---|---|---|---|
| `dm2020-07-03.siriusxm.web-dl.flac` | 4123 | SiriusXM (WEB), Dave solo | SiriusXM sample; flat folder, only an info `.txt`; no numbered setlist match with file names (fan request / "Dave Speak" tracks) |
| `dmb2019-03-08.Mitsubishi_Electric_Halle.Dusseldorf.srg.44KHz.16Bit.flac+mp3` | 3878 | Microphone (taper, SRG), non-US (Dusseldorf, Germany) | Non-US sample; `FLAC/` and `MP3/` subfolders, `.ffp`, `.m3u`, `.md5`; torrent holds both formats |

| `dmb2002-12-18.conan.flac16` | – | TV broadcast (Late Night with Conan O'Brien) | Broadcast/SBD-type source; sat at 0% for hours until its seeders came online, then completed |
| `dmb2025-05-27ck63.flac16 copy` | – | Microphone (taper, CK63) | Recent taper; the torrent folder name really ends in " copy" |
| `dmb1996-09-06.newsource.shnf` | 5509 (listed as `dmb1996-09-06.ecm909a.shnf`) | Microphone (taper, ECM-909A), **SHN** | Pre-2000 SHN: 12 `.shn` files over 2 discs. **The torrent folder name differs from the site's release name.** Info file misspells "Spetember", so the date must come from the release name. Real SHN→FLAC conversion verified (44.1 kHz/16-bit) |

## Given up (removed 2026-10-20, never progressed)

`dm2004-01-09.tv.flac16`, `dm1999-06-15.Q101.5.shnf`, `dm1997-01-24.fm.shnf` and `dmb1997-06-13.akg461.flac16` were stuck at 0% for hours. The tracker listed 3–4 seeders each, but none ever connected (qBittorrent itself was reachable: `connection_status: connected`). These were offline or unreachable seeders, not a local problem. This is the scenario the `stalled`/`abandoned` states handle.

