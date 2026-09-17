"""
reference.py
Reference tables and assumptions for the Carrier Allocation Optimiser.

ALL DATA IN THIS PROJECT IS SYNTHETIC.
Numbers are calibrated from the author's experience as a transport planner
at a family-owned 3PL in Vietnam. Every assumed number is tagged # CALIBRATE.
This file contains NO logic - only assumptions - so reviewers can check them.
"""

import pandas as pd

# ---------------------------------------------------------------
# 1. Planning horizon and seasonality
# ---------------------------------------------------------------
START_DATE = "2026-01-05"   # CALIBRATE: 60-day window spanning Tet 2026
N_DAYS = 60                 # CALIBRATE

# Tet (Lunar New Year) 2026 is on 17 Feb. Demand is multiplied by these factors.
SEASONALITY = [
    # (first day,    last day,     multiplier, label)
    ("2026-01-26", "2026-02-15", 1.40, "Pre-Tet peak"),        # CALIBRATE
    ("2026-02-16", "2026-02-20", 0.10, "Tet shutdown"),        # CALIBRATE
    ("2026-02-21", "2026-02-27", 0.80, "Post-Tet slow week"),  # CALIBRATE
]

# ---------------------------------------------------------------
# 2. Routes (fixed - the model does NOT choose routes)
# ---------------------------------------------------------------
ROUTES = pd.DataFrame([
    {
        "route": "BULK",      # one customer, multi-stop
        "stops": "Hanoi > Nghe An/Ha Tinh > Dak Lak > HCMC",
        "km_one_way": 1550,   # CALIBRATE
        "transit_days": 4,    # CALIBRATE: includes 3-4 hrs paperwork per stop
        "n_drops": 3,         # CALIBRATE
    },
    {
        "route": "RETAIL",    # many customers, consolidated, direct
        "stops": "Hanoi > HCMC (direct)",
        "km_one_way": 1700,   # CALIBRATE
        "transit_days": 3,    # CALIBRATE
        "n_drops": 1,         # CALIBRATE
    },
])

# ---------------------------------------------------------------
# 3. Vehicle types and in-house fleet
# ---------------------------------------------------------------
VEHICLE_TYPES = pd.DataFrame([
    {"vehicle_type": "CONTAINER", "capacity_cbm": 28, "fuel_l_per_100km": 35},  # CALIBRATE: 20ft ~33 m3 internal, ~28 usable
    {"vehicle_type": "REEFER",    "capacity_cbm": 24, "fuel_l_per_100km": 33},  # CALIBRATE: 20ft reefer, ~24 m3 usable
])

N_CONTAINER_TRUCKS = 10   # CALIBRATE
N_REEFER_TRUCKS = 5       # CALIBRATE


def build_fleet():
    """Create one row per in-house truck. All trucks are based in Hanoi."""
    rows = []
    for i in range(1, N_CONTAINER_TRUCKS + 1):
        rows.append({"truck_id": f"C{i:02d}", "vehicle_type": "CONTAINER", "home_depot": "Hanoi"})
    for i in range(1, N_REEFER_TRUCKS + 1):
        rows.append({"truck_id": f"R{i:02d}", "vehicle_type": "REEFER", "home_depot": "Hanoi"})
    return pd.DataFrame(rows)


FLEET = build_fleet()

# ---------------------------------------------------------------
# 4. In-house cost inputs (VND)
# ---------------------------------------------------------------
DIESEL_VND_PER_L = 28_000                 # CALIBRATE
TOLL_VND_PER_KM = 2_000                   # CALIBRATE
DRIVER_VND_PER_ROUND_TRIP = 23_000_000    # CALIBRATE: driver also covers maintenance
OVERHEAD_VND_PER_TRUCK_DAY = 900_000      # CALIBRATE: fully-absorbed costing only
ROUND_TRIP_DAYS = 12                      # CALIBRATE: 4 down, 1-2 loading in HCMC, return
SOUTHBOUND_COST_SHARE = 0.5               # CALIBRATE: loaded backhaul pays the other half

# Regional contractors (warehousing, handling, short-haul at stops).
# Same cost whichever truck carries the goods -> excluded from the decision,
# but INCLUDED in total freight spend so savings % isn't inflated.
HANDLING_VND_PER_DROP = 1_500_000         # CALIBRATE

# ---------------------------------------------------------------
# 5. Long-haul subcontractors (anonymised)
# Subs charge a one-way price = our own MARGINAL one-way cost + a fixed premium.
# No backhaul commitment: we pay one way only.
# Preference order and price order are the same: A is called first AND is cheapest.
SUBCONTRACTORS = pd.DataFrame([
    {"carrier": "A", "preference_rank": 1, "premium_vnd": 1_500_000, "offers_reefer": True,  "max_trucks_per_day": 2},  # CALIBRATE
    {"carrier": "B", "preference_rank": 2, "premium_vnd": 3_000_000, "offers_reefer": False, "max_trucks_per_day": 1},  # CALIBRATE
    {"carrier": "C", "preference_rank": 3, "premium_vnd": 4_500_000, "offers_reefer": True,  "max_trucks_per_day": 1},  # CALIBRATE: assumed same step as A->B
])

# ---------------------------------------------------------------
# 6. Demand and planner behaviour
# ---------------------------------------------------------------
AVG_TRUCKLOADS_PER_DAY = {"CONTAINER": 1.0, "REEFER": 0.45}  # CALIBRATE: tuned to ~5.5 sub trips/week (avg of 8 samples)
BULK_SHARE = 0.5                  # CALIBRATE: share of truckloads that are bulk bookings
CARTON_CBM = {"small": 0.06, "large": 0.12}  # CALIBRATE: 50x40x30 cm and 60x50x40 cm cartons
LARGE_CARTON_SHARE = 0.4                     # CALIBRATE: share of cartons that are the large size
RETAIL_CARTONS = (10, 60)                    # CALIBRATE: cartons per retail booking
BULK_FILL = (0.6, 1.0)            # CALIBRATE: bulk order weight as a share of payload
CUTOFF_HOUR = 15                  # CALIBRATE: orders after this roll to next day
BULK_MAX_WAIT_DAYS = 0            # CALIBRATE: bulk customers are time-sensitive
RETAIL_MAX_WAIT_DAYS = 1          # CALIBRATE: retail accepts 4-5 days total
NOISE_RATE = 0.12                 # CALIBRATE: share of planner decisions that deviate


# ---------------------------------------------------------------
# Quick self-test: run `python reference.py`
# ---------------------------------------------------------------
if __name__ == "__main__":
    print("SYNTHETIC DATA - reference tables\n")
    print(ROUTES.to_string(index=False), "\n")
    print(VEHICLE_TYPES.to_string(index=False), "\n")
    print(FLEET["vehicle_type"].value_counts().to_string(), "\n")
    print(SUBCONTRACTORS.to_string(index=False), "\n")

    # Sanity check: does the fleet size match ~5-6 sub calls per week?
    print("In-house capacity vs average demand (truckloads per day):")
    total_shortfall = 0
    for vt in ["CONTAINER", "REEFER"]:
        n_trucks = (FLEET["vehicle_type"] == vt).sum()
        capacity = n_trucks / ROUND_TRIP_DAYS
        demand = AVG_TRUCKLOADS_PER_DAY[vt]
        shortfall = max(demand - capacity, 0)
        total_shortfall += shortfall
        print(f"  {vt:<10} capacity {capacity:.2f}  demand {demand:.2f}  shortfall {shortfall:.2f}")
    print(f"Rough sub calls per week (ignores peaks and packing): {total_shortfall * 7:.1f}")