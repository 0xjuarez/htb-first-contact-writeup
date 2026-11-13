#!/usr/bin/env python3
"""HTB Space Center - satellite visibility windows (parametrized).

Usage: satvis_param.py "<line1>" "<line2>" <lat_deg> <lon_deg>
Computes rise/set times (elevation > 30 deg) for the next 24h from now.
"""
import math
import sys
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec

LINE1 = sys.argv[1]
LINE2 = sys.argv[2]
STATION_LAT = math.radians(float(sys.argv[3]))
STATION_LON = math.radians(float(sys.argv[4]))
ELEV_MIN = math.radians(30.0)

A = 6378.137
F = 1.0 / 298.257223563
E2 = F * (2 - F)


def gmst_rad(dt: datetime) -> float:
    jd = dt.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return math.radians(g % 360.0)


def station_ecef():
    lat, lon = STATION_LAT, STATION_LON
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    return (
        n * math.cos(lat) * math.cos(lon),
        n * math.cos(lat) * math.sin(lon),
        n * (1 - E2) * math.sin(lat),
    )


def elevation_at(sat, dt, st_ecef, epoch):
    tsince = (dt - epoch).total_seconds() / 60.0
    e, r, _v = sat.sgp4_tsince(tsince)
    if e != 0:
        raise RuntimeError(f"SGP4 error {e} at {dt}")
    g = gmst_rad(dt)
    cg, sg = math.cos(g), math.sin(g)
    x, y, z = r
    xe = cg * x + sg * y
    ye = -sg * x + cg * y
    dx, dy, dz = xe - st_ecef[0], ye - st_ecef[1], z - st_ecef[2]
    lat, lon = STATION_LAT, STATION_LON
    slat, clat, slon, clon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    east = -slon * dx + clon * dy
    north = -slat * clon * dx - slat * slon * dy + clat * dz
    up = clat * clon * dx + clat * slon * dy + slat * dz
    return math.atan2(up, math.hypot(east, north))


def bisect_crossing(sat, t0, t1, st_ecef, epoch):
    f0 = elevation_at(sat, t0, st_ecef, epoch) - ELEV_MIN
    for _ in range(60):
        tm = t0 + (t1 - t0) / 2
        fm = elevation_at(sat, tm, st_ecef, epoch) - ELEV_MIN
        if (f0 > 0) == (fm > 0):
            t0, f0 = tm, fm
        else:
            t1 = tm
        if (t1 - t0).total_seconds() < 0.05:
            break
    return t0 + (t1 - t0) / 2


def main():
    sat = Satrec.twoline2rv(LINE1, LINE2)
    # Epoch from TLE: epoch days field (columns 19-32 of line 1)
    epoch_year = 2000 + int(LINE1[18:20])
    epoch_days = float(LINE1[20:32])
    epoch = datetime(epoch_year, 1, 1, tzinfo=timezone.utc) + timedelta(days=epoch_days - 1)
    print(f"TLE epoch: {epoch.isoformat()}")
    st = station_ecef()
    start = datetime.now(timezone.utc).replace(microsecond=0)
    end = start + timedelta(hours=24)

    step = timedelta(seconds=5)
    t = start
    prev_el = elevation_at(sat, t, st, epoch)
    prev_t = t
    intervals = []
    in_pass = prev_el > ELEV_MIN
    cur_start = t if in_pass else None
    t += step
    while t <= end:
        el = elevation_at(sat, t, st, epoch)
        if el > ELEV_MIN and not in_pass:
            cur_start = bisect_crossing(sat, prev_t, t, st, epoch)
            in_pass = True
        elif el <= ELEV_MIN and in_pass:
            intervals.append((cur_start, bisect_crossing(sat, prev_t, t, st, epoch)))
            in_pass = False
            cur_start = None
        prev_el, prev_t = el, t
        t += step
    if in_pass:
        intervals.append((cur_start, end))
    if intervals and intervals[0][0] <= start:
        print("(omitting current interval, satellite already visible at start)")
        intervals = intervals[1:]

    print(f"Window: {start.isoformat()} -> {end.isoformat()}")
    print(f"Passes > 30 deg: {len(intervals)}")
    out = []
    for rise, set_t in intervals:
        rise_r = (rise + timedelta(seconds=0.5)).replace(microsecond=0)
        set_r = (set_t + timedelta(seconds=0.5)).replace(microsecond=0)
        trunc_r = rise.replace(microsecond=0)
        trunc_s = set_t.replace(microsecond=0)
        max_el = max(
            math.degrees(elevation_at(sat, rise + timedelta(seconds=s), st, epoch))
            for s in range(0, int((set_t - rise).total_seconds()), 10)
        )
        print(f"  rounded:   {rise_r.strftime('%Y-%m-%dT%H:%M:%SZ')} {set_r.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        print(f"  truncated: {trunc_r.strftime('%Y-%m-%dT%H:%M:%SZ')} {trunc_s.strftime('%Y-%m-%dT%H:%M:%SZ')}   (exact {rise.strftime('%H:%M:%S.%f')[:-4]} -> {set_t.strftime('%H:%M:%S.%f')[:-4]}, max elev ~{max_el:.1f} deg)")
        out += [rise_r.strftime("%Y-%m-%dT%H:%M:%SZ"), set_r.strftime("%Y-%m-%dT%H:%M:%SZ")]
    print()
    print("ROUNDED:   " + " ".join(out))
    tout = []
    for rise, set_t in intervals:
        tout += [rise.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 set_t.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")]
    print("TRUNCATED: " + " ".join(tout))


if __name__ == "__main__":
    main()
