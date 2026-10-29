#!/usr/bin/env python3
"""Build lectures.json from the public playlists of a YouTube channel.

Runs inside GitHub Actions (.github/workflows/update-lectures.yml).
No API key needed: yt-dlp is used in flat mode (metadata only, nothing is downloaded).

Rules
- Every public playlist of the channel becomes one group, named after the playlist.
- Playlists listed in lectures-config.json "exclude" are skipped.
- lectures-config.json "playlists" can give a playlist an English title, a subtitle,
  a kind ("course" or "other"), a sort mode ("number") and text to strip from video titles.
- Private or deleted videos are skipped.
- If a playlist cannot be read this time, its last good copy in lectures.json is kept.
- lectures.json is rewritten only when something really changed.
"""
import json
import re
import subprocess
import sys
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

    order = [i for i in conf if i in titles] + [i for i in found if i not in conf]

    result = []
    for pid in order:
        if pid in exclude:
            continue
        c = conf.get(pid, {})
        try:
            data = ytdlp("https://www.youtube.com/playlist?list=" + pid)
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
