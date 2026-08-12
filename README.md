# 🌊 Swell Alert v2 — Wrightsville Beach

Free, serverless swell forecasting with real planning lead time, a live buoy confirmation layer, and a feedback loop that learns what actually makes Wrightsville good.

## The three warning tiers

| Tier | Lead time | Source | What it means |
|---|---|---|---|
| 🔭 **OUTLOOK** | **3–7 days** | Open-Meteo marine model (WaveWatch-class) | A swell day is projected — start planning |
| 📡 **INCOMING** | **24–48 hrs** | Same model, refined | Lock in your window; wind forecast included |
| 🌊/🔥 **LIVE** | **0–18 hrs** | NDBC buoys 41013 + 41110 | Energy confirmed in the water. 🔥 = 14s+ at 3ft+ |

Every alert includes **swell direction** (E–S is Wrightsville's ideal window, flagged ✅/⚠️), **wind speed and direction** (offshore NW–NE under 15kt = "IDEAL"), and the 8am dawn-patrol wind forecast on OUTLOOK/INCOMING emails.

## The feedback loop

Every LIVE alert email ends with a one-tap link to log your session — it opens a pre-filled GitHub issue. Type a rating (1–10) and optional notes, submit, done. A bot automatically:
1. Pairs your rating with the exact buoy conditions at that moment
2. Appends it to `sessions.csv`
3. Closes the issue with a 🤙

Buddies on the recipient list can rate too (they just need free GitHub accounts). Meanwhile `observations.csv` silently logs every hourly buoy reading. **Every month or two, paste both CSVs into Claude** and ask it to recalibrate — it'll find patterns like "SE at 14s+ averages 8/10 but E at 12s averages 4/10" and tell you exactly which thresholds to tighten so alerts match *your* definition of good.

## Setup (15 minutes, one time)

1. **Create repo:** github.com → New repository → `swell-alert` → **Private** → Create. Upload all files, keeping the `.github/workflows/` folder structure intact.
2. **Gmail app password:** myaccount.google.com → Security → enable 2-Step Verification → search "App passwords" → create one → copy the 16 characters.
3. **Add secrets:** repo Settings → Secrets and variables → Actions → add:
   - `GMAIL_ADDRESS` — your Gmail
   - `GMAIL_APP_PASSWORD` — the 16-char password
   - `RECIPIENTS` — comma-separated: `you@gmail.com, buddy@gmail.com, 9105551234@tmomail.net`
4. **Enable + test:** Actions tab → enable workflows → Swell Alert → Run workflow. Check the log for live buoy readings. A quiet summer day logs "no alert" — that's success.

Texts via carrier gateways (`@tmomail.net`, `@vtext.com`) work but carriers are killing these off (AT&T already did) — email is the reliable channel.

## Tuning

All thresholds sit at the top of `swell_alert.py` — edit in GitHub, commit, next run uses them:

- `FCST_MIN_PERIOD` / `FCST_MIN_HEIGHT_FT` — what earns an OUTLOOK email (12s / 2.5ft)
- `HEADS_UP_PERIOD` / `FIRE_PERIOD` / `FIRE_MIN_HEIGHT_FT` — live buoy tiers (12s / 14s / 3ft)
- `IDEAL_SWELL_MIN/MAX_DEG` — swell direction window (90–180° = E through S)
- `OFFSHORE_WIND_MIN/MAX_DEG`, `MAX_WIND_KT` — wind quality gate
- `COOLDOWN_HOURS` — spam control (12)

## Honest limitations

- The 5–7 day OUTLOOK inherits model uncertainty — hurricane swell projections sharpen a lot inside 3 days. Treat OUTLOOK as "watch this," INCOMING as "plan," LIVE as "go."
- NDBC buoys occasionally go offline; the script skips gracefully and forecast alerts still work.
- Open-Meteo is free without a key; if it's ever down, buoy alerts still run independently.
