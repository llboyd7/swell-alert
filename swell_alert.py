#!/usr/bin/env python3
"""
Swell Alert v2 — Wrightsville Beach, NC
Three-tier warning system with multi-day projection, direction tracking,
observation logging, and a surf-quality feedback loop.

TIERS (most to least lead time):
  🔭 OUTLOOK   3–7 days out  — Open-Meteo marine model shows a swell day coming
  📡 INCOMING  24–48h out    — projected swell is now inside two days
  🌊/🔥 LIVE   0–18h         — NDBC buoys confirm long-period energy in the water

Every run also appends a row to observations.csv (swell + wind history),
and every alert email links to a pre-filled GitHub issue for rating the
session afterward — ratings land in sessions.csv for calibration.
"""

import csv
import json
import os
import re
import smtplib
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

# ─────────────────────────── CONFIG ────────────────────────────
# Wrightsville Beach nearshore point for the forecast model
LAT, LON = 34.20, -77.79

OFFSHORE_BUOY = "41013"   # Frying Pan Shoals
NEARSHORE_BUOY = "41110"  # Masonboro Inlet
WIND_STATION = "JMPN7"    # Johnnie Mercer's Pier

# Live "GO" alert — the ONE email type. Fires when the buoy shows genuinely
# rideable energy, judged on estimated BEACH-FACE height (long-period swell
# shoals up ~2x offshore), not the raw offshore number. A 1.6ft @ 14s pulse
# breaks chest-high (buds scored Masonboro 9/23 off exactly that).
LIVE_MIN_PERIOD = 9.0       # organized/groundswell period floor to bother
LIVE_MIN_FACE_FT = 2.5      # estimated beach face (waist+) to send a GO
LIVE_FIRE_FACE_FT = 5.0     # head-high+ face = 🔥 emphasis (else 🏄)
LIVE_MAX_WIND_KT = 18.0     # strong CROSS-shore above this = too bumpy (offshore is fine)
LIVE_GALE_KT = 28.0         # even offshore, above this you can't paddle out
#   ^ 9/24 was really good at 4ft@11s in 20kt N OFFSHORE — strong offshore grooms the
#     wave (hollow/clean), it does NOT junk it. Only onshore / strong cross / gale kills it.

# Forecast alert thresholds — "worth a heads-up" for a beach break.
# A day earns an OUTLOOK if EITHER condition holds (Sensitive preset):
FCST_MIN_TOTAL_FT = 2.5          # total surf height is fun-size on its own, OR
FCST_SWELL_MIN_HEIGHT_FT = 1.5   # a cleaner swell at least this tall...
FCST_SWELL_MIN_PERIOD = 6.5      # ...at this period or longer
# ^ period floor lowered 7.0 -> 6.5 after the 2026-09-01 session: an *ideal*
#   longboard morning (ESE ~2ft @ 7s, light N offshore) forecast at 6.95s was
#   missed by the old 7.0s floor. See sessions.csv.

# Wind + swell angle are gates, not garnish. A day with surfable size still
# does NOT earn an OUTLOOK if the swell is off-angle or it's blown out onshore.
FCST_MAX_ONSHORE_KT = 12.0       # 8am onshore wind stronger than this = junk, skip

# Letter grade A/B/C/D, fit to real logged sessions (sessions.csv). Signals:
# period (king), size, cleanliness (swell/total), dawn wind, and swell angle.
GRADE_A_PERIOD = 10.0            # period >= this (real groundswell) = Grade A juice
GRADE_A_HEIGHT_FT = 3.0         # ...or swell >= this = Grade A on size alone
GRADE_B_HEIGHT_FT = 2.0         # swell >= this (clean, in period) = B base; below = C
FCST_CLEAN_RATIO_MIN = 0.55     # swell/total below this = wind chop on top = auto D
# Big + short-period = closeouts ("can't find a shoulder"). At short period, MORE
# size is worse, not better (9/01 2ft@7s = fun B; 9/25 4.3ft@7.7s = closeout dud).
CLOSEOUT_PERIOD = 9.0           # below this period...
CLOSEOUT_MIN_HEIGHT_FT = 3.0    # ...swell this big or more closes out -> D
# Core swell window: the beach faces ~ESE, so ESE–SE is the organized sweet spot.
# Inside the working window (E–S) but outside this core softens one grade —
# straight-E 9/18 rated C vs ESE 9/01 rated B on otherwise-equal conditions.
CORE_SWELL_MIN_DEG = 100        # ESE
CORE_SWELL_MAX_DEG = 160        # SSE
GRADE_LABELS = {                 # grade -> (emoji, phrase)
    "A": ("🏆", "Grade A — juicy, all boards"),
    "B": ("⭐", "Grade B — clean & fun"),
    "C": ("〰️", "Grade C — small but rideable"),
    "D": ("🚫", "Grade D — junky, skip it"),
}

# Wind at Wrightsville (beach faces ~ESE). Offshore/clean wind blows off the
# land — the WSW→W→NW→N→NE arc. Onshore (junky) blows in off the ocean —
# ENE→E→SE→S. Anything in the gaps (NE–ENE, S–SW) is cross-shore.
OFFSHORE_WIND_MIN_DEG = 245   # WSW
OFFSHORE_WIND_MAX_DEG = 45    # NE
ONSHORE_WIND_MIN_DEG = 60     # ENE
ONSHORE_WIND_MAX_DEG = 190    # S
MAX_WIND_KT = 15.0

# Best swell window for Wrightsville (degrees the swell comes FROM)
IDEAL_SWELL_MIN_DEG = 90    # E
IDEAL_SWELL_MAX_DEG = 180   # S

# Spots share the buoys but differ in WIND exposure. Masonboro has a north jetty
# that shelters northerly wind (NW→N→ENE), so a strong N blow that junks the open
# beach can leave Mase clean (9/24 was pumping in 20kt N at Mase). Out Front
# (August St) is an open beach and feels the wind.
SPOTS = {
    "outfront":  {"name": "Out Front (August St)", "jetty_block": None},
    "masonboro": {"name": "Masonboro",             "jetty_block": (300, 67)},  # NW→N→ENE blocked
}
DEFAULT_SPOT = "outfront"

COOLDOWN_HOURS = 12

BASE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE, "state.json")
OBS_FILE = os.path.join(BASE, "observations.csv")

NDBC_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.{ext}"
MARINE_URL = (
    "https://marine-api.open-meteo.com/v1/marine"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=swell_wave_height,swell_wave_period,swell_wave_direction,wave_height"
    "&timezone=America%2FNew_York&forecast_days=7"
    # NOAA GFS-Wave (US operational). The default best_match uses a coarse global
    # model (gwam) that flattens period to ~5s here and chronically under-forecast
    # this spot (missed 9/08's 15s day entirely). GFS-Wave resolves real period.
    "&models=ncep_gfswave025"
)
WIND_FCST_URL = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={LAT}&longitude={LON}"
    "&hourly=wind_speed_10m,wind_direction_10m"
    "&wind_speed_unit=kn&timezone=America%2FNew_York&forecast_days=7"
)

M_TO_FT = 3.28084
MPS_TO_KT = 1.94384
COMPASS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]
# ───────────────────────────────────────────────────────────────


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "swell-alert/2.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def deg_to_compass(deg):
    if deg is None:
        return "?"
    return COMPASS[int((deg + 11.25) % 360 // 22.5)]


def parse_float(val: str):
    if val in ("MM", "99.0", "99.00", "999", "999.0", "9999.0"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


def in_window(deg, lo, hi) -> bool:
    """True if compass bearing deg falls in window lo→hi (handles wraparound)."""
    if deg is None:
        return False
    if lo <= hi:
        return lo <= deg <= hi
    return deg >= lo or deg <= hi


def wind_label(deg, kt) -> str:
    """Surfer-style wind read: direction class + strength (beach faces ~ESE)."""
    if deg is None or kt is None:
        return "wind n/a"
    if in_window(deg, OFFSHORE_WIND_MIN_DEG, OFFSHORE_WIND_MAX_DEG):
        cls = "offshore"
    elif in_window(deg, ONSHORE_WIND_MIN_DEG, ONSHORE_WIND_MAX_DEG):
        cls = "onshore"
    else:
        cls = "cross-shore"
    strong = kt > MAX_WIND_KT
    if cls == "offshore":
        return "offshore but strong" if strong else "offshore 🙌"
    return f"{cls}/strong" if strong else cls


# ───────────────────── LIVE BUOY READS ─────────────────────────

def ndbc_lines(station, ext):
    text = http_get(NDBC_URL.format(station=station, ext=ext))
    return [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def latest_spec(station):
    """.spec: YY MM DD hh mm WVHT SwH SwP WWH WWP SwD WWD STEEPNESS APD MWD"""
    lines = ndbc_lines(station, "spec")
    if not lines:
        return {}
    f = lines[0].split()
    swh, wvht = parse_float(f[6]), parse_float(f[5])
    mwd = parse_float(f[14]) if len(f) > 14 else None
    return {
        "time_utc": f"{f[0]}-{f[1]}-{f[2]} {f[3]}:{f[4]}Z",
        "wvht_ft": round(wvht * M_TO_FT, 1) if wvht is not None else None,
        "swell_ht_ft": round(swh * M_TO_FT, 1) if swh is not None else None,
        "swell_period_s": parse_float(f[7]),
        "swell_dir": f[10] if len(f) > 10 else "?",     # compass, e.g. "SE"
        "mean_wave_dir_deg": mwd,
        "steepness": f[12] if len(f) > 12 else None,    # SWELL / AVERAGE / STEEP / VERY_STEEP
    }


def latest_met(station):
    """.txt: YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD ..."""
    lines = ndbc_lines(station, "txt")
    if not lines:
        return {}
    f = lines[0].split()
    wspd, wvht = parse_float(f[6]), parse_float(f[8])
    return {
        "time_utc": f"{f[0]}-{f[1]}-{f[2]} {f[3]}:{f[4]}Z",
        "wind_dir_deg": parse_float(f[5]),
        "wind_kt": round(wspd * MPS_TO_KT, 1) if wspd is not None else None,
        "wvht_ft": round(wvht * M_TO_FT, 1) if wvht is not None else None,
        "dpd_s": parse_float(f[9]),
    }


# ──────────────────── FORECAST PROJECTION ──────────────────────

def fetch_forecast():
    """
    Return list of daily summaries for the next 7 days:
    [{date, max_swell_ft, period_at_max, dir_at_max_deg, best_wind_kt, best_wind_deg}]
    Wind is sampled at 8am local (dawn-patrol-ish) each day.
    """
    marine = json.loads(http_get(MARINE_URL))["hourly"]
    windf = json.loads(http_get(WIND_FCST_URL))["hourly"]
    days = {}
    for i, ts in enumerate(marine["time"]):
        date = ts[:10]
        d = days.setdefault(date, {"date": date, "max_swell_ft": 0, "max_total_ft": 0,
                                   "period_at_max": None, "dir_at_max_deg": None})
        tot_m = marine["wave_height"][i]
        if tot_m is not None:
            tot_ft = tot_m * M_TO_FT
            if tot_ft > d["max_total_ft"]:
                d["max_total_ft"] = round(tot_ft, 1)
        ht_m = marine["swell_wave_height"][i]
        if ht_m is None:
            continue
        ht_ft = ht_m * M_TO_FT
        if ht_ft > d["max_swell_ft"]:
            d["max_swell_ft"] = round(ht_ft, 1)
            d["period_at_max"] = marine["swell_wave_period"][i]
            d["dir_at_max_deg"] = marine["swell_wave_direction"][i]
    for i, ts in enumerate(windf["time"]):
        if ts[11:13] == "08":  # 8am local
            date = ts[:10]
            if date in days:
                days[date]["wind_8am_kt"] = windf["wind_speed_10m"][i]
                days[date]["wind_8am_deg"] = windf["wind_direction_10m"][i]
    return sorted(days.values(), key=lambda d: d["date"])


def wind_effect(spot, wdeg, wkt):
    """How wind treats a given SPOT: 'clean' (no penalty), 'soften' (one grade),
    or 'blown' (skip). Masonboro's north jetty shelters northerly wind; the open
    beach feels strong offshore as bumpy."""
    if wdeg is None:
        return "clean"                                # unknown -> don't penalize
    wk = wkt or 0
    jb = SPOTS.get(spot, {}).get("jetty_block")
    if jb and in_window(wdeg, jb[0], jb[1]):
        return "clean"                                # sheltered by the jetty, any strength
    if in_window(wdeg, ONSHORE_WIND_MIN_DEG, ONSHORE_WIND_MAX_DEG):
        return "blown" if wk > FCST_MAX_ONSHORE_KT else "soften"      # onshore
    if in_window(wdeg, OFFSHORE_WIND_MIN_DEG, OFFSHORE_WIND_MAX_DEG):
        if wk > LIVE_GALE_KT:
            return "blown"                            # offshore gale, can't paddle out
        return "soften" if wk > LIVE_MAX_WIND_KT else "clean"  # strong offshore (open) = bumpy
    return "blown" if wk > LIVE_MAX_WIND_KT else "soften"      # cross-shore


def forecast_day_grade(d, spot=DEFAULT_SPOT):
    """Letter grade A/B/C/D for a day at a SPOT, or None to skip. Base grade from
    period/size, then softened for chop, off-core angle, or the spot's wind."""
    total = d.get("max_total_ft") or 0
    swell = d.get("max_swell_ft") or 0
    period = d.get("period_at_max") or 0
    dir_deg = d.get("dir_at_max_deg")
    wkt, wdeg = d.get("wind_8am_kt"), d.get("wind_8am_deg")

    # ---- hard skips: not even a grade ----
    if not in_window(dir_deg, IDEAL_SWELL_MIN_DEG, IDEAL_SWELL_MAX_DEG):
        return None                                   # off-angle
    if period < FCST_SWELL_MIN_PERIOD:
        return None                                   # short-period wind slop
    we = wind_effect(spot, wdeg, wkt)
    if we == "blown":
        return None                                   # blown out for this spot
    if swell < FCST_SWELL_MIN_HEIGHT_FT and total < FCST_MIN_TOTAL_FT:
        return None                                   # nothing there

    # ---- chop-dominated, tiny, or closeout = automatic D ----
    ratio = (swell / total) if total else 1.0
    if ratio < FCST_CLEAN_RATIO_MIN or swell < FCST_SWELL_MIN_HEIGHT_FT:
        return "D"
    if period < CLOSEOUT_PERIOD and swell >= CLOSEOUT_MIN_HEIGHT_FT:
        return "D"                                    # big + short period = closeouts

    # ---- base grade from juice (period / size) ----
    if period >= GRADE_A_PERIOD or swell >= GRADE_A_HEIGHT_FT:
        base = "A"
    elif swell >= GRADE_B_HEIGHT_FT:
        base = "B"
    else:
        base = "C"

    # ---- soften one notch per factor working against you ----
    softer = {"A": "B", "B": "C", "C": "D", "D": "D"}
    if we == "soften":
        base = softer[base]                           # spot's wind (cross / light onshore / strong open offshore)
    if not in_window(dir_deg, CORE_SWELL_MIN_DEG, CORE_SWELL_MAX_DEG):
        base = softer[base]                           # off-core swell angle (edge of window)
    return base


def forecast_day_qualifies(d, spot=DEFAULT_SPOT) -> bool:
    """Worth an OUTLOOK email — only genuinely good days (A or B). C/D still show
    on the dashboard, but don't fire a notification."""
    return forecast_day_grade(d, spot) in ("A", "B")


def find_swell_days(forecast):
    """Days worth an OUTLOOK email (A/B) under the current thresholds."""
    return [d for d in forecast if forecast_day_qualifies(d)]


def describe_day(d) -> str:
    swell_dir = deg_to_compass(d.get("dir_at_max_deg"))
    angle_txt = "✅ ideal window" if in_window(d.get("dir_at_max_deg"), IDEAL_SWELL_MIN_DEG, IDEAL_SWELL_MAX_DEG) else "⚠️ off-angle"
    wind = ""
    if d.get("wind_8am_kt") is not None:
        wdir = deg_to_compass(d.get("wind_8am_deg"))
        tag = wind_label(d.get("wind_8am_deg"), d.get("wind_8am_kt"))
        wind = f" | 8am wind {d['wind_8am_kt']:.0f}kt {wdir} ({tag})"
    period = d.get("period_at_max")
    pstr = f"{period:.0f}s" if period else "short-period"
    emoji, gtxt = GRADE_LABELS[forecast_day_grade(d)]
    return (f"{d['date']}: {emoji} {gtxt} — {d.get('max_total_ft', 0)}ft surf "
            f"(swell {d['max_swell_ft']}ft @ {pstr} from {swell_dir}) ({angle_txt}){wind}")


# ─────────────────── STATE / LOG / EMAIL ───────────────────────

def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)


def hours_since(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except (ValueError, TypeError):
        return 1e9


def log_observation(offshore, nearshore, wind, tier):
    """Append current readings to observations.csv for history/calibration."""
    new = not os.path.exists(OBS_FILE)
    with open(OBS_FILE, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["utc", "swell_ht_ft", "swell_period_s", "swell_dir",
                        "offshore_wvht_ft", "nearshore_wvht_ft", "nearshore_dpd_s",
                        "wind_kt", "wind_dir_deg", "tier"])
        w.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            offshore.get("swell_ht_ft"), offshore.get("swell_period_s"),
            offshore.get("swell_dir"), offshore.get("wvht_ft"),
            nearshore.get("wvht_ft"), nearshore.get("dpd_s"),
            wind.get("wind_kt"), wind.get("wind_dir_deg"), tier or "",
        ])


def feedback_link() -> str:
    """Pre-filled GitHub issue link for rating the session."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        return ""
    today = datetime.now().strftime("%Y-%m-%d")
    return (f"\nAfter you surf, rate it (builds the calibration log):\n"
            f"https://github.com/{repo}/issues/new"
            f"?title=Session+{today}&body=Rating+(1-10):%20%0ANotes:%20\n")


def send_email(subject, body):
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipients = [r.strip() for r in os.environ["RECIPIENTS"].split(",") if r.strip()]
    if not recipients:
        return
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, sender, ", ".join(recipients)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(sender, password)
        s.sendmail(sender, recipients, msg.as_string())
    print(f"Sent: {subject}")


# ─────────────────────────── MAIN ──────────────────────────────

def check_forecast(state) -> dict:
    """OUTLOOK / INCOMING alerts from the 7-day model. One email per swell event."""
    try:
        forecast = fetch_forecast()
    except Exception as e:
        print(f"Forecast fetch failed (non-fatal): {e}")
        return state

    swell_days = find_swell_days(forecast)
    if not swell_days:
        print("Forecast: no projected swell days in next 7.")
        state["projected_dates"] = []
        return state

    dates = [d["date"] for d in swell_days]
    already = set(state.get("projected_dates", []))
    new_dates = [d for d in dates if d not in already]

    today = datetime.now().date()
    first = datetime.strptime(dates[0], "%Y-%m-%d").date()
    days_out = (first - today).days
    lines = "\n".join("  " + describe_day(d) for d in swell_days)

    if new_dates:
        _g0 = forecast_day_grade(swell_days[0])
        _e0, _ = GRADE_LABELS[_g0]
        subject = f"🔭 OUTLOOK {_e0} Grade {_g0}: {swell_days[0]['max_total_ft']}ft surf projected {dates[0]} ({days_out} days out)"
        body = (f"7-day model projection — Wrightsville Beach\n\nProjected swell days:\n{lines}\n\n"
                f"Model: Open-Meteo marine (WaveWatch-class). Expect refinement as it gets closer;\n"
                f"buoy confirmation alerts will follow when energy actually shows at Frying Pan Shoals.\n")
        send_email(subject, body)
        state["projected_dates"] = dates
        state["incoming_sent_for"] = state.get("incoming_sent_for", "")
    elif 0 <= days_out <= 2 and state.get("incoming_sent_for") != dates[0]:
        subject = f"📡 INCOMING: projected swell now {days_out} day(s) out — plan your window"
        body = (f"Updated projection — Wrightsville Beach\n\n{lines}\n\n"
                f"Buoy confirmation alerts will fire when it arrives.\n")
        send_email(subject, body)
        state["incoming_sent_for"] = dates[0]
    else:
        print(f"Forecast: swell days {dates} already announced.")
    return state


def shoal_factor(period):
    """Long-period swell stands up more as it shoals onto the sandbars.
    Rough surfer heuristic mapping deep-water period -> face multiplier."""
    if period is None:
        return 1.0
    if period < 8:
        return 1.0
    if period < 11:
        return 1.4
    if period < 14:
        return 1.9
    return 2.2


def face_height_ft(offshore_swell_ft, period):
    if offshore_swell_ft is None:
        return 0.0
    return offshore_swell_ft * shoal_factor(period)


def size_word(face):
    if face < 1.5:
        return "ankle-knee"
    if face < 2.5:
        return "knee-thigh"
    if face < 3.5:
        return "waist-to-chest"
    if face < 4.5:
        return "chest-to-shoulder"
    if face < 5.5:
        return "head-high"
    if face < 7.5:
        return "overhead"
    return "well overhead"


def check_buoys(state) -> dict:
    """The ONE email: a LIVE 'GO' alert when the buoy shows genuinely rideable
    energy, described in beach-face terms (not the small offshore number)."""
    offshore = latest_spec(OFFSHORE_BUOY)
    nearshore = latest_met(NEARSHORE_BUOY)
    wind = latest_met(WIND_STATION)

    period = offshore.get("swell_period_s")
    height = offshore.get("swell_ht_ft")
    face = round(face_height_ft(height, period), 1) if height is not None else 0.0
    print(f"41013: {height}ft @ {period}s {offshore.get('swell_dir')} (~{face}ft face) | "
          f"41110: {nearshore.get('wvht_ft')}ft DPD {nearshore.get('dpd_s')}s | "
          f"wind {wind.get('wind_kt')}kt {deg_to_compass(wind.get('wind_dir_deg'))}")

    # GO per SPOT — wind treats each differently (Masonboro's jetty shelters N).
    wdeg, wkt = wind.get("wind_dir_deg"), wind.get("wind_kt")
    # Clean-water gate: swell must be a real fraction of total seas (not wind chop),
    # AND the buoy's own steepness flag must not be VERY_STEEP (a steep, choppy,
    # "drifty" sea — 10/06 was VERY_STEEP and came in rough/drifty, as the buddy's
    # period-coherence theory predicts).
    seas = offshore.get("wvht_ft")
    steep = offshore.get("steepness")
    clean_water = (((not seas) or (not height) or (height / seas) >= FCST_CLEAN_RATIO_MIN)
                   and steep != "VERY_STEEP")
    rideable = (period is not None and period >= LIVE_MIN_PERIOD
                and face >= LIVE_MIN_FACE_FT and clean_water)
    verdicts = {}   # spot -> "clean" | "bumpy" | None
    for key in SPOTS:
        if not rideable:
            verdicts[key] = None
        else:
            we = wind_effect(key, wdeg, wkt)
            verdicts[key] = None if we == "blown" else ("clean" if we == "clean" else "bumpy")
    any_go = any(verdicts.values())
    tier = "GO" if any_go else None

    log_observation(offshore, nearshore, wind, tier)

    if not any_go:
        state.pop("live_tier", None)
        state.pop("live_time", None)
        return state

    # One swell shouldn't email repeatedly.
    if state.get("live_tier") == "GO" and hours_since(state.get("live_time", "")) < COOLDOWN_HOURS:
        print("Live alert cooldown active.")
        return state

    words = size_word(face)
    wind_txt = wind_label(wdeg, wkt)
    swell_dir = offshore.get("swell_dir", "?")
    flame = "🔥" if face >= LIVE_FIRE_FACE_FT else "🏄"
    clean_spots = [SPOTS[k]["name"] for k, v in verdicts.items() if v == "clean"]
    lead = clean_spots[0] if clean_spots else next(SPOTS[k]["name"] for k, v in verdicts.items() if v)
    subject = f"{flame} IT'S ON at {lead}: ~{words} ({period:.0f}s {swell_dir})"

    spot_lines = []
    for key in SPOTS:
        v, nm = verdicts[key], SPOTS[key]["name"]
        if v == "clean":
            spot_lines.append(f"  ✅ {nm}: GO — clean")
        elif v == "bumpy":
            spot_lines.append(f"  🌬️ {nm}: rideable but brisk/bumpy ({wind_txt})")
        else:
            spot_lines.append(f"  ⛔ {nm}: not today (wind or too small)")

    body = (f"Buoy says GO — Wrightsville\n\n"
            f"Estimated surf: ~{words}  (about {face:.0f}ft faces)\n"
            f"  {period:.0f}s {swell_dir} groundswell — long-period stands up bigger on the sandbars.\n"
            f"  Wind: {wkt} kt {deg_to_compass(wdeg)} — {wind_txt}\n\n"
            f"By spot (Masonboro's north jetty shelters N/NE wind; Out Front is open):\n"
            + "\n".join(spot_lines) + "\n\n"
            f"Raw buoy readings:\n"
            f"  Offshore (Frying Pan 41013): {height} ft @ {period}s from {swell_dir}  ({offshore.get('time_utc')})\n"
            f"  Nearshore (Masonboro 41110): {nearshore.get('wvht_ft')} ft, DPD {nearshore.get('dpd_s')}s\n\n"
            f"Live dashboard: https://llboyd7.github.io/swell-alert/\n"
            f"{feedback_link()}")
    send_email(subject, body)
    state["live_tier"] = "GO"
    state["live_time"] = datetime.now(timezone.utc).isoformat()
    return state


def main():
    state = load_state()
    # OUTLOOK/INCOMING forecasts now live on the always-current dashboard, not
    # email. Email is reserved for the single LIVE "GO" alert so it means one
    # thing: paddle out now. (check_forecast is kept but no longer emails.)
    state = check_buoys(state)
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
