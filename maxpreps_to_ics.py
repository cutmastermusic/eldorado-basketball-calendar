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
     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ----------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------

def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text


# ----------------------------------------------------------------------
# Parse strategy 1: __NEXT_DATA__ JSON (MaxPreps is a Next.js site).
# We walk the JSON looking for contest/game objects rather than
# hard-coding a path, so minor site refactors don't break us.
# ----------------------------------------------------------------------

def parse_next_data(html: str):
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
    )
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None

    games = []

    def walk(node):
        if isinstance(node, dict):
            # Heuristic: a game object has a date and an opponent-ish field
            keys = {k.lower() for k in node.keys()}
            has_date = keys & {"date", "contestdate", "dateutc", "contestdateutc", "datelocal"}
            has_opp = keys & {"opponent", "opponentname", "opponentschoolname", "title"}
            if has_date and has_opp:
                games.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    if not games:
        return None

    parsed = []
    for g in games:
        lower = {k.lower(): v for k, v in g.items()}
        date_raw = (lower.get("datelocal") or lower.get("contestdate")
                    or lower.get("date") or lower.get("dateutc")
                    or lower.get("contestdateutc"))
        opp = (lower.get("opponentname") or lower.get("opponentschoolname")
               or lower.get("opponent") or "")
        if isinstance(opp, dict):
            opp = opp.get("name") or opp.get("schoolName") or ""
        if not date_raw or not opp:
            continue
        dt = None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%m/%d/%Y %I:%M %p"):
            try:
                dt = datetime.strptime(str(date_raw)[:26].rstrip("Z"), fmt)
                break
            except ValueError:
                continue
        if dt is None:
            continue
        home_away = str(lower.get("homeawaytype", lower.get("homeaway", ""))).lower()
        is_home = ("home" in home_away) if home_away else None
        parsed.append({
            "dt": dt,
            "opponent": str(opp).strip(),
            "is_home": is_home,
            "district": bool(lower.get("isdistrict") or lower.get("isconference")),
            "uidsrc": str(lower.get("contestid") or lower.get("id") or ""),
        })
    return parsed or None


# ----------------------------------------------------------------------
# Parse strategy 2: fallback — game links + row text.
# Game URLs embed the date (…/11-24-2026/?c=<contest-uuid>) which is
# very stable. Time and opponent come from the surrounding row text.
# ----------------------------------------------------------------------

GAME_LINK_RE = re.compile(
    r'href="(?:https://www\.maxpreps\.com)?(/[a-z]{2}/[a-z-]+/game/[^"]*?/'
    r'(\d{1,2})-(\d{1,2})-(\d{4})/\?c=([0-9a-f-]{36}))"',
    re.IGNORECASE,
)
TIME_RE = re.compile(r'(\d{1,2}):(\d{2})\s*(am|pm)', re.IGNORECASE)


def parse_fallback(html: str):
    games = {}
    for m in GAME_LINK_RE.finditer(html):
        path, mo, day, yr, cid = m.groups()
        if cid in games:
            continue
        # Look at a window of HTML after the link for time / opponent / @ vs
        window = html[m.start(): m.start() + 3000]
        hour, minute = (19, 0)  # default 7pm if TBA
        # The game link's inner text is like "11/247:00pm" (date+time mashed).
        # Grab that text, strip the known "M/D" date prefix, parse the rest.
        link_text_m = re.search(r'>([^<]*)</a>', window)
        tm = None
        if link_text_m:
            txt = link_text_m.group(1).strip()
            prefix = f"{int(mo)}/{int(day)}"
            if txt.startswith(prefix):
                txt = txt[len(prefix):]
            tm = TIME_RE.match(txt.strip())
        if tm is None:
            tm = TIME_RE.search(window)  # last resort
        if tm:
            hour = int(tm.group(1)) % 12
            if tm.group(3).lower() == "pm":
                hour += 12
            minute = int(tm.group(2))
        # Home/away: MaxPreps prefixes opponent with '@' (away) or 'vs'
        is_home = None
        at_pos = re.search(r'>\s*@', window)
        vs_pos = re.search(r'>\s*vs', window, re.IGNORECASE)
        if at_pos and (not vs_pos or at_pos.start() < vs_pos.start()):
            is_home = False
        elif vs_pos:
            is_home = True
        # Opponent name: text of the school link in this row
        opp = ""
        om = re.search(
            r'href="(?:https://www\.maxpreps\.com)?/[a-z]{2}/[a-z-]+/'
            r'([a-z0-9-]+)/(?:basketball|football|baseball|soccer|volleyball|softball)[^"]*"[^>]*>(.*?)</a>',
            window, re.IGNORECASE | re.DOTALL)
        if om:
            opp = re.sub(r'<[^>]+>', '', om.group(2))
            opp = re.sub(r'\s+', ' ', opp).strip()
            opp = re.sub(r'^(@|vs\.?)\s*', '', opp, flags=re.IGNORECASE)
        district = '*' in (opp or '')
        opp = opp.replace('*', '').strip()
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
        star = " (District)" if g.get("district") else ""
        summary = f"🏀 {TEAM_NAME.split()[0]} {prefix} {g['opponent']}{star}"
        if g["is_home"]:
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
    games = parse_next_data(html) or parse_fallback(html)
    if not games:
        print("ERROR: no games parsed — MaxPreps layout may have changed.", file=sys.stderr)
        sys.exit(1)
    ics = build_ics(games)
    with open(OUTPUT_FILE, "w", newline="") as f:
        f.write(ics)
    print(f"Wrote {OUTPUT_FILE} with {len(games)} games:")
    for g in sorted(games, key=lambda x: x["dt"]):
        ha = "vs" if g["is_home"] else ("@ " if g["is_home"] is False else "??")
        print(f"  {g['dt']:%a %m/%d %I:%M%p}  {ha} {g['opponent']}")


if __name__ == "__main__":
    main()
