"""Generate the bundled synthetic drive log (app/static/sample/sample_drive.csv).

Synthetic data only: a deterministic 10 Hz drive on a winding public road
(max ~60 km/h) with mostly smooth sections plus a few harsh braking /
accelerating / cornering moments so every metric has something to show.

Run:  python scripts/generate_sample.py
"""
from __future__ import annotations

import csv
import math
import random
from pathlib import Path

HZ = 10
DT = 1.0 / HZ
G = 9.80665
OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "sample" / "sample_drive.csv"

# ("to", target_kmh, accel_g)                         -> ramp speed to target
# ("hold", seconds, lat_amp_g, lat_period_s, wiggle_g) -> constant speed, cornering
PHASES = [
    ("to", 55, 0.15), ("hold", 60, 0.22, 12, 0.03),
    ("to", 0, -0.15), ("hold", 8, 0.0, 0, 0.0),
    ("to", 50, 0.36),                                   # harsh acceleration
    ("hold", 80, 0.28, 10, 0.06),
    ("to", 20, -0.50),                                  # harsh braking
    ("to", 55, 0.18), ("hold", 90, 0.30, 9, 0.05),
    ("hold", 16, 0.47, 8, 0.0),                         # tight corners
    ("to", 30, -0.42),                                  # harsh braking
    ("to", 60, 0.16), ("hold", 100, 0.24, 12, 0.07),
    ("to", 25, -0.55),                                  # harsh braking
    ("to", 50, 0.33),                                   # harsh acceleration
    ("hold", 90, 0.20, 14, 0.04),
    ("to", 0, -0.13), ("hold", 6, 0.0, 0, 0.0),
]


def generate(seed: int = 7) -> list[tuple]:
    rng = random.Random(seed)
    rows: list[tuple] = []
    t, v = 0.0, 0.0  # v in m/s
    hr = 74.0        # synthetic wearable heart rate (first-order lag, ~5 s time constant)

    def emit(ax: float, ay: float) -> None:
        nonlocal t, hr
        target = 72 + 8 * (v * 3.6 / 60) + 90 * max(0.0, abs(ax) - 0.3) + 60 * max(0.0, abs(ay) - 0.35)
        hr += (target - hr) * 0.02
        rows.append((round(t, 2), round(v * 3.6, 2),
                     round(ax + rng.gauss(0, 0.015), 3),
                     round(ay + rng.gauss(0, 0.015), 3),
                     int(round(hr + rng.gauss(0, 0.6)))))
        t += DT

    for phase in PHASES:
        if phase[0] == "to":
            _, target_kmh, ax_g = phase
            target = target_kmh / 3.6
            step = ax_g * G * DT
            while (step > 0 and v < target) or (step < 0 and v > target):
                v = min(target, v + step) if step > 0 else max(target, v + step)
                emit(ax_g, 0.0)
        else:
            _, seconds, amp, period, wiggle = phase
            for k in range(int(seconds * HZ)):
                ay = 0.0
                if period and v > 3:
                    ay = amp * math.sin(2 * math.pi * k * DT / period)
                    ay += wiggle * math.sin(2 * math.pi * k * DT / 1.3)
                emit(0.0, ay)
    return rows


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = generate()
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp_s", "speed_kmh", "accel_long_g", "accel_lat_g", "heart_rate_bpm"])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
