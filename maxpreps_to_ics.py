#!/usr/bin/env python3
"""
MaxPreps schedule -> iCalendar (.ics) generator.

Fetches a MaxPreps team schedule page, parses the games, and writes
schedule.ics — a calendar file that Google Calendar (or Apple/Outlook)
can subscribe to by URL. Re-run daily via GitHub Actions so schedule
changes flow into everyone's calendars automatically.

Configure via the CONFIG section below (or environment variables).
"""

import json
import os
import re
import sys
import hashlib
from datetime import datetime, timedelta

import requests

# ----------------------------------------------------------------------
# CONFIG — edit these for your team
# ----------------------------------------------------------------------
SCHEDULE_URL = os.environ.get(
    "MAXPREPS_URL",
    "https://www.maxpreps.com/nm/albuquerque/eldorado-golden-eagles/basketball/schedule/",
)
TEAM_NAME = os.environ.get("TEAM_NAME", "Eldorado Eagles Basketball")
CALENDAR_NAME = os.environ.get("CALENDAR_NAME", "Eldorado Basketball")
TIMEZONE = os.environ.get("TEAM_TZ", "America/Denver")  # New Mexico
HOME_VENUE = os.environ.get(
    "HOME_VENUE", "Eldorado High School, 11300 Montgomery Blvd NE, Albuquerque, NM 87111"
)
GAME_DURATION_HOURS = 2
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "schedule.ics")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

BROWSER_HEADERS = {
    "User-Agent": UA,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.maxpreps.com/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# ----------------------------------------------------------------------
# Fetch — three strategies, in order:
#   1. requests with full browser-like headers
#   2. cloudscraper (mimics real browser TLS fingerprint; beats most bot walls)
#   3. r.jina.ai reader proxy (fetches the page for us, returns markdown —
#      the fallback parser understands markdown links too)
# ----------------------------------------------------------------------

def fetch_html(url: str) -> str:
    errors = []

    try:
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
        r.raise_for_status()
        print("Fetched via requests+headers")
        return r.text
    except Exception as e:
        errors.append(f"requests: {e}")

    try:
        import cloudscraper
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        r = s.get(url, timeout=45)
        r.raise_for_status()
        print("Fetched via cloudscraper")
        return r.text
    except Exception as e:
        errors.append(f"cloudscraper: {e}")

    try:
        r = requests.get(
            "https://r.jina.ai/" + url,
            headers={"User-Agent": UA, "Accept": "text/plain"},
            timeout=60,
        )
        r.raise_for_status()
        print("Fetched via r.jina.ai proxy")
        return r.text
    except Exception as e:
        errors.append(f"jina proxy: {e}")

    raise RuntimeError("All fetch strategies failed:\n  " + "\n  ".join(errors))


# ----------------------------------------------------------------------
# Parse strategy 1: schema.org JSON-LD (primary).
# MaxPreps embeds <script type="application/ld+json"> with SportsEvent
# entries carrying exact UTC start times, home/away teams, and venues.
# ----------------------------------------------------------------------

LDJSON_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def parse_ld_json(html: str):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = None

    events = []

    def scan(node):
        if isinstance(node, list):
            for v in node:
                scan(v)
        elif isinstance(node, dict):
            if node.get("@type") == "SportsEvent":
                events.append(node)
            for v in node.values():
                scan(v)

    for m in LDJSON_RE.finditer(html):
        try:
            scan(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
    if not events:
        return None

    our_key = TEAM_NAME.split()[0].lower()  # e.g. "eldorado"
    parsed = []
    for e in events:
        start = e.get("startDate")
        home = (e.get("homeTeam") or {}).get("name", "")
        away = (e.get("awayTeam") or {}).get("name", "")
        if not start or not (home or away):
            continue
        try:
            dt = datetime.fromisoformat(start)
        except ValueError:
            continue
        if dt.tzinfo is not None and tz is not None:
            dt = dt.astimezone(tz).replace(tzinfo=None)
        elif dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)

        if our_key in home.lower():
            is_home, opp = True, away
        elif our_key in away.lower():
            is_home, opp = False, home
        else:
            is_home, opp = None, away or home
        opp = re.sub(r"\s+High School$", "", opp).strip()

        loc = e.get("location") or {}
        addr = loc.get("address") or {}
        loc_parts = [loc.get("name", ""), addr.get("streetAddress", ""),
                     addr.get("addressLocality", ""), addr.get("addressRegion", "")]
        location = ", ".join(p.strip() for p in loc_parts if p and p.strip())

        cid = ""
        mu = re.search(r"c=([0-9a-f-]{36})", e.get("url") or "")
        if mu:
            cid = mu.group(1)
        parsed.append({
            "dt": dt, "opponent": opp, "is_home": is_home,
            "district": False, "uidsrc": cid, "location": location,
        })
    return parsed or None


# ----------------------------------------------------------------------
# Parse strategy 2: fallback — game links + row text.
# Game URLs embed the date (…/11-24-2026/?c=<contest-uuid>) which is
# very stable. Time and opponent come from the surrounding row text.
# ----------------------------------------------------------------------

# Matches game URLs in HTML (href="...") OR markdown ((https://...)) output
GAME_LINK_RE = re.compile(
    r'(?:https://www\.maxpreps\.com)?(/[a-z]{2}/[a-z-]+/game/[^"()\s]*?/'
    r'(\d{1,2})-(\d{1,2})-(\d{4})/\?c=([0-9a-f-]{36}))',
    re.IGNORECASE,
)
# Opponent school link: HTML <a href=...>NAME</a> or markdown [NAME](url)
OPP_HTML_RE = re.compile(
    r'href="(?:https://www\.maxpreps\.com)?/[a-z]{2}/[a-z-]+/'
    r'[a-z0-9-]+/(?:basketball|football|baseball|soccer|volleyball|softball)[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL)
OPP_MD_RE = re.compile(
    r'\[((?:@|vs\.?)\s*(?:!\[[^\]]*\]\([^)]*\)\s*)?[^\]\[]*)\]'
    r'\((?:https://www\.maxpreps\.com)?/[a-z]{2}/[a-z-]+/'
    r'[a-z0-9-]+/(?:basketball|football|baseball|soccer|volleyball|softball)',
    re.IGNORECASE | re.DOTALL)
TIME_RE = re.compile(r'(\d{1,2}):(\d{2})\s*(am|pm)', re.IGNORECASE)


def parse_fallback(html: str):
    games = {}
    for m in GAME_LINK_RE.finditer(html):
        path, mo, day, yr, cid = m.groups()
        if cid in games:
            continue
        # Look at a window of text after the link for time / opponent / @ vs.
        # Works on both raw HTML and markdown (jina proxy output).
        window = html[m.start(): m.start() + 3000]
        hour, minute = (19, 0)  # default 7pm if TBA
        # The game link's text is like "11/247:00pm" (date+time mashed).
        # HTML: >text</a>   Markdown: [text](url)
        tm = None
        txt = None
        html_txt = re.search(r'>([^<]*)</a>', window)
        if html_txt and '<a' not in window[:html_txt.start()]:
            txt = html_txt.group(1)
        elif m.start() >= 2 and html[m.start() - 1] == '(' and html[m.start() - 2] == ']':
            # Markdown: [11/247:00pm](URL) — our URL match starts right after "]("
            j = html.rfind('[', max(0, m.start() - 200), m.start() - 2)
            if j != -1:
                txt = html[j + 1: m.start() - 2]
        if txt:
            txt = txt.strip()
            prefix = f"{int(mo)}/{int(day)}"
            if txt.startswith(prefix):
                txt = txt[len(prefix):]
            tm = TIME_RE.match(txt.strip())
        if tm is None:
            tm = TIME_RE.search(window[:400])  # last resort, stay in this row
        if tm:
            hour = int(tm.group(1)) % 12
            if tm.group(3).lower() == "pm":
                hour += 12
            minute = int(tm.group(2))
        # Opponent name + home/away from the school link in this row
        opp = ""
        om = OPP_HTML_RE.search(window)
        if om:
            opp = re.sub(r'<[^>]+>', '', om.group(1))
        else:
            om = OPP_MD_RE.search(window)
            if om:
                opp = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', om.group(1))  # strip mascot img
        opp = re.sub(r'\s+', ' ', opp).strip()
        # Home/away: MaxPreps prefixes opponent with '@' (away) or 'vs' (home)
        is_home = None
        if opp.startswith('@'):
            is_home = False
        elif re.match(r'vs\.?\s*', opp, re.IGNORECASE):
            is_home = True
        opp = re.sub(r'^(@|vs\.?)\s*', '', opp, flags=re.IGNORECASE)
        district = '*' in (opp or '')
        opp = opp.replace('*', '').replace('\\', '').strip()
        if not opp:
            # last resort: derive from URL slug
            slug = path.split('/game/')[1].split('/')[0]
            parts = slug.split('-vs-')
            opp = parts[-1].replace('-', ' ').title()
        try:
            dt = datetime(int(yr), int(mo), int(day), hour, minute)
        except ValueError:
            continue
        games[cid] = {
            "dt": dt, "opponent": opp, "is_home": is_home,
            "district": district, "uidsrc": cid,
        }
    return list(games.values()) or None


# ----------------------------------------------------------------------
# ICS generation
# ----------------------------------------------------------------------

def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


VTIMEZONE_DENVER = """BEGIN:VTIMEZONE
TZID:America/Denver
BEGIN:DAYLIGHT
TZOFFSETFROM:-0700
TZOFFSETTO:-0600
TZNAME:MDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0600
TZOFFSETTO:-0700
TZNAME:MST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def build_ics(games: list) -> str:
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//maxpreps-to-ics//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(CALENDAR_NAME)}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
        VTIMEZONE_DENVER,
    ]
    for g in sorted(games, key=lambda x: x["dt"]):
        prefix = "vs" if g["is_home"] else ("@" if g["is_home"] is False else "vs")
        if g["is_home"]:
            ha_label = " — HOME"
        elif g["is_home"] is False:
            ha_label = " — AWAY"
        else:
            ha_label = ""
        star = " (District)" if g.get("district") else ""
        summary = f"🏀 {TEAM_NAME.split()[0]} {prefix} {g['opponent']}{ha_label}{star}"
        if g.get("location"):
            location = g["location"]
        elif g["is_home"]:
            location = HOME_VENUE
        elif g["is_home"] is False:
            location = f"Away — at {g['opponent']}"
        else:
            location = ""
        uid_seed = g.get("uidsrc") or f"{g['dt'].isoformat()}|{g['opponent']}"
        uid = hashlib.md5(uid_seed.encode()).hexdigest() + "@maxpreps-to-ics"
        end = g["dt"] + timedelta(hours=GAME_DURATION_HOURS)
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"DTSTART;TZID={TIMEZONE}:{fmt_dt(g['dt'])}",
            f"DTEND;TZID={TIMEZONE}:{fmt_dt(end)}",
            f"SUMMARY:{ics_escape(summary)}",
        ]
        if location:
            lines.append(f"LOCATION:{ics_escape(location)}")
        lines.append(f"DESCRIPTION:{ics_escape('Schedule source: ' + SCHEDULE_URL)}")
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "DESCRIPTION:Game reminder",
            "TRIGGER:-PT1H",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # Fold long lines per RFC 5545 (75 octets)
    out = []
    for ln in lines:
        for sub in ln.split("\n"):
            while len(sub.encode()) > 73:
                cut = 73
                while (sub.encode()[:cut][-1] & 0xC0) == 0x80:  # don't split UTF-8
                    cut -= 1
                out.append(sub.encode()[:cut].decode())
                sub = " " + sub.encode()[cut:].decode()
            out.append(sub)
    return "\r\n".join(out) + "\r\n"


def main():
    html = fetch_html(SCHEDULE_URL)
    games = parse_ld_json(html) or parse_fallback(html)
    if not games:
        print("ERROR: no games parsed — MaxPreps layout may have changed.", file=sys.stderr)
        sys.exit(1)
    ics = build_ics(games)
    with open(OUTPUT_FILE, "w", newline="") as f:
        f.write(ics)
    print(f"Wrote {OUTPUT_FILE} with {len(games)} games:")
    for g in sorted(games, key=lambda x: x["dt"]):
        if g["is_home"]:
            ha = "vs"
            lbl = "HOME"
        elif g["is_home"] is False:
            ha = "@ "
            lbl = "AWAY"
        else:
            ha, lbl = "??", "?"
        print(f"  {g['dt']:%a %m/%d %I:%M%p}  {ha} {g['opponent']} [{lbl}]")


if __name__ == "__main__":
    main()
