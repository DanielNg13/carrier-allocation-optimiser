"""
heuristics.py
Rule-based allocation methods to compare against the optimiser:

  1. planner_heuristic() - encodes planner_rules.md (my real manual rules),
                           plus ~12% human deviation noise.
  2. naive_cheapest()    - "always pick the cheapest eligible carrier",
                           a simple baseline to show what the optimiser adds.

ALL DATA IN THIS PROJECT IS SYNTHETIC.

IMPORTANT: the planner heuristic does NOT look at the cost table.
It follows the rules committed in planner_rules.md before the cost model
existed, so the comparison with the optimiser is not circular.

ORDER TABLE (input) - generate_data.py must produce these columns:
  order_id       unique id, e.g. "O0001". One order = one customer's booking.
  booking_type   "BULK" (own truck, multi-stop) or "RETAIL" (consolidated, direct)
  vehicle_type   "CONTAINER" or "REEFER"
  volume_cbm     cubic metres of cartons; never above that vehicle's capacity
  ready_day      first day the order can ship (after-cutoff orders already +1)
  max_wait_days  days it may wait after ready_day before it counts as late
"""

import random
import pandas as pd
import reference as ref
from cost_matrix import build_cost_table, sub_is_eligible, IN_HOUSE

RETAIL_MIN_FILL = 0.85   # CALIBRATE: "handler says it's full" = at least 85% of usable space
MAX_EXTRA_DAYS = 30      # safety stop: how long to keep planning after the last order


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def capacity(vehicle_type):
    """Usable cargo space of one truck, in cubic metres."""
    return ref.VEHICLE_TYPES.set_index("vehicle_type").loc[vehicle_type, "capacity_cbm"]


def default_truck_ready_days():
    """
    Day each in-house truck is next available in Hanoi.
    Spread evenly over one round trip, so the fleet doesn't all start at home on day 0.
    """
    ready = {}
    for vt in ref.VEHICLE_TYPES["vehicle_type"]:
        truck_ids = ref.FLEET.loc[ref.FLEET["vehicle_type"] == vt, "truck_id"].tolist()
        for i, truck_id in enumerate(truck_ids):
            ready[truck_id] = round(i * ref.ROUND_TRIP_DAYS / len(truck_ids))
    return ready


def is_due(order, day):
    """True if the order cannot wait any longer without being late."""
    return day >= order["ready_day"] + order["max_wait_days"]


def make_trip(route, vehicle_type, trip_orders, day):
    """A trip = one truck going south with one or more orders on it."""
    return {
        "route": route,
        "vehicle_type": vehicle_type,
        "orders": trip_orders,
        "volume": sum(o["volume_cbm"] for o in trip_orders),
        "due": any(is_due(o, day) for o in trip_orders),
        "earliest_ready": min(o["ready_day"] for o in trip_orders),
    }


def pack_retail(orders, truck_space):
    """
    First-fit packing: put each order in the first truck with room.
    An order is never split (planner rule 8).
    """
    trucks = []
    for order in orders:
        placed = False
        for truck in trucks:
            if truck["volume"] + order["volume_cbm"] <= truck_space:
                truck["orders"].append(order)
                truck["volume"] += order["volume_cbm"]
                placed = True
                break
        if not placed:
            trucks.append({"orders": [order], "volume": order["volume_cbm"]})
    return [truck["orders"] for truck in trucks]


# ---------------------------------------------------------------
# Core day-by-day simulation (shared by both methods)
# ---------------------------------------------------------------
def run_allocation(orders, ranking_fn, retail_waits_for_own_truck, retail_first,
                   noise_rate=0.0, seed=42, truck_ready_days=None):
    """
    Simulate dispatch one day at a time.

    ranking_fn(route, vehicle_type) -> carriers in the order they are tried.
    retail_waits_for_own_truck      -> planner rule 3 (retail waits rather than sub).
    retail_first                    -> planner rule 2 (own trucks go to retail first).
    noise_rate                      -> chance of each human deviation.
    """
    rng = random.Random(seed)   # fixed seed = same result every run
    if truck_ready_days is None:
        truck_ready_days = default_truck_ready_days()
    truck_ready = dict(truck_ready_days)   # copy, so the input is not changed
    truck_type = dict(zip(ref.FLEET["truck_id"], ref.FLEET["vehicle_type"]))
    sub_capacity = dict(zip(ref.SUBCONTRACTORS["carrier"], ref.SUBCONTRACTORS["max_trucks_per_day"]))

    open_orders = orders.sort_values(["ready_day", "order_id"]).to_dict("records")
    noise_flags = {}   # order_id -> type of deviation that affected it
    results = []
    trip_counter = 0
    first_day = int(orders["ready_day"].min())
    last_day = int(orders["ready_day"].max()) + MAX_EXTRA_DAYS

    for day in range(first_day, last_day + 1):
        if not open_orders:
            break
        ready_now = [o for o in open_orders if o["ready_day"] <= day]

        # ---- 1. Build today's candidate trips ----
        trips = []
        for o in ready_now:
            if o["booking_type"] == "BULK":
                trips.append(make_trip("BULK", o["vehicle_type"], [o], day))

        for vt in ref.VEHICLE_TYPES["vehicle_type"]:
            retail = [o for o in ready_now
                      if o["booking_type"] == "RETAIL" and o["vehicle_type"] == vt]
            for packed in pack_retail(retail, capacity(vt)):
                trip = make_trip("RETAIL", vt, packed, day)
                full_enough = trip["volume"] >= RETAIL_MIN_FILL * capacity(vt)
                if not (full_enough or trip["due"]):
                    continue   # rule 7: wait for more orders to fill the truck

                # Deviation: push a retail order to a later truck although it fit
                kept = []
                for o in packed:
                    if not is_due(o, day) and rng.random() < noise_rate:
                        noise_flags.setdefault(o["order_id"], "pushed_retail")
                    else:
                        kept.append(o)
                if kept:
                    trips.append(make_trip("RETAIL", vt, kept, day))

        # ---- 2. Decide which trips get first pick of own trucks ----
        retail_trips = [t for t in trips if t["route"] == "RETAIL"]
        bulk_trips = [t for t in trips if t["route"] == "BULK"]
        bulk_first_today = False
        if retail_first:
            # Deviation: some days bulk gets own trucks before retail
            bulk_first_today = rng.random() < noise_rate
            if bulk_first_today:
                trips = bulk_trips + retail_trips
            else:
                trips = retail_trips + bulk_trips
        else:
            trips.sort(key=lambda t: t["earliest_ready"])   # first come, first served

        # ---- 3. Assign each trip to a carrier ----
        sub_used = {c: 0 for c in sub_capacity}
        done_ids = set()
        for trip in trips:
            vt = trip["vehicle_type"]
            options = ranking_fn(trip["route"], vt)
            if trip["route"] == "RETAIL" and retail_waits_for_own_truck and not trip["due"]:
                options = [IN_HOUSE]   # rule 3: retail waits for our own truck

            carrier, truck_id = None, None
            for option in options:
                if option == IN_HOUSE:
                    free = [t for t in truck_ready
                            if truck_type[t] == vt and truck_ready[t] <= day]
                    if free:
                        free.sort(key=lambda t: truck_ready[t])   # longest-waiting truck first
                        carrier, truck_id = IN_HOUSE, free[0]
                        break
                elif sub_is_eligible(option, vt) and sub_used[option] < sub_capacity[option]:
                    carrier = option
                    break

            if carrier is None:
                continue   # nothing available today: the orders wait

            # Deviation: phone a different sub than the first choice
            if carrier != IN_HOUSE and rng.random() < noise_rate:
                others = [c for c in sub_capacity
                          if c != carrier and sub_is_eligible(c, vt)
                          and sub_used[c] < sub_capacity[c]]
                if others:
                    carrier = rng.choice(others)
                    for o in trip["orders"]:
                        noise_flags.setdefault(o["order_id"], "carrier_swap")

            if bulk_first_today and trip["route"] == "BULK" and carrier == IN_HOUSE and retail_trips:
                for o in trip["orders"]:
                    noise_flags.setdefault(o["order_id"], "bulk_priority")

            # Commit the decision
            if carrier == IN_HOUSE:
                truck_ready[truck_id] = day + ref.ROUND_TRIP_DAYS
            else:
                sub_used[carrier] += 1
            trip_counter += 1
            for o in trip["orders"]:
                row = dict(o)
                row.update({
                    "dispatch_day": day,
                    "route": trip["route"],
                    "carrier": carrier,
                    "truck_id": truck_id,
                    "trip_id": f"T{trip_counter:04d}",
                })
                results.append(row)
                done_ids.add(o["order_id"])

        open_orders = [o for o in open_orders if o["order_id"] not in done_ids]

    # Anything still open after the safety stop is unserved
    for o in open_orders:
        row = dict(o)
        row.update({"dispatch_day": None, "route": o["booking_type"],
                    "carrier": "UNSERVED", "truck_id": None, "trip_id": None})
        results.append(row)

    plan = pd.DataFrame(results).sort_values("order_id").reset_index(drop=True)
    plan["days_waited"] = plan["dispatch_day"] - plan["ready_day"]
    plan["late"] = (plan["days_waited"] > plan["max_wait_days"]) | (plan["carrier"] == "UNSERVED")
    plan["noise_type"] = plan["order_id"].map(noise_flags)
    return plan


# ---------------------------------------------------------------
# The two public methods
# ---------------------------------------------------------------
def planner_heuristic(orders, noise_rate=ref.NOISE_RATE, seed=42, truck_ready_days=None):
    """My manual rules (planner_rules.md). Uses NO cost information."""
    sub_order = ref.SUBCONTRACTORS.sort_values("preference_rank")["carrier"].tolist()

    def planner_ranking(route, vehicle_type):
        return [IN_HOUSE] + sub_order   # rule 1 (own first), rule 4 (A, B, C)

    return run_allocation(orders, planner_ranking,
                          retail_waits_for_own_truck=True, retail_first=True,
                          noise_rate=noise_rate, seed=seed,
                          truck_ready_days=truck_ready_days)


def naive_cheapest(orders, costing="marginal", truck_ready_days=None):
    """Baseline: every trip goes to the cheapest carrier with capacity. No planner rules."""
    table = build_cost_table(costing)
    table = table[table["eligible"]]

    def cheapest_ranking(route, vehicle_type):
        rows = table[(table["route"] == route) & (table["vehicle_type"] == vehicle_type)]
        return rows.sort_values("cost_vnd")["carrier"].tolist()

    return run_allocation(orders, cheapest_ranking,
                          retail_waits_for_own_truck=False, retail_first=False,
                          noise_rate=0.0, truck_ready_days=truck_ready_days)


# ---------------------------------------------------------------
# Quick self-test: run `python heuristics.py`
# Uses a small throwaway order set (the real one comes from generate_data.py).
# ---------------------------------------------------------------
def make_test_orders(n_days=20, seed=1):
    rng = random.Random(seed)
    rows = []
    for day in range(n_days):
        daily_mix = [
            ("BULK", "CONTAINER", 1),
            ("BULK", "REEFER", rng.choice([0, 1])),
            ("RETAIL", "CONTAINER", 6),
            ("RETAIL", "REEFER", rng.randint(0, 3)),
        ]
        for booking_type, vt, count in daily_mix:
            for _ in range(count):
                if booking_type == "BULK":
                    volume = round(rng.uniform(*ref.BULK_FILL) * capacity(vt), 2)
                    wait = ref.BULK_MAX_WAIT_DAYS
                else:
                    volume = round(rng.uniform(1.0, 5.0), 2)   # rough size, test only
                    wait = ref.RETAIL_MAX_WAIT_DAYS
                rows.append({
                    "order_id": f"O{len(rows) + 1:04d}",
                    "booking_type": booking_type,
                    "vehicle_type": vt,
                    "volume_cbm": volume,
                    "ready_day": day,
                    "max_wait_days": wait,
                })
    return pd.DataFrame(rows)


def summarise(plan, name):
    print(f"--- {name} ---")
    print(pd.crosstab(plan["booking_type"], plan["carrier"], margins=True, margins_name="Total"))
    print(f"Trips: {plan['trip_id'].nunique()}   "
          f"Late orders: {plan['late'].sum()}   "
          f"Orders affected by noise: {plan['noise_type'].notna().mean():.0%}")
    print()


if __name__ == "__main__":
    print("SYNTHETIC DATA - heuristics self-test (throwaway orders)\n")
    orders = make_test_orders()
    print(f"{len(orders)} test orders over {orders['ready_day'].nunique()} days\n")

    summarise(planner_heuristic(orders, noise_rate=0.0), "Planner rules, no noise")
    noisy = planner_heuristic(orders)
    summarise(noisy, "Planner rules, with noise")
    print("Noise types:", noisy["noise_type"].value_counts().to_dict(), "\n")
    summarise(naive_cheapest(orders, "marginal"), "Naive cheapest (marginal costing)")
    summarise(naive_cheapest(orders, "absorbed"), "Naive cheapest (absorbed costing)")