"""
cost_matrix.py
Cost of every allocation option: in-house truck vs subcontractor A/B/C,
for each route (BULK / RETAIL) and vehicle type (CONTAINER / REEFER).

ALL DATA IN THIS PROJECT IS SYNTHETIC. Inputs come from reference.py.

Two in-house costing modes:
  - "marginal": only costs that exist because the truck moved
                (fuel, tolls, driver). Correct for dispatch decisions.
  - "absorbed": marginal + fixed overhead (depreciation, insurance, depot).
                Common in accounting, misleading for dispatch.
"""

import pandas as pd
import reference as ref

COSTING_MODES = ["marginal", "absorbed"]
IN_HOUSE = "INHOUSE"


# ---------------------------------------------------------------
# Small lookup helpers
# ---------------------------------------------------------------
def get_route(route):
    """Return one row of the ROUTES table, e.g. get_route('BULK')."""
    return ref.ROUTES.set_index("route").loc[route]


def get_vehicle(vehicle_type):
    """Return one row of the VEHICLE_TYPES table."""
    return ref.VEHICLE_TYPES.set_index("vehicle_type").loc[vehicle_type]


def get_sub(carrier):
    """Return one row of the SUBCONTRACTORS table."""
    return ref.SUBCONTRACTORS.set_index("carrier").loc[carrier]


# ---------------------------------------------------------------
# 1. In-house cost
# ---------------------------------------------------------------
def inhouse_round_trip_breakdown(vehicle_type, route, costing="marginal"):
    """Cost lines for one full in-house round trip (Hanoi -> HCMC -> Hanoi)."""
    if costing not in COSTING_MODES:
        raise ValueError(f"costing must be one of {COSTING_MODES}, got {costing!r}")

    r = get_route(route)
    v = get_vehicle(vehicle_type)
    round_trip_km = 2 * r["km_one_way"]

    fuel = round_trip_km / 100 * v["fuel_l_per_100km"] * ref.DIESEL_VND_PER_L
    tolls = round_trip_km * ref.TOLL_VND_PER_KM
    driver = ref.DRIVER_VND_PER_ROUND_TRIP  # driver also covers maintenance

    overhead = 0
    if costing == "absorbed":
        overhead = ref.OVERHEAD_VND_PER_TRUCK_DAY * ref.ROUND_TRIP_DAYS

    return {
        "fuel": fuel,
        "tolls": tolls,
        "driver": driver,
        "overhead": overhead,
        "total": fuel + tolls + driver + overhead,
    }


def inhouse_trip_cost(vehicle_type, route, costing="marginal"):
    """Cost charged to ONE southbound trip (backhaul pays the rest)."""
    round_trip = inhouse_round_trip_breakdown(vehicle_type, route, costing)["total"]
    return round_trip * ref.SOUTHBOUND_COST_SHARE


# ---------------------------------------------------------------
# 2. Subcontractor cost
# ---------------------------------------------------------------
def sub_is_eligible(carrier, vehicle_type):
    """A sub can only take a reefer trip if it has reefer trucks."""
    if vehicle_type == "REEFER":
        return bool(get_sub(carrier)["offers_reefer"])
    return True


def sub_trip_cost(carrier, vehicle_type, route):
    """
    One-way sub price = our MARGINAL one-way cost + that carrier's premium.
    Always based on marginal cost: the sub's price does not change
    just because we choose to allocate our overhead differently.
    Returns None if the carrier cannot do this trip.
    """
    if not sub_is_eligible(carrier, vehicle_type):
        return None
    base = inhouse_trip_cost(vehicle_type, route, costing="marginal")
    return base + get_sub(carrier)["premium_vnd"]


# ---------------------------------------------------------------
# 3. Handling (regional contractors)
# ---------------------------------------------------------------
def handling_cost_per_order(route):
    """Same whoever carries the goods -> not a decision cost, but part of total spend."""
    return get_route(route)["n_drops"] * ref.HANDLING_VND_PER_DROP


# ---------------------------------------------------------------
# 4. Full cost table (used by heuristics, model and evaluation)
# ---------------------------------------------------------------
def build_cost_table(costing="marginal"):
    """
    One row per (route, vehicle_type, carrier) option.
    cost_index: 100 = our own MARGINAL trip cost for that route and vehicle.
    Indexing lets us publish results without real VND price levels.
    """
    rows = []
    for route in ref.ROUTES["route"]:
        for vehicle_type in ref.VEHICLE_TYPES["vehicle_type"]:
            baseline = inhouse_trip_cost(vehicle_type, route, "marginal")

            # In-house option
            cost = inhouse_trip_cost(vehicle_type, route, costing)
            rows.append({
                "route": route,
                "vehicle_type": vehicle_type,
                "carrier": IN_HOUSE,
                "eligible": True,
                "cost_vnd": round(cost),
                "cost_index": round(cost / baseline * 100, 1),
            })

            # Subcontractor options
            for carrier in ref.SUBCONTRACTORS["carrier"]:
                cost = sub_trip_cost(carrier, vehicle_type, route)
                rows.append({
                    "route": route,
                    "vehicle_type": vehicle_type,
                    "carrier": carrier,
                    "eligible": cost is not None,
                    "cost_vnd": None if cost is None else round(cost),
                    "cost_index": None if cost is None else round(cost / baseline * 100, 1),
                })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------
# Quick self-test: run `python cost_matrix.py`
# ---------------------------------------------------------------
if __name__ == "__main__":
    pd.set_option("display.width", 120)
    print("SYNTHETIC DATA - cost matrix\n")

    print("In-house round-trip breakdown (CONTAINER, BULK, marginal):")
    for line, value in inhouse_round_trip_breakdown("CONTAINER", "BULK").items():
        print(f"  {line:<10} {value:>14,.0f} VND")
    print()

    for mode in COSTING_MODES:
        print(f"Cost table - {mode} in-house costing:")
        print(build_cost_table(mode).to_string(index=False))
        print()

    # The costing trap: does absorbed costing make subbing look cheaper?
    print("Costing trap check (absorbed in-house vs cheapest sub A):")
    for route in ref.ROUTES["route"]:
        for vt in ref.VEHICLE_TYPES["vehicle_type"]:
            own = inhouse_trip_cost(vt, route, "absorbed")
            sub_a = sub_trip_cost("A", vt, route)
            verdict = "sub looks cheaper" if sub_a < own else "own truck cheaper"
            print(f"  {route:<7} {vt:<10} own {own:>12,.0f}  A {sub_a:>12,.0f}  -> {verdict}")

    print("\nHandling per order:",
          {r: f"{handling_cost_per_order(r):,.0f}" for r in ref.ROUTES["route"]})