#!/usr/bin/env python3
"""
Writes docs/data.json — a snapshot of current buoy conditions + the 7-day
forecast for the GitHub Pages dashboard (docs/index.html reads it).

Reuses swell_alert's already-verified fetch/parse functions, so there is no
duplicate parsing logic to keep in sync. Run hourly by the workflow, right
after swell_alert.py.
"""

import json
import os
from datetime import datetime, timezone

import swell_alert as s

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
OUT = os.path.join(DOCS, "data.json")


def main():
    os.makedirs(DOCS, exist_ok=True)

    offshore = s.latest_spec(s.OFFSHORE_BUOY)     # 41013 Frying Pan Shoals
    nearshore = s.latest_met(s.NEARSHORE_BUOY)    # 41110 Masonboro Inlet
    wind = s.latest_met(s.WIND_STATION)           # JMPN7 Johnnie Mercer's Pier

    # Current live tier — same logic as check_buoys()
    period = offshore.get("swell_period_s")
    height = offshore.get("swell_ht_ft")
    tier = None
    if period is not None:
        if period >= s.FIRE_PERIOD and (height or 0) >= s.FIRE_MIN_HEIGHT_FT:
            tier = "FIRE"
        elif period >= s.HEADS_UP_PERIOD:
            tier = "HEADS_UP"

    ideal_wind = (
        s.in_window(wind.get("wind_dir_deg"), s.OFFSHORE_WIND_MIN_DEG, s.OFFSHORE_WIND_MAX_DEG)
        and (wind.get("wind_kt") or 99) <= s.MAX_WIND_KT
    )

    forecast = []
    try:
        for d in s.fetch_forecast():
            period_at_max = d.get("period_at_max")
            forecast.append({
                "date": d["date"],
                "total_ft": d.get("max_total_ft"),
                "max_swell_ft": d["max_swell_ft"],
                "period_s": period_at_max,
                "dir_deg": d.get("dir_at_max_deg"),
                "dir_compass": s.deg_to_compass(d.get("dir_at_max_deg")),
                "ideal_dir": s.in_window(d.get("dir_at_max_deg"),
                                         s.IDEAL_SWELL_MIN_DEG, s.IDEAL_SWELL_MAX_DEG),
                "wind_8am_kt": d.get("wind_8am_kt"),
                "wind_8am_deg": d.get("wind_8am_deg"),
                "wind_8am_compass": s.deg_to_compass(d.get("wind_8am_deg")),
                "wind_8am_offshore": s.in_window(d.get("wind_8am_deg"),
                                                 s.OFFSHORE_WIND_MIN_DEG, s.OFFSHORE_WIND_MAX_DEG)
                                     and (d.get("wind_8am_kt") or 99) <= s.MAX_WIND_KT,
                "is_swell_day": s.forecast_day_qualifies(d),
                "quality": s.forecast_day_quality(d) if s.forecast_day_qualifies(d) else None,
            })
    except Exception as e:
        print(f"Forecast fetch failed (dashboard will show buoy only): {e}")

    data = {
        "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "tier": tier,
        "wind_ideal": ideal_wind,
        "offshore": {
            "station": s.OFFSHORE_BUOY, "name": "Frying Pan Shoals",
            "swell_ht_ft": offshore.get("swell_ht_ft"),
            "swell_period_s": offshore.get("swell_period_s"),
            "swell_dir": offshore.get("swell_dir"),
            "seas_ft": offshore.get("wvht_ft"),
            "time_utc": offshore.get("time_utc"),
        },
        "nearshore": {
            "station": s.NEARSHORE_BUOY, "name": "Masonboro Inlet",
            "wvht_ft": nearshore.get("wvht_ft"),
            "dpd_s": nearshore.get("dpd_s"),
            "time_utc": nearshore.get("time_utc"),
        },
        "wind": {
            "station": s.WIND_STATION, "name": "Johnnie Mercer's Pier",
            "kt": wind.get("wind_kt"),
            "dir_compass": s.deg_to_compass(wind.get("wind_dir_deg")),
            "dir_deg": wind.get("wind_dir_deg"),
            "time_utc": wind.get("time_utc"),
        },
        "forecast": forecast,
        "thresholds": {
            "fire_period": s.FIRE_PERIOD, "fire_min_height_ft": s.FIRE_MIN_HEIGHT_FT,
            "heads_up_period": s.HEADS_UP_PERIOD,
            "fcst_min_total_ft": s.FCST_MIN_TOTAL_FT,
            "fcst_swell_min_height_ft": s.FCST_SWELL_MIN_HEIGHT_FT,
            "fcst_swell_min_period": s.FCST_SWELL_MIN_PERIOD,
        },
    }

    with open(OUT, "w") as fh:
        json.dump(data, fh, indent=2)
    print(f"Wrote {OUT} — tier={tier}, forecast_days={len(forecast)}")


if __name__ == "__main__":
    main()
