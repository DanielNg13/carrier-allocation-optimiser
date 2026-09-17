"""
generate_data.py
Create the synthetic order book: ~60 days of long-haul orders out of Hanoi.

ALL DATA IN THIS PROJECT IS SYNTHETIC.
The real records are on paper; this dataset is calibrated from the author's
operating experience (see reference.py for every assumption).

Output: data/orders.csv (one row per order, columns as listed in heuristics.py)
Re-running with the same SEED always gives the same file.
"""

import math
import os
import random
import pandas as pd
import reference as ref
from heuristics import capacity, planner_heuristic

SEED = 2026                    # change to create a different synthetic sample
OUTPUT_PATH = os.path.join("data", "orders.csv")
RETAIL_AVG_FILL = 0.90         # CALIBRATE: a retail "truckload" is ~90% of usable space

# CALIBRATE: relative number of orders arriving in each hour.
# Most arrive in the morning; about 6% arrive at or after the 3pm cut-off.
ORDER_HOUR_WEIGHTS = {
    7: 8, 8: 14, 9: 16, 10: 16, 11: 14, 12: 8, 13: 10, 14: 8,   # before cut-off: 94
    15: 3, 16: 2, 17: 1,                                        # after cut-off:   6
}


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def poisson(rng, mean):
    """
    Random count of events with the given average (Poisson distribution).
    Good for "how many orders arrive today". Knuth's simple method.
    """
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    count, product = 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def retail_order_volume(rng):
    """One retail customer's booking: a mix of small and large cartons, in m3."""
    n_cartons = rng.randint(*ref.RETAIL_CARTONS)
    volume = 0
    for _ in range(n_cartons):
        if rng.random() < ref.LARGE_CARTON_SHARE:
            volume += ref.CARTON_CBM["large"]
        else:
            volume += ref.CARTON_CBM["small"]
    return round(volume, 2)


def average_retail_volume():
    """Expected size of one retail order - used to decide how many orders to create."""
    avg_cartons = sum(ref.RETAIL_CARTONS) / 2
    avg_carton = (ref.LARGE_CARTON_SHARE * ref.CARTON_CBM["large"]
                  + (1 - ref.LARGE_CARTON_SHARE) * ref.CARTON_CBM["small"])
    return avg_cartons * avg_carton


def season_multiplier(date):
    """Demand multiplier for one date (1.0 = a normal day)."""
    for start, end, multiplier, _label in ref.SEASONALITY:
        if pd.Timestamp(start) <= date <= pd.Timestamp(end):
            return multiplier
    return 1.0


def season_label(date):
    for start, end, _multiplier, label in ref.SEASONALITY:
        if pd.Timestamp(start) <= date <= pd.Timestamp(end):
            return label
    return "Normal"


# ---------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------
def generate_orders(seed=SEED):
    rng = random.Random(seed)
    dates = pd.date_range(ref.START_DATE, periods=ref.N_DAYS, freq="D")
    hours = list(ORDER_HOUR_WEIGHTS.keys())
    hour_weights = list(ORDER_HOUR_WEIGHTS.values())
    rows = []

    for day, date in enumerate(dates):
        multiplier = season_multiplier(date)

        for vt in ref.VEHICLE_TYPES["vehicle_type"]:
            truckloads = ref.AVG_TRUCKLOADS_PER_DAY[vt] * multiplier
            bulk_truckloads = truckloads * ref.BULK_SHARE
            retail_truckloads = truckloads * (1 - ref.BULK_SHARE)

            # Bulk: one order = one full truck
            n_bulk = poisson(rng, bulk_truckloads)

            # Retail: enough small orders to fill the expected retail trucks
            retail_cbm = retail_truckloads * capacity(vt) * RETAIL_AVG_FILL
            n_retail = poisson(rng, retail_cbm / average_retail_volume())

            for booking_type, count in [("BULK", n_bulk), ("RETAIL", n_retail)]:
                for _ in range(count):
                    if booking_type == "BULK":
                        # Capped at 100% of the truck, so no bulk order needs splitting
                        volume = round(rng.uniform(*ref.BULK_FILL) * capacity(vt), 2)
                        max_wait = ref.BULK_MAX_WAIT_DAYS
                    else:
                        volume = retail_order_volume(rng)
                        max_wait = ref.RETAIL_MAX_WAIT_DAYS

                    hour = rng.choices(hours, weights=hour_weights)[0]
                    after_cutoff = hour >= ref.CUTOFF_HOUR   # rule 5: rolls to tomorrow

                    rows.append({
                        "order_day": day,
                        "order_date": date.date(),
                        "order_hour": hour,
                        "season": season_label(date),
                        "booking_type": booking_type,
                        "vehicle_type": vt,
                        "volume_cbm": volume,
                        "ready_day": day + 1 if after_cutoff else day,
                        "max_wait_days": max_wait,
                    })

    orders = pd.DataFrame(rows)
    orders = orders.sort_values(["order_day", "order_hour"]).reset_index(drop=True)
    orders.insert(0, "order_id", [f"O{i + 1:04d}" for i in range(len(orders))])
    orders.insert(1, "synthetic", True)   # label travels with the data
    return orders


# ---------------------------------------------------------------
# Run: `python generate_data.py`
# ---------------------------------------------------------------
if __name__ == "__main__":
    orders = generate_orders()
    os.makedirs("data", exist_ok=True)
    orders.to_csv(OUTPUT_PATH, index=False)

    print("SYNTHETIC DATA - order book generated\n")
    print(f"Saved {len(orders)} orders to {OUTPUT_PATH}\n")
    print(orders.head(8).to_string(index=False), "\n")

    print("Orders by type:")
    print(pd.crosstab(orders["booking_type"], orders["vehicle_type"], margins=True), "\n")

    # Check 1: does demand follow the seasonality?
    daily_cbm = orders.groupby(["order_day", "season"])["volume_cbm"].sum().reset_index()
    print("Average m3 per day by season:")
    print(daily_cbm.groupby("season")["volume_cbm"].mean().round(1).to_string(), "\n")

    retail = orders[orders["booking_type"] == "RETAIL"]
    print(f"Average retail order: {retail['volume_cbm'].mean():.2f} m3")
    print(f"Orders after the {ref.CUTOFF_HOUR}:00 cut-off (rolled to next day): "
          f"{(orders['ready_day'] > orders['order_day']).mean():.0%}\n")

    # Check 2: calibration against the real operation (planner rules on this data)
    plan = planner_heuristic(orders)
    sub_trips = plan[plan["carrier"] != "INHOUSE"]["trip_id"].nunique()
    weeks = ref.N_DAYS / 7
    print("Calibration check (planner heuristic on this data):")
    print(f"  Sub trips per week:        {sub_trips / weeks:.1f}   (target: 5-6)")
    print(f"  Orders affected by noise:  {plan['noise_type'].notna().mean():.0%}   (target: ~12%)")
    print(f"  Late orders:               {plan['late'].sum()}")