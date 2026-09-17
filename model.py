"""
model.py
The optimiser: a binary assignment MIP (PuLP) that decides, one day at a time,
which orders go on which truck (own truck or subcontractor A/B/C).

ALL DATA IN THIS PROJECT IS SYNTHETIC.

Each day the model sees the same things the planner saw:
  - the open orders (ready today, or held from earlier days)
  - which of our own trucks are back in Hanoi
  - how many trucks each subcontractor can give us today
and it minimises freight cost, subject to:
  - truck space (m3)
  - reefer cargo only on reefer trucks (B has no reefers)
  - a bulk booking takes a whole truck; retail orders share a truck
  - an order is never split across trucks
  - each sub's daily truck limit
  - an order past its waiting limit must ship today (or pay a big late penalty)

It does NOT see the future: it plans today with today's information,
exactly like the planner. Same constraints, different decision logic.
"""

import os
import time
import pulp
import pandas as pd
import reference as ref
from cost_matrix import build_cost_table, handling_cost_per_order, sub_is_eligible, IN_HOUSE
from heuristics import capacity, default_truck_ready_days, is_due, MAX_EXTRA_DAYS

HOLD_PENALTY_VND = 200_000       # CALIBRATE: service cost of making a retail order wait one day
LATE_PENALTY_VND = 50_000_000    # CALIBRATE: very large, so the model only ships late if it has no truck at all
SOLVER_TIME_LIMIT = 30           # seconds per day (each day normally solves in well under 1 second)


# ---------------------------------------------------------------
# Inputs for one day
# ---------------------------------------------------------------
def trip_cost_lookup(costing):
    """{(route, vehicle_type, carrier): cost of one trip} for eligible options."""
    table = build_cost_table(costing)
    table = table[table["eligible"]]
    return {(row.route, row.vehicle_type, row.carrier): row.cost_vnd
            for row in table.itertuples()}


def build_bins(day, truck_ready, truck_type):
    """
    A "bin" is one truck that could leave Hanoi today:
      - each of our own trucks that is back home
      - each subcontractor truck slot (up to that sub's daily limit)
    """
    bins = []
    for truck_id, ready_day in truck_ready.items():
        if ready_day <= day:
            bins.append({"carrier": IN_HOUSE, "vehicle_type": truck_type[truck_id],
                         "truck_id": truck_id})

    for sub in ref.SUBCONTRACTORS.itertuples():
        for vt in ref.VEHICLE_TYPES["vehicle_type"]:
            if not sub_is_eligible(sub.carrier, vt):
                continue
            for _slot in range(sub.max_trucks_per_day):
                bins.append({"carrier": sub.carrier, "vehicle_type": vt, "truck_id": None})
    return bins


# ---------------------------------------------------------------
# The MIP for one day
# ---------------------------------------------------------------
def solve_day(day, open_orders, bins, costs):
    """
    Build and solve today's model.
    Returns a list of trips: {"carrier", "truck_id", "route", "vehicle_type", "orders"}.
    """
    prob = pulp.LpProblem(f"dispatch_day_{day}", pulp.LpMinimize)
    B = range(len(bins))
    O = range(len(open_orders))

    # --- Decision variables (all 0/1) ---
    # use_retail[b] = 1 if truck b leaves today on the RETAIL route
    # use_bulk[b]   = 1 if truck b leaves today carrying a BULK booking
    # x[(o, b)]     = 1 if order o goes on truck b
    # late[o]       = 1 if a due order cannot ship today
    use_retail = {b: pulp.LpVariable(f"retail_{b}", cat="Binary") for b in B}
    use_bulk = {b: pulp.LpVariable(f"bulk_{b}", cat="Binary") for b in B}
    x = {}
    for o in O:
        for b in B:
            if open_orders[o]["vehicle_type"] == bins[b]["vehicle_type"]:
                x[(o, b)] = pulp.LpVariable(f"x_{o}_{b}", cat="Binary")
    due = [o for o in O if is_due(open_orders[o], day)]
    not_due = [o for o in O if o not in due]
    late = {o: pulp.LpVariable(f"late_{o}", cat="Binary") for o in due}

    def shipped(o):
        """1 if order o goes on any truck today, else 0."""
        return pulp.lpSum(x[(o, b)] for b in B if (o, b) in x)

    # --- Objective: trip costs + penalties ---
    trip_costs = []
    for b in B:
        vt, carrier = bins[b]["vehicle_type"], bins[b]["carrier"]
        trip_costs.append(costs[("RETAIL", vt, carrier)] * use_retail[b])
        trip_costs.append(costs[("BULK", vt, carrier)] * use_bulk[b])
    holding = [HOLD_PENALTY_VND * (1 - shipped(o)) for o in not_due]
    lateness = [LATE_PENALTY_VND * late[o] for o in due]
    prob += pulp.lpSum(trip_costs) + pulp.lpSum(holding) + pulp.lpSum(lateness)

    # --- Constraints ---
    for b in B:
        # A truck does one job today: retail, bulk, or nothing
        prob += use_retail[b] + use_bulk[b] <= 1

        # Retail: total volume fits, and only if the truck is used for retail
        retail_on_b = [(o, b) for o in O
                       if (o, b) in x and open_orders[o]["booking_type"] == "RETAIL"]
        prob += (pulp.lpSum(open_orders[o]["volume_cbm"] * x[(o, b)] for o, b in retail_on_b)
                 <= capacity(bins[b]["vehicle_type"]) * use_retail[b])

        # Bulk: at most one bulk booking per truck (it has its own multi-stop route)
        bulk_on_b = [(o, b) for o in O
                     if (o, b) in x and open_orders[o]["booking_type"] == "BULK"]
        prob += pulp.lpSum(x[key] for key in bulk_on_b) <= use_bulk[b]

    for o in due:
        prob += shipped(o) + late[o] == 1     # must ship today, or be marked late
    for o in not_due:
        prob += shipped(o) <= 1               # may ship today or wait (never split)

    for sub in ref.SUBCONTRACTORS.itertuples():
        sub_bins = [b for b in B if bins[b]["carrier"] == sub.carrier]
        prob += (pulp.lpSum(use_retail[b] + use_bulk[b] for b in sub_bins)
                 <= sub.max_trucks_per_day)

    # --- Solve ---
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=SOLVER_TIME_LIMIT))
    status = pulp.LpStatus[prob.status]
    if status != "Optimal":
        print(f"  Warning: day {day} solver status = {status}")

    # --- Read the answer ---
    trips = []
    for b in B:
        if use_retail[b].value() > 0.5:
            route = "RETAIL"
        elif use_bulk[b].value() > 0.5:
            route = "BULK"
        else:
            continue
        on_truck = [open_orders[o] for o in O
                    if (o, b) in x and x[(o, b)].value() > 0.5]
        if on_truck:   # skip an "empty" trip the solver left switched on
            trips.append({**bins[b], "route": route, "orders": on_truck})
    return trips


# ---------------------------------------------------------------
# Rolling day-by-day loop
# ---------------------------------------------------------------
def run_optimiser(orders, costing="marginal", truck_ready_days=None, verbose=False):
    """Solve one day at a time; returns a plan in the same format as heuristics.py."""
    costs = trip_cost_lookup(costing)
    if truck_ready_days is None:
        truck_ready_days = default_truck_ready_days()
    truck_ready = dict(truck_ready_days)
    truck_type = dict(zip(ref.FLEET["truck_id"], ref.FLEET["vehicle_type"]))

    open_orders = orders.sort_values(["ready_day", "order_id"]).to_dict("records")
    results = []
    trip_counter = 0
    first_day = int(orders["ready_day"].min())
    last_day = int(orders["ready_day"].max()) + MAX_EXTRA_DAYS

    for day in range(first_day, last_day + 1):
        if not open_orders:
            break
        today = [o for o in open_orders if o["ready_day"] <= day]
        if not today:
            continue

        bins = build_bins(day, truck_ready, truck_type)
        trips = solve_day(day, today, bins, costs)

        done_ids = set()
        for trip in trips:
            trip_counter += 1
            if trip["carrier"] == IN_HOUSE:
                truck_ready[trip["truck_id"]] = day + ref.ROUND_TRIP_DAYS
            for o in trip["orders"]:
                row = dict(o)
                row.update({
                    "dispatch_day": day,
                    "route": trip["route"],
                    "carrier": trip["carrier"],
                    "truck_id": trip["truck_id"],
                    "trip_id": f"M{trip_counter:04d}",
                })
                results.append(row)
                done_ids.add(o["order_id"])
        open_orders = [o for o in open_orders if o["order_id"] not in done_ids]

        if verbose:
            print(f"  day {day:>2}: {len(today):>3} orders waiting, {len(trips)} trips sent, "
                  f"{len(today) - len(done_ids)} held to tomorrow")

    for o in open_orders:
        row = dict(o)
        row.update({"dispatch_day": None, "route": o["booking_type"],
                    "carrier": "UNSERVED", "truck_id": None, "trip_id": None})
        results.append(row)

    plan = pd.DataFrame(results).sort_values("order_id").reset_index(drop=True)
    plan["days_waited"] = plan["dispatch_day"] - plan["ready_day"]
    plan["late"] = (plan["days_waited"] > plan["max_wait_days"]) | (plan["carrier"] == "UNSERVED")
    plan["noise_type"] = None
    return plan


# ---------------------------------------------------------------
# Cost of any plan (used here and in evaluate.py)
# ---------------------------------------------------------------
def freight_cost(plan, costing="marginal"):
    """
    Total spend of a plan, in VND:
      trips    = one cost per truck trip (own or sub)
      handling = regional contractors at the drops (same for every method)
    """
    costs = trip_cost_lookup(costing)
    shipped = plan[plan["trip_id"].notna()]
    trips = shipped.drop_duplicates("trip_id")
    trip_total = sum(costs[(t.route, t.vehicle_type, t.carrier)] for t in trips.itertuples())
    handling_total = sum(handling_cost_per_order(r) for r in shipped["route"])
    return {"trips": trip_total, "handling": handling_total,
            "total": trip_total + handling_total}


# ---------------------------------------------------------------
# Quick self-test: run `python model.py`
# ---------------------------------------------------------------
if __name__ == "__main__":
    from heuristics import planner_heuristic, naive_cheapest

    path = os.path.join("data", "orders.csv")
    if not os.path.exists(path):
        raise SystemExit("data/orders.csv not found - run `python generate_data.py` first.")
    orders = pd.read_csv(path)
    print(f"SYNTHETIC DATA - optimiser self-test on {len(orders)} orders\n")

    start = time.time()
    mip = run_optimiser(orders, costing="marginal", verbose=True)
    print(f"\nSolved {orders['ready_day'].max() + 1}+ days in {time.time() - start:.1f} s\n")

    methods = {
        "Planner rules, with noise": planner_heuristic(orders),
        "Planner rules, no noise": planner_heuristic(orders, noise_rate=0.0),
        "Naive cheapest": naive_cheapest(orders, "marginal"),
        "Optimiser (MIP)": mip,
    }
    rows = []
    for name, plan in methods.items():
        cost = freight_cost(plan, "marginal")
        rows.append({
            "method": name,
            "trips": plan["trip_id"].nunique(),
            "sub_trips": plan.loc[plan["carrier"].isin(ref.SUBCONTRACTORS["carrier"]), "trip_id"].nunique(),
            "late_orders": int(plan["late"].sum()),
            "avg_wait_days": round(plan["days_waited"].mean(), 2),
            "spend_M_VND": round(cost["total"] / 1e6, 1),
        })
    print("Comparison (marginal costing, SYNTHETIC):")
    print(pd.DataFrame(rows).to_string(index=False))