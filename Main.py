

```python
import argparse
import json
import math
from datetime import datetime, timezone

EARTH_RADIUS_KM = 6371.0
C_SPEED = 3.0e8
G_GRAVITY = 9.81
DEFAULT_GRID_KM = 108.1
DEFAULT_NODE_COUNT = 370


def normalize_angle(angle):
    while angle < 0:
        angle += 360
    while angle >= 360:
        angle -= 360
    return angle


def angular_distance(a, b):
    diff = abs(normalize_angle(a) - normalize_angle(b))
    return min(diff, 360 - diff)


def haversine_km(lat1_deg, lon1_deg, lat2_deg, lon2_deg):
    lat1 = math.radians(lat1_deg)
    lon1 = math.radians(lon1_deg)
    lat2 = math.radians(lat2_deg)
    lon2 = math.radians(lon2_deg)

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def geoid_proxy(lat_deg, lon_deg):
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    return 3.0 * math.sin(lat_rad) + 1.5 * math.cos(lon_rad)


def generate_nodes(node_count):
    side = int(math.ceil(math.sqrt(node_count)))
    nodes = []
    for i in range(node_count):
        row = i // side
        col = i % side
        lat = -90.0 + (180.0 / side) * (row + 0.5)
        lon = -180.0 + (360.0 / side) * (col + 0.5)
        nodes.append({"lat": lat, "lon": lon})
    return nodes


def jitter_for_node(lat_deg, lon_deg, grid_km, zero_lat, zero_lon):
    dist_km = haversine_km(lat_deg, lon_deg, zero_lat, zero_lon)
    return (dist_km / grid_km) - math.floor(dist_km / grid_km)


def compute_timeslip(lat_deg, lon_deg):
    geoid = geoid_proxy(lat_deg, lon_deg)
    return (geoid * G_GRAVITY * 1e9) / (C_SPEED ** 2)


def compute_subsolar_lon(seconds_since_epoch):
    hours = seconds_since_epoch / 3600.0
    return normalize_angle(360.0 - (15.0 * hours))


def is_day_side(node_lon, subsolar_lon):
    return angular_distance(node_lon, subsolar_lon) <= 90.0


def classify_jitter(j):
    if abs(j) < 1e-9:
        return "earthquake_integer_lock"
    if 0.10 <= j <= 0.20:
        return "fire_arc_arcing_gap"
    if 0.23 <= j <= 0.27:
        return "tornado_hurricane_stress_knee"
    if 0.07 <= j <= 0.09:
        return "derecho_tilt_lock"
    return "neutral"


def simulate(inputs):
    nodes = generate_nodes(inputs["node_count"])
    zero_lat = inputs["giza_zero_lat"]
    zero_lon = inputs["giza_zero_lon"]

    t_arrival = 1.5e6 / max(inputs["solar_wind_speed_km_s"], 1.0)
    t_response = 1.0e7 / max(inputs["solar_wind_speed_km_s"], 1.0)
    now_ts = datetime.now(timezone.utc).timestamp()
    t_event_epoch = now_ts + t_arrival + t_response
    subsolar_lon = compute_subsolar_lon(t_arrival)

    hits = []
    echo_hits = []

    for i, node in enumerate(nodes):
        lat = node["lat"]
        lon = node["lon"]

        j = jitter_for_node(lat, lon, inputs["grid_km"], zero_lat, zero_lon)
        timeslip = compute_timeslip(lat, lon)
        v_p_i = inputs["x_ray_flux_w_m2"] * 1e9 * j
        f_h_i = (inputs["solar_wind_speed_km_s"] * inputs["proton_density_cm3"]) / inputs["grid_km"]
        s_soak_i = (inputs["solar_wind_speed_km_s"] / 440.0) * timeslip
        f_v = (inputs["m_flare_count_24h"] / 24.0) / 0.0145
        f_theta_i = 6.6 * (1.0 + timeslip)

        event_type = classify_jitter(j)

        fire_arc = (0.10 <= j <= 0.20) and (v_p_i > 1.0e6)
        earthquake = (abs(j) < 1e-9) and (f_h_i > 1.0e3)
        volcano = (abs(j) < 1e-9) and (f_h_i > 2.0e3)
        dust_storm = s_soak_i > 1.0
        tornado_hurricane = (0.23 <= j <= 0.27) and (f_v > 10.0)
        derech_o = (0.07 <= j <= 0.09) and (f_theta_i > 7.0)

        primary_event = None
        if volcano:
            primary_event = "volcano"
        elif earthquake:
            primary_event = "earthquake"
        elif fire_arc:
            primary_event = "fire_arc"
        elif tornado_hurricane:
            primary_event = "tornado_hurricane"
        elif derech_o:
            primary_event = "derecho"
        elif dust_storm:
            primary_event = "dust_storm"

        day_side = is_day_side(lon, subsolar_lon)

        echo_risk = primary_event is not None and (timeslip * 1.0145 > 1.0)

        result = {
            "node_index": i,
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "jitter": round(j, 6),
            "classification": event_type,
            "timeslip": round(timeslip, 6),
            "day_side": day_side,
            "primary_event": primary_event,
            "echo_risk": echo_risk,
            "v_p_i": round(v_p_i, 6),
            "f_h_i": round(f_h_i, 6),
            "s_soak_i": round(s_soak_i, 6),
            "f_v": round(f_v, 6),
            "f_theta_i": round(f_theta_i, 6),
        }

        if primary_event:
            hits.append(result)
        if echo_risk:
            echo_hits.append(result)

    return {
        "timing": {
            "t_arrival_s": round(t_arrival, 6),
            "t_response_s": round(t_response, 6),
            "t_event_epoch": round(t_event_epoch, 6),
            "subsolar_lon_deg": round(subsolar_lon, 3),
        },
        "counts": {
            "nodes": len(nodes),
            "primary_hits": len(hits),
            "echo_hits": len(echo_hits),
        },
        "primary_hits": hits[:20],
        "echo_hits": echo_hits[:20],
    }


def parse_args():
    parser = argparse.ArgumentParser(description="MASER-Earth experimental simulation")
    parser.add_argument("--solar-wind-speed", type=float, default=500.0)
    parser.add_argument("--x-ray-flux", type=float, default=1.2e-05)
    parser.add_argument("--proton-density", type=float, default=12.0)
    parser.add_argument("--m-flare-count", type=int, default=9)
    parser.add_argument("--cme-velocity", type=float, default=1200.0)
    parser.add_argument("--grid-km", type=float, default=108.1)
    parser.add_argument("--node-count", type=int, default=370)
    parser.add_argument("--giza-zero-lat", type=float, default=29.9792)
    parser.add_argument("--giza-zero-lon", type=float, default=31.1340)
    parser.add_argument("--json-input", type=str, default=None)
    return parser.parse_args()


def main():
    args = parse_args()

    if args.json_input:
        with open(args.json_input, "r", encoding="utf-8") as f:
            inputs = json.load(f)
    else:
        inputs = {
            "solar_wind_speed_km_s": args.solar_wind_speed,
            "x_ray_flux_w_m2": args.x_ray_flux,
            "proton_density_cm3": args.proton_density,
            "m_flare_count_24h": args.m_flare_count,
            "cme_velocity_km_s": args.cme_velocity,
            "grid_km": args.grid_km,
            "node_count": args.node_count,
            "giza_zero_lat": args.giza_zero_lat,
            "giza_zero_lon": args.giza_zero_lon,
        }

    result = simulate(inputs)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
