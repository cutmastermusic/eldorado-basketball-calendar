# Eldorado Basketball → Google Calendar

Automatically pulls the Eldorado Eagles basketball schedule from MaxPreps
every morning and publishes it as a calendar feed (`schedule.ics`) that
Google Calendar, Apple Calendar, and Outlook can subscribe to. When
MaxPreps changes (new game time, playoff games added), everyone's
calendar updates automatically — nobody ever re-imports anything.

## One-time setup (~15 minutes, no coding)

### 1. Create the GitHub repo
1. Sign up / sign in at [github.com](https://github.com) (free).
2. Click **New repository**. Name it `eldorado-basketball-calendar`.
   Set it to **Public** (required for the free calendar URL). Click **Create**.

### 2. Upload these files
1. On the new repo page, click **uploading an existing file**.
2. Drag in `maxpreps_to_ics.py` and `README.md`, click **Commit changes**.
3. The workflow file must go in a specific folder, so add it separately:
   click **Add file → Create new file**, and for the filename type
   exactly: `.github/workflows/update-schedule.yml`
   (typing the `/` creates the folders). Paste the contents of
   `update-schedule.yml` into the editor and click **Commit changes**.

### 3. Run it once
1. Go to the **Actions** tab of your repo.
2. If prompted, click **I understand my workflows, enable them**.
3. Click **Update basketball schedule** on the left → **Run workflow** → green **Run workflow** button.
4. Wait ~30 seconds, refresh — you should see a green checkmark, and a
   `schedule.ics` file will now exist in your repo.

### 4. Get your calendar URL
Your feed URL is:

```
https://raw.githubusercontent.com/YOUR_USERNAME/eldorado-basketball-calendar/main/schedule.ics
```

Replace `YOUR_USERNAME` with your GitHub username. (You can also click
the `schedule.ics` file in your repo, then the **Raw** button, and copy
the address bar.)

### 5. Subscribe in Google Calendar (each person does this once)
**On a computer** (this step needs a browser, not the phone app):
1. Open [calendar.google.com](https://calendar.google.com).
2. Left sidebar → next to **Other calendars**, click **+** → **From URL**.
3. Paste the feed URL → **Add calendar**.

The calendar then appears on phones automatically (in the Google Calendar
app: Settings → your account → make sure the new calendar is checked/synced).
Send your wife the same URL — she adds it to her own Google account the
same way.

**Apple Calendar:** File → New Calendar Subscription → paste URL.

## What you get
- 🏀 Every game with correct date, time, and Mountain Time zone handling
- Home games located at Eldorado HS (address included — tap for directions)
- Away games marked `@ Opponent`
- District games labeled
- A 1-hour-before reminder on every game
- Daily automatic refresh at ~6 AM

## Good to know
- **Google refreshes subscribed calendars on its own schedule — typically
  every 8–24 hours.** A schedule change on MaxPreps can take up to a day
  to appear on your phone. The daily 6 AM scrape + Google's refresh
  usually means changes appear within a day.
- **If MaxPreps redesigns their site** and the scraper breaks, the daily
  Action will show a red ❌ in the Actions tab and GitHub emails you.
  The old schedule stays in place (nothing gets deleted); the script just
  needs a parser tweak.
- **New season?** Edit `maxpreps_to_ics.py` — the config block at the top
  has the URL, team name, and home venue. MaxPreps keeps the same URL per
  team, so it usually rolls over automatically.
- **Different team / your other kids?** Copy the repo, change
  `SCHEDULE_URL` in the config block to any MaxPreps schedule page
  (JV, freshman, another sport, another school — the URLs are on the
  team page dropdown), and you have another feed.
