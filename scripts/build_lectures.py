#!/usr/bin/env python3
"""Build lectures.json from the public playlists of a YouTube channel.

Runs inside GitHub Actions (.github/workflows/update-lectures.yml).
No API key needed: yt-dlp is used in flat mode (metadata only, nothing is downloaded).

Rules
- Every public playlist of the channel becomes one group, named after the playlist.
- Playlists listed in lectures-config.json "exclude" are skipped.
- lectures-config.json "playlists" can give a playlist an English title, a subtitle,
  a kind ("course" or "other"), a sort mode ("number") and text to strip from video titles.
- Playlists are discovered with yt-dlp and, as a second source, from the channel page itself.
  Optional "include" in the config adds playlist ids by hand. Extras must belong to the channel.
- Private or deleted videos are skipped.
- If a playlist cannot be read this time, its last good copy in lectures.json is kept.
- lectures.json is rewritten only when something really changed.
"""
import json
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "lectures-config.json"
OUT = ROOT / "lectures.json"


def ytdlp(url):
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "--dump-single-json",
           "--no-warnings", "--sleep-requests", "1", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "yt-dlp failed").strip()[-600:])
    return json.loads(r.stdout)


def playlist_id(entry):
    m = re.search(r"list=([\w-]+)", entry.get("url") or "")
    return m.group(1) if m else entry.get("id")


def scrape_ids(channel):
    """Second source: playlist ids that appear in the channel's Playlists page."""
    try:
        req = urllib.request.Request(
            channel.rstrip("/") + "/playlists?hl=en",
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                     "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1"})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as ex:
        print("WARN page scrape: %s" % ex, file=sys.stderr)
        return []
    ids = []
    for m in re.finditer(r'"(?:contentId|playlistId)":"((?:PL|OLAK5uy_)[\w-]+)"', html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def clean(title, strip):
    for s in strip:
        title = title.replace(s, "")
    title = re.sub(r"Lec0*(\d+)(?=_|\s|$)", r"Lecture \1", title)
    title = re.sub(r"(Lecture \d+)_(Morning|Evening)", r"\1 · \2", title)
    title = title.replace("_", " ")
    return re.sub(r"\s+", " ", title).strip(" |-–·")


def number_key(video):
    nums = re.findall(r"\d+", video["title"])
    return (int(nums[-1]) if nums else 10**6, video["title"])


def main():
    cfg = json.loads(CONFIG.read_text("utf-8"))
    conf = cfg.get("playlists", {})
    exclude = set(cfg.get("exclude", []))

    previous = {}
    if OUT.exists():
        try:
            previous = json.loads(OUT.read_text("utf-8"))
        except ValueError:
            previous = {}
    old = {p["id"]: p for p in previous.get("playlists", [])}

    listing = ytdlp(cfg["channel"].rstrip("/") + "/playlists")
    found, titles = [], {}
    for e in listing.get("entries") or []:
        pid = playlist_id(e)
        if pid and pid not in titles:
            titles[pid] = (e.get("title") or "").strip()
            found.append(pid)
    if not found:
        sys.exit("No playlists found - keeping the existing lectures.json")
    channel_id = listing.get("channel_id") or listing.get("uploader_id")

    include = cfg.get("include", [])
    scraped = [i for i in scrape_ids(cfg["channel"]) if i not in titles]
    extras = scraped + [i for i in include if i not in titles and i not in scraped]
    print("playlists: %d from yt-dlp, %d extra from page/include: %s" % (len(found), len(extras), extras))
    for i in extras:
        titles[i] = ""
        found.append(i)
    unverified = set(scraped) - set(include)  # must be checked against the channel id

    order = [i for i in conf if i in titles] + [i for i in found if i not in conf]

    result = []
    for pid in order:
        if pid in exclude:
            continue
        c = conf.get(pid, {})
        try:
            data = ytdlp("https://www.youtube.com/playlist?list=" + pid)
            owner = data.get("channel_id") or data.get("uploader_id")
            if pid in unverified and channel_id and owner != channel_id:
                print("skip %s: belongs to another channel" % pid)
                continue
            videos = []
            for e in data.get("entries") or []:
                t = (e.get("title") or "").strip()
                if not e.get("id") or t.startswith("["):  # [Private video], [Deleted video]
                    continue
                videos.append({"id": e["id"], "title": clean(t, c.get("strip", []))})
            if c.get("sort") == "number":
                videos.sort(key=number_key)
            entry = {
                "id": pid,
                "title": c.get("title") or titles[pid] or data.get("title") or pid,
                "sub": c.get("sub", ""),
                "kind": c.get("kind", "other"),
                "videos": videos,
            }
        except Exception as ex:  # keep the last good copy of this playlist
            print("WARN %s: %s" % (pid, ex), file=sys.stderr)
            entry = old.get(pid)
            if not entry:
                continue
        if entry["videos"]:
            result.append(entry)

    if not result:
        sys.exit("Nothing to publish - keeping the existing lectures.json")

    new = {"playlists": result}
    prev = {k: v for k, v in previous.items() if k != "updated"}
    if prev == new:
        print("No changes (%d playlists)" % len(result))
        return
    new["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", "utf-8")
    print("Wrote lectures.json: %d playlists, %d videos" % (
        len(result), sum(len(p["videos"]) for p in result)))


if __name__ == "__main__":
    main()
