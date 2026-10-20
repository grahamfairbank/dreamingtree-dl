# Tagging

How a show's info file and torrent name become the tags on each FLAC file in the library. The code is in `app/infofile.py`, `app/sources.py`, `app/albumname.py`, `app/tagger.py`, `app/art.py` and `app/convert.py`. The pipeline steps that call them are in `app/pipeline.py`.

The album names are the author's convention, derived from 288 hand-tagged shows in `media-server:/srv/music/DMB_Live/PrevYear`.

## Album names

Format: `YYYY-MM-DD: <place>` followed, when there is one, by a space and a source suffix.

| Case | Place | Example |
|---|---|---|
| United States | `City, ST` (two-letter state code) | `2022-07-09: Saratoga Springs, NY` |
| District of Columbia and Puerto Rico | `City, DC` / `City, PR` | `2018-12-13: Washington, DC` |
| Canada | `City, ON` (province code) | `2018-07-10: Toronto, ON` |
| Canada, city only | `City, Canada` | `2002-04-20: Kanata, Canada` |
| United Kingdom (England, Scotland, Wales, Northern Ireland, UK, Great Britain) | `City, UK` | `2024-04-25: London, UK` |
| Other countries | `City, Country name` | `2024-04-07: Oslo, Norway` |
| No city and no region | the venue or show title, see below | `2020-07-03: Live From Home By Request siriusxm` |
| No location at all | the date only | `2020-07-03` |

Rules, from `app/albumname.py`:

- A US state or Canadian province is written as its two-letter code. State names are accepted in any case (`virginia`, `Virginia` and `VA` all give `VA`). `Washington D.C.`, `DC` and `D.C.` give `DC`.
- `NYC` and `New York City` give `New York, NY`.
- A country that is the United States is never written in the album name. It is only used for the region.
- A suffix is lowercased, so `SBD` becomes `sbd`. With no suffix there is no trailing space.

The date comes from the torrent name first (`dmb2025-07-12...`). If the name has no full date, the info file's date is used. If the two disagree, the torrent name wins.

### Examples from the plan and the tests

```
2022-07-09: Saratoga Springs, NY
2024-05-24: West Palm Beach, FL siriusxm
1994-10-27: Madison, WI sbd
2018-07-10: Toronto, ON
2024-04-25: London, UK
2024-04-07: Oslo, Norway
2022-07-09: Edinburgh, UK fm
2022-07-09: Virginia Beach, VA matrix
2022-07-09: Dublin, Ireland webcast
2019-03-08: Dusseldorf, Germany siriusxm
```

The owner's older tags used country codes, such as `DEU`. The app writes the English country name.

### Venue fallback for shows with no location

Some broadcasts have no city, such as the 2020 "Live From Home" shows. Then the album uses the show's title in place of the city:

```
2020-07-03: Live From Home By Request siriusxm
```

When the app finds no location, it takes the first header line (within the first six plain lines) that is not the artist, a date, or a source or broadcast line. That line stands in for the city. If no line qualifies, the album is just the date.

Venue fallback does not apply when a city is known. The owner's older tags sometimes used a venue name instead (`2015-07-04: SPAC`, `2019-07-18: Jones Beach`). The app writes the city (`Saratoga Springs, NY`, `Wantagh, NY`) for those.

### Venue to town overrides

A few venues are tagged by the town the owner uses, not the mailing town. These are overridden in `app/infofile.py` when the venue and region match:

| Venue contains | Region | City used |
|---|---|---|
| `Gorge` | WA | `George` |
| `Alpine Valley` | WI | `Elkhorn` |
| `Darien Lake` | NY | `Darien Lake` |
| `Lake Tahoe` | NV | `Lake Tahoe` |

## Source suffixes

The suffix describes the recording source when it is not an audience or taper recording. Taper recordings have no suffix.

| Suffix | Meaning | Torrent-name tokens | Info-file cues |
|---|---|---|---|
| `siriusxm` | SiriusXM radio capture | `siriusxm`, `sirius`, `sxm`, `xm` | "SiriusXM", "sxm", "satellite radio" in the Source or Lineage lines |
| `sbd` | Soundboard, straight from the desk | `sbd`, `asbd`, `dsbd`, `fobsbd`, `soundboard` | "SBD", "soundboard", "board feed", "desk tape" |
| `fm` | FM radio broadcast | `fm`, `fmbroadcast` | "FM broadcast", "FM radio" |
| `webcast` | Online stream or web capture (includes YouTube and web rips) | `webcast`, `webdl`, `web`, `livestream`, `stream`, `youtube`, `webrip` | "webcast", "livestream", "web-dl", "youtube" |
| `matrix` | Soundboard feed mixed with audience microphones | `matrix`, `mtx` | "matrix"; or a source line that joins SBD and a microphone, such as `SBD & Schoeps MK-4V onstage mics > DAT` |
| `tv` | TV broadcast | `tv`, `hdtv`, `pbs`, `conan`, `letterman`, `fallon`, `kimmel`, `colbert`, `snl`, `tvrip` | "TV", "television", "PBS", "broadcast on <channel>" |

Priority, from `app/sources.py`:

1. **Torrent-name tokens first.** The first recognised token decides. `dmb1994.sbd.flac16` is `sbd` whatever the info file says.
2. **Then the info file's Source line and lineage lines.** These are used only when the name has no token.
3. **A microphone overrides a SiriusXM name token.** If the info file's source or equipment line names a microphone, the recording is a taper recording with no suffix, even when the name says `siriusxm`. A name with `+siriusxm` (for example `ck61+siriusxm`) is also a taper recording, unless the info file describes the SiriusXM stream with no microphone.
4. The result is always lowercase.

Examples from `tests/test_sources.py`:

| Torrent name | Suffix | Why |
|---|---|---|
| `dmb2025-07-11.siriusxm.flac16` | `siriusxm` | name token |
| `dmb2018-07-13.sirius-xm.flac16` | `siriusxm` | name token, with a hyphen |
| `dmb2023-07-14.sxm.flac16` | `siriusxm` | name token |
| `dmb2018-05-30.siriusxm.web-dl.flac16` | `siriusxm` | `web-dl` with `siriusxm` is the SiriusXM stream, not a webcast |
| `dmb1994-10-27.sbd.reconvert.flac16` | `sbd` | name token |
| `dmb1993-11-09.asbd.fixed-v2.flac` | `sbd` | name token |
| `dmb1993-05-27 (set I).dsbd - New York City` | `sbd` | name token; parenthesised text is ignored |
| `dmb2001-01-01.fm.flac16` | `fm` | name token |
| `dmb2024-07-20.webcast.flac16` | `webcast` | name token |
| `dmb2024-07-20.web-dl.flac16` | `webcast` | name token |
| `dm2026-10-03.youtube.flac16` | `webcast` | name token |
| `d+t2026-09-26.webrip.flac16` | `webcast` | name token |
| `dmb2001-01-01.matrix.flac16` | `matrix` | name token |
| `dmb2002-12-18.conan.flac16` | `tv` | name token |
| `dm2004-01-09.tv.flac16` | `tv` | name token |
| `dmb2025-07-12.ca-11.flac16` | (none) | `ca-11` is a microphone |
| `dmb2023-06-02.ck61+siriusxm.flac16` | (none) | taper microphone joined to SiriusXM |
| `dmb2005-05-09` with info line `Radio@AOL Webcast from "Stand Up" release party` | `webcast` | info-file cue, name has no token |
| `dmb2019-07-12` with info line `SiriusXM HLS "Extreme" quality stream > ffmpeg` | `siriusxm` | info-file cue |
| `dmb1993-07-14.new` with info line `ASBD (unknown generation) -> CD` | `sbd` | info-file cue |

## Tag mapping

What is written to each FLAC file (`app/tagger.py`). The tags the app owns are replaced on every tag run. Other tags, such as ReplayGain, fingerprints and the encoder, are left alone.

| Tag | Value | Source |
|---|---|---|
| `TITLE` | Setlist title, with `*`, `~`, `>` and time stamps removed | info-file setlist, matched by position |
| `ARTIST` | From the torrent's artist code: `dmb` gives `Dave Matthews Band`; `d&t` and `d+t` give `Dave Matthews & Tim Reynolds`; `dm` gives `Dave Matthews`; anything else gives the info file's artist, or `Unknown Artist` | torrent name |
| `ALBUMARTIST` | Same as `ARTIST` | torrent name |
| `ALBUM` | The album name, see above | computed |
| `DATE` | `YYYY-MM-DD`, taken from the album name, else the info file's date | album name |
| `GENRE` | `Live` | fixed |
| `TRACKNUMBER` | Position in the setlist, or the position within the disc on multi-disc shows | setlist |
| `TRACKTOTAL` | Number of setlist tracks (within the disc on multi-disc shows) | setlist |
| `DISCNUMBER` | Disc from the setlist: a heading such as `Disc 2` or `CD II`, or a `d2t01`-style track line. Written only when the setlist has more than one disc. | setlist |
| `DISCTOTAL` | Number of discs, only on multi-disc shows | setlist |
| `COMMENT` | The info file's lineage lines (`Taper: ...`, `Source: ...`, `Conversion: ...`). A track with a footnote adds `Note: <footnote>`. A track that segues into the next adds `Segues into the next track (>)`. | info file |
| embedded picture | Front cover (type 3), description `Cover`, JPEG or PNG only | cover art |

When the setlist and the file count do not match, titles are not replaced: the existing title is kept, with any leading track number removed (`01 Intro` becomes `Intro`), or the title is taken from the filename. `TRACKNUMBER` is the file order and `TRACKTOTAL` is the file count. No `DISCNUMBER` is written. A warning is recorded. See below.

Track files are also renamed after tagging (`app/pipeline.py`):

- Single disc: `01 Title.flac`, `02 Title.flac`.
- Multi-disc: `d1-01 Title.flac`, `d2-01 Title.flac`.
- Characters that are not safe in a file name are replaced with `_`. A duplicate title gets ` (2)`, ` (3)`.
- A `cover.jpg` is written next to the tracks in the library folder, from the same cover.

## Cover art

Priority, from `app/art.py`:

1. **An image inside the torrent folder.** The app looks through every image in the torrent folder.
   - Only files of 10 KB or more count.
   - Files with `back`, `tray` or `disc` in the name are used only when nothing else is there.
   - Files whose name has `cover`, `front`, `poster`, `folder` or the word `art` or `artwork` are preferred. Within each group, the largest file wins.
   - The file must start with a real JPEG, PNG, GIF or WEBP signature. A GIF or WEBP is found, but the tagger then warns that it is not embedded, since only JPEG and PNG are embedded.
2. **The dmbalmanac poster for the date.** The app reads the year's listing, finds the row for this date and the artist (`DMB`, `Dave & Tim`, or `Dave solo`), then fetches the poster. The site is reached at no more than one request a second.
3. **None.** Plex or the casting device then shows its generic art. The job gets the warning `no cover art found`.

The two sources can be turned off separately in Settings (`art.use_torrent_image` and `art.use_dmbalmanac`). Turning both off gives no cover.

### Cache

Under `data/cache/art/dmbalmanac/`, created on the first lookup:

| File | What | Lifetime |
|---|---|---|
| `listing-<year>.html` | the site's show listing for a year | past years never expire; the current year and later are refreshed after one day |
| `<artist>-<date>.media.html` | the poster's media page | kept |
| `<artist>-<date>.poster` | the poster image | kept |
| `<artist>-<date>.none` | "no poster for this show" | 30 days, then the site is asked again |

Artist slugs are `dmb`, `dt` (for `d&t`) and `dm`. Example: `dmb-2024-04-25.poster`.

If the directory is deleted, the next analysis fetches the listing for each year and the poster for each show again, at one request a second.

## Track numbering and renaming

Track numbers come from the setlist, not from the file names. The app matches the setlist to the audio files by position. This is why a mismatch is handled as a warning rather than guessed at.

- Files are sorted by natural order (`1, 2, 10`, not `1, 10, 2`) before matching.
- When the setlist has as many entries as there are audio files, entry *n* is file *n*.
- When the counts differ, nothing is matched. File order gives the track numbers, and the existing or filename-derived titles are kept. A warning explains which.

## SHN, APE and WAV to FLAC

When `convert_to_flac` is on (the default), each `.shn`, `.ape` or `.wav` file is decoded to FLAC with ffmpeg (`app/convert.py`):

- The output is written to the work copy. The torrent file is never touched.
- Sample rate is kept. Bit depth is kept: 16-bit sources give 16-bit FLAC; 24-bit sources are decoded to 32-bit working samples so the encoder records 24 bits.
- Metadata from the source is not copied. The tagger writes all tags after conversion.
- A timeout of 30 minutes applies per file.
- If ffmpeg fails, the job goes to `error` with a detail such as `[analysing] ffmpeg failed on <file>: ...`, holding the last 500 characters of ffmpeg's message. Tagging runs in the `analysing` state. Retry runs the step again.

When `convert_to_flac` is off, non-FLAC audio is copied unchanged and each file gets this warning: `<file>: not converted to FLAC (convert_to_flac is off)`.

## Warnings and errors

Warnings are kept on the job, shown under its row in Jobs, and do not stop it. Errors stop the job at the step that failed.

**Warnings:**

| Text | Meaning |
|---|---|
| `setlist has N tracks but there are M files; kept existing/filename titles, check the track titles` | Titles were not replaced. Check them, or check the setlist against the files. |
| `no setlist found in the info file; kept existing/filename titles` | No numbered setlist was found in the info file. |
| `no cover art found` | Neither source produced a cover. |
| `cover image is not JPEG or PNG; not embedded` | The found image is a GIF or WEBP. It is not embedded. |
| `<file>: not converted to FLAC (convert_to_flac is off)` | A non-FLAC file was copied as it is. |

**Errors** (job goes to `error`, shown in red, with `[<step>] <message>`):

| Message | Typical cause |
|---|---|
| `no info .txt file found in the torrent` | The torrent has no info file. Nothing can be tagged. |
| `could not determine the show date` | Neither the name nor the info file has a usable date. |
| `no audio files found in the torrent` | Only non-audio files. |
| `ffmpeg failed on <file>: ...` | Corrupt or unsupported audio. |
| `destination already exists and is not empty: <path>` | The library already has that folder. See [OPERATIONS.md](OPERATIONS.md). |
| `torrent content not found at <path>` | The torrent's files are not where qBittorrent says. |

Info files are heuristic. When the setlist parses but the count is wrong, the app prefers a visible warning to a wrong title.

## Regression corpus

`tests/fixtures/info/` holds 275 info files from the author's hand-tagged library, with the album name the author used for each (`expected.json`). The test (`tests/test_infofile.py`) builds an album name for each from the folder name and info file, and compares it with the author's tag.

Current result, from `tests/fixtures/info/MISMATCHES.md`:

| Measure | Count | Rate |
|---|---|---|
| Shows in corpus | 275 | |
| Exact match | 211 | 76.7% |
| Exact match, plus accepted author conventions | 250 | 90.9% |

The test fails if the exact rate drops below 76.5% or the adjusted rate below 90.5%. Those thresholds sit just under the current numbers.

The mismatches are classified in `MISMATCHES.md`. Most are author conventions the app deliberately does not copy:

- venue or nickname in place of the city (18 shows)
- author dropped the suffix (9 shows, not accepted)
- author used a country code such as `NL` or `DEU` (5 shows, not accepted)
- author typos, punctuation, whitespace, dropped apostrophes, extra text, and date typos (accepted)
- real info-file errors, such as a city or state typo (3 shows, accepted)

Run the corpus:

```sh
scripts/test.sh tests/test_infofile.py
```

After a deliberate rule change, regenerate the mismatch list. Do not edit `MISMATCHES.md` by hand:

```sh
UPDATE_MISMATCHES=1 scripts/test.sh tests/test_infofile.py
```

`test_mismatches_file_is_current` fails if the file is out of date.
