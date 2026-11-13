#!/usr/bin/env python3
"""HTB Space Center - autosolver (multi-ronda, con reintentos).

Conecta al servicio y resuelve TODOS los satelites de la sesion en cadena:
lee el TLE y la estacion de cada ronda, calcula los pases con elevacion
> 30 grados para las proximas 24h (SGP4) y envia la respuesta en el acto.
Si una ronda no tiene ningun pase >30 (instancia imposible por azar),
aborta esa cadena y reconecta con una nueva, hasta 5 intentos.
Cuando el servicio entrega la bandera, la muestra y termina.

Uso:  ~/satvenv/bin/python autosolve.py [host] [puerto]
Por defecto: 154.57.164.82 38775
"""
import math
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone

from sgp4.api import Satrec

HOST = sys.argv[1] if len(sys.argv) > 1 else "154.57.164.82"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 38775
MAX_ATTEMPTS = 5

A = 6378.137
F = 1.0 / 298.257223563
E2 = F * (2 - F)
PROMPT = b"When will it be visible next?>" 


def gmst_rad(dt):
    jd = dt.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - t ** 3 / 38710000.0
    return math.radians(g % 360.0)


def elevation_at(sat, dt, st_ecef, epoch, lat, lon, elev_min):
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
    slat, clat, slon, clon = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    east = -slon * dx + clon * dy
    north = -slat * clon * dx - slat * slon * dy + clat * dz
    up = clat * clon * dx + clat * slon * dy + slat * dz
    return math.atan2(up, math.hypot(east, north))


def bisect_crossing(sat, t0, t1, st_ecef, epoch, lat, lon, elev_min):
    f0 = elevation_at(sat, t0, st_ecef, epoch, lat, lon, elev_min) - elev_min
    for _ in range(60):
        tm = t0 + (t1 - t0) / 2
        fm = elevation_at(sat, tm, st_ecef, epoch, lat, lon, elev_min) - elev_min
        if (f0 > 0) == (fm > 0):
            t0, f0 = tm, fm
        else:
            t1 = tm
        if (t1 - t0).total_seconds() < 0.05:
            break
    return t0 + (t1 - t0) / 2


def find_passes(sat, epoch, lat, lon, thr_deg, hours=24):
    """Devuelve (pasos, ventana_actual). La ventana actual se omite de los
    pasos pero se devuelve aparte por si es la unica que existe."""
    elev_min = math.radians(thr_deg)
    n = A / math.sqrt(1 - E2 * math.sin(lat) ** 2)
    st = (
        n * math.cos(lat) * math.cos(lon),
        n * math.cos(lat) * math.sin(lon),
        n * (1 - E2) * math.sin(lat),
    )
    start = datetime.now(timezone.utc).replace(microsecond=0)
    end = start + timedelta(hours=hours)
    step = timedelta(seconds=5)
    t = start
    prev = elevation_at(sat, t, st, epoch, lat, lon, elev_min)
    pt = t
    in_pass = prev > elev_min
    if in_pass:
        # El satelite ya esta visible: retroceder hasta hallar la subida REAL
        # del pase en curso (no usar la hora de inicio como subida).
        tb = start
        elb = prev
        while elb > elev_min and (start - tb) < timedelta(hours=3):
            tb -= step
            elb = elevation_at(sat, tb, st, epoch, lat, lon, elev_min)
        cur = bisect_crossing(sat, tb, tb + step, st, epoch, lat, lon, elev_min)
    else:
        cur = None
    iv = []
    t += step
    while t <= end:
        el = elevation_at(sat, t, st, epoch, lat, lon, elev_min)
        if el > elev_min and not in_pass:
            cur = bisect_crossing(sat, pt, t, st, epoch, lat, lon, elev_min)
            in_pass = True
        elif el <= elev_min and in_pass:
            iv.append((cur, bisect_crossing(sat, pt, t, st, epoch, lat, lon, elev_min)))
            in_pass = False
        prev, pt = el, t
        t += step
    if in_pass:
        iv.append((cur, end))
    current = None
    if iv and iv[0][0] <= start:
        current = iv[0]
        iv = iv[1:]  # omitir la ventana actual (el reto lo permite)
    return iv, current


def fmt(iv):
    out = []
    for r, s in iv:
        out.append((r + timedelta(seconds=0.5)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
        out.append((s + timedelta(seconds=0.5)).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return " ".join(out)


def last_tle(text):
    l1s = [x for x in text.splitlines() if x.startswith("1 ")]
    l2s = [x for x in text.splitlines() if x.startswith("2 ")]
    st = re.findall(r"\(Lat,Long\):\s*([-\d.]+),([-\d.]+)", text)
    if not l1s or not l2s or not st:
        return None
    l1, l2 = l1s[-1], l2s[-1]
    if len(l2) == 70:  # a veces viene un espacio de mas antes del rev number
        i = l2.rfind(" ")
        l2 = l2[:i] + l2[i + 1:]
    lat, lon = st[-1]
    return l1, l2, float(lat), float(lon)


def read_until_prompt(s, buf, timeout=20):
    """Lee hasta que el prompt aparezca en el buffer. Devuelve (buf, ok)."""
    s.settimeout(timeout)
    try:
        while PROMPT not in buf:
            chunk = s.recv(4096)
            if not chunk:
                return buf, False
            buf += chunk
    except socket.timeout:
        return buf, False
    return buf, True


def solve_chain():
    """Resuelve una cadena completa. Devuelve la bandera o None."""
    print(f"Conectando a {HOST}:{PORT} ...")
    s = socket.create_connection((HOST, PORT), timeout=15)
    buf = b""
    ronda = 0
    try:
        while True:
            buf, ok = read_until_prompt(s, buf)
            text = buf.decode("utf-8", "replace")
            if not ok:
                print("---- Datos recibidos ----")
                print(text)
                print("No llego el prompt o se cerro la conexion.")
                return None
            parsed = last_tle(text)
            if not parsed:
                print("No pude leer el TLE o la estacion.")
                print(text)
                return None
            l1, l2, lat_deg, lon_deg = parsed
            ronda += 1
            lat, lon = math.radians(lat_deg), math.radians(lon_deg)
            sat = Satrec.twoline2rv(l1, l2)
            epoch = datetime(2000 + int(l1[18:20]), 1, 1, tzinfo=timezone.utc) + timedelta(days=float(l1[20:32]) - 1)
            iv, current = find_passes(sat, epoch, lat, lon, 30.0)
            if iv:
                answer = fmt(iv)
                print(f"[Ronda {ronda}] Pases >30 deg: {len(iv)} -> enviando: {answer}")
            elif current:
                # Sin pases futuros, pero si la ventana actual: es la unica
                # respuesta >30 posible; una respuesta vacia casi seguro falla.
                answer = fmt([current])
                print(f"[Ronda {ronda}] Sin pases futuros >30; envio la ventana actual: {answer}")
            else:
                print(f"[Ronda {ronda}] Sin NINGUN pase >30 en 24h: instancia imposible. Aborto la cadena.")
                return None
            buf = b""
            s.sendall((answer + "\n").encode())
            s.settimeout(10)
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        print("El servidor cerro la conexion.")
                        # NO borrar buf: la respuesta (y la posible bandera)
                        # ya puede estar ahi; borrarla la perderia.
                        break
                    buf += chunk
                    if PROMPT in buf:
                        break
            except socket.timeout:
                pass
            resp = buf.decode("utf-8", "replace")
            print("---- Respuesta del servidor ----")
            print(resp)
            m = re.search(r"HTB\{[^}]*\}", resp)
            if m:
                return m.group(0)
            if "Incorrect" in resp:
                print("Respuesta incorrecta. Fin de la cadena.")
                return None
            if PROMPT not in buf:
                print("No hay mas rondas. Fin de la cadena.")
                return None
            # Si hay prompt, el loop sigue con el siguiente satelite (ya en buf)
    finally:
        s.close()


def main():
    for attempt in range(1, MAX_ATTEMPTS + 1):
        print(f"\n===== Intento {attempt}/{MAX_ATTEMPTS} =====")
        flag = solve_chain()
        if flag:
            print(f"\n*** BANDERA: {flag} ***")
            return
        if attempt < MAX_ATTEMPTS:
            print("Reintentando con una cadena nueva en 3 segundos...")
            time.sleep(3)
    print("\nSe acabaron los intentos sin bandera.")


if __name__ == "__main__":
    main()
