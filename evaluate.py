"""
evaluate.py
Compare the optimiser with the planner heuristic (my manual decisions)
and the naive "cheapest carrier" baseline, then save tables and charts.

ALL DATA IN THIS PROJECT IS SYNTHETIC.

What it reports:
  1. Match rate: how often the optimiser makes the planner's decision
       - strategic match: same in-house vs subcontract decision
       - exact match:     same carrier AND same dispatch day
     overall and by segment (booking type x vehicle type, and season)
  2. Spend by method, as an index (planner = 100) and as % of TOTAL spend
  3. Costing sensitivity: optimiser deciding on marginal vs fully-absorbed
     in-house cost, each evaluated on both bases
  4. Deviation sensitivity: how the saving changes with the planner's
     assumed deviation rate (0%, 6%, 12%, 18%)

Outputs go to results/ (charts as .png, tables as .csv).
"""

import os
import matplotlib
matplotlib.use("Agg")   # save charts to files without opening windows
import matplotlib.pyplot as plt
import pandas as pd
import reference as ref
from cost_matrix import IN_HOUSE, COSTING_MODES
from heuristics import planner_heuristic, naive_cheapest
from model import run_optimiser, freight_cost

ORDERS_PATH = os.path.join("data", "orders.csv")
RESULTS_DIR = "results"
NOISE_LEVELS = [0.0, 0.06, 0.12, 0.18]   # planner deviation rates to test
NOISE_SEEDS = [42, 1, 2, 3, 4]           # average several random runs per level


# ---------------------------------------------------------------
# 1. Match rate
# ---------------------------------------------------------------
def decision_type(carrier):
    """Collapse a carrier into the strategic decision: own truck or subcontract."""
    if carrier == IN_HOUSE:
        return "IN_HOUSE"
    if carrier == "UNSERVED":
        return "UNSERVED"
    return "SUB"


def compare_plans(planner_plan, model_plan):
    """One row per order with both decisions side by side."""
    left = planner_plan[["order_id", "booking_type", "vehicle_type", "season",
                         "carrier", "dispatch_day"]]
    right = model_plan[["order_id", "carrier", "dispatch_day"]]
    both = left.merge(right, on="order_id", suffixes=("_planner", "_model"))

    both["strategic_match"] = (both["carrier_planner"].map(decision_type)
                               == both["carrier_model"].map(decision_type))
    both["exact_match"] = ((both["carrier_planner"] == both["carrier_model"])
                           & (both["dispatch_day_planner"] == both["dispatch_day_model"]))
    return both


def match_summary(both, by):
    """Match rates (%) and order counts, grouped by the given column(s)."""
    table = both.groupby(by).agg(
        orders=("order_id", "count"),
        strategic_match_pct=("strategic_match", "mean"),
        exact_match_pct=("exact_match", "mean"),
    )
    table[["strategic_match_pct", "exact_match_pct"]] *= 100
    return table.round(1).reset_index()


# ---------------------------------------------------------------
# 2. Spend by method
# ---------------------------------------------------------------
def spend_table(plans, costing="marginal"):
    """Spend of each plan. Index: planner = 100 (no real VND levels published)."""
    rows = []
    for name, plan in plans.items():
        cost = freight_cost(plan, costing)
        rows.append({
            "method": name,
            "trips": plan["trip_id"].nunique(),
            "sub_trips": plan.loc[plan["carrier"].map(decision_type) == "SUB", "trip_id"].nunique(),
            "late_orders": int(plan["late"].sum()),
            "spend_vnd": cost["total"],
        })
    table = pd.DataFrame(rows)
    base = table.loc[0, "spend_vnd"]   # first plan in the dict is the reference
    table["spend_index"] = (table["spend_vnd"] / base * 100).round(1)
    table["saving_pct_of_total"] = ((base - table["spend_vnd"]) / base * 100).round(1)
    return table


# ---------------------------------------------------------------
# 3. Costing sensitivity (marginal vs fully-absorbed)
# ---------------------------------------------------------------
def costing_sensitivity(orders, planner_plan):
    """Optimiser decides on each basis; every plan is then costed on both bases."""
    rows = []
    for decide_on in COSTING_MODES:
        plan = run_optimiser(orders, costing=decide_on)
        row = {"optimiser_decides_on": decide_on,
               "in_house_trips": plan.loc[plan["carrier"] == IN_HOUSE, "trip_id"].nunique(),
               "sub_trips": plan.loc[plan["carrier"].map(decision_type) == "SUB", "trip_id"].nunique()}
        for evaluate_on in COSTING_MODES:
            planner_cost = freight_cost(planner_plan, evaluate_on)["total"]
            plan_cost = freight_cost(plan, evaluate_on)["total"]
            row[f"saving_pct_on_{evaluate_on}"] = round((planner_cost - plan_cost) / planner_cost * 100, 1)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------
# 4. Deviation sensitivity
# ---------------------------------------------------------------
def noise_sensitivity(orders, model_plan):
    """Saving vs planner (% of total spend) at different deviation rates."""
    model_cost = freight_cost(model_plan, "marginal")["total"]
    rows = []
    for level in NOISE_LEVELS:
        savings = []
        for seed in NOISE_SEEDS:
            plan = planner_heuristic(orders, noise_rate=level, seed=seed)
            planner_cost = freight_cost(plan, "marginal")["total"]
            savings.append((planner_cost - model_cost) / planner_cost * 100)
        rows.append({
            "deviation_rate_pct": round(level * 100),
            "avg_saving_pct": round(sum(savings) / len(savings), 1),
            "min_saving_pct": round(min(savings), 1),
            "max_saving_pct": round(max(savings), 1),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------
# Charts
# ---------------------------------------------------------------
def chart_spend(spend):
    """Saving of each method vs the planner. Bars start at zero, so gaps are not exaggerated."""
    others = spend.iloc[1:]   # skip the planner itself (saving = 0 by definition)
    fig, ax = plt.subplots(figsize=(8, 4))
    bars = ax.barh(others["method"], others["saving_pct_of_total"], color="#4C72B0")
    ax.bar_label(bars, fmt="%.1f%%", padding=3)
    ax.set_xlim(0, max(others["saving_pct_of_total"]) * 1.2)
    ax.invert_yaxis()
    ax.set_xlabel("Saving vs planner (% of total freight spend)")
    ax.set_title("What each method saves vs my manual plan - SYNTHETIC DATA")
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS_DIR, "chart1_saving_by_method.png"), dpi=150)
    plt.close(fig)


def chart_match(by_segment):
    # Show the sample size under each label: small segments are less reliable
    labels = (by_segment["booking_type"] + " / " + by_segment["vehicle_type"]
              + "\n(n=" + by_segment["orders"].astype(str) + ")")
    positions = range(len(labels))
    width = 0.4
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([p - width / 2 for p in positions], by_segment["strategic_match_pct"],
           width, label="Strategic (own truck vs sub)", color="#4C72B0")
    ax.bar([p + width / 2 for p in positions], by_segment["exact_match_pct"],
           width, label="Exact (same carrier, same day)", color="#DD8452")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of orders where optimiser = planner")
    ax.set_title("Match rate by segment - SYNTHETIC DATA")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS_DIR, "chart2_match_by_segment.png"), dpi=150)
    plt.close(fig)


def chart_noise(noise):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(noise["deviation_rate_pct"], noise["avg_saving_pct"], marker="o", color="#4C72B0")
    ax.fill_between(noise["deviation_rate_pct"], noise["min_saving_pct"],
                    noise["max_saving_pct"], alpha=0.2, color="#4C72B0",
                    label=f"Range over {len(NOISE_SEEDS)} random runs")
    ax.axhline(0, color="grey", linewidth=1)
    ax.axvline(ref.NOISE_RATE * 100, color="grey", linestyle="--", linewidth=1,
               label="Assumed deviation rate")
    ax.set_xlabel("Planner deviation rate (%)")
    ax.set_ylabel("Optimiser saving (% of total spend)")
    ax.set_title("Saving depends on how often the planner deviates - SYNTHETIC DATA")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS_DIR, "chart3_saving_vs_deviation.png"), dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------
# Run: `python evaluate.py`
# ---------------------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists(ORDERS_PATH):
        raise SystemExit("data/orders.csv not found - run `python generate_data.py` first.")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    pd.set_option("display.width", 140)

    orders = pd.read_csv(ORDERS_PATH)
    print(f"SYNTHETIC DATA - evaluation on {len(orders)} orders\n")

    print("Running methods...")
    planner = planner_heuristic(orders)
    plans = {
        "Planner (my rules + deviations)": planner,
        "Planner rules, no deviations": planner_heuristic(orders, noise_rate=0.0),
        "Naive cheapest carrier": naive_cheapest(orders, "marginal"),
        "Optimiser (MIP)": run_optimiser(orders, "marginal"),
    }
    model = plans["Optimiser (MIP)"]

    # 1. Match rate
    both = compare_plans(planner, model)
    both["all"] = "All orders"
    overall = match_summary(both, "all")
    by_segment = match_summary(both, ["booking_type", "vehicle_type"])
    by_season = match_summary(both, "season")
    print("\n1. MATCH RATE (optimiser vs planner)")
    print(overall.to_string(index=False))
    print(by_segment.to_string(index=False))
    print(by_season.to_string(index=False))

    # 2. Spend
    spend = spend_table(plans)
    print("\n2. SPEND (marginal = cash basis; index planner = 100)")
    print(spend.drop(columns="spend_vnd").to_string(index=False))

    # 3. Costing sensitivity
    costing = costing_sensitivity(orders, planner)
    print("\n3. COSTING SENSITIVITY (optimiser saving vs planner, % of total spend)")
    print(costing.to_string(index=False))

    # 4. Deviation sensitivity
    noise = noise_sensitivity(orders, model)
    print("\n4. DEVIATION SENSITIVITY (optimiser saving vs planner, % of total spend)")
    print(noise.to_string(index=False))

    # Save everything
    by_segment.to_csv(os.path.join(RESULTS_DIR, "match_by_segment.csv"), index=False)
    by_season.to_csv(os.path.join(RESULTS_DIR, "match_by_season.csv"), index=False)
    spend.drop(columns="spend_vnd").to_csv(os.path.join(RESULTS_DIR, "spend_by_method.csv"), index=False)
    costing.to_csv(os.path.join(RESULTS_DIR, "costing_sensitivity.csv"), index=False)
    noise.to_csv(os.path.join(RESULTS_DIR, "deviation_sensitivity.csv"), index=False)
    chart_spend(spend)
    chart_match(by_segment)
    chart_noise(noise)

    # Headline
    x = overall.loc[0, "strategic_match_pct"]
    y = spend.loc[spend["method"] == "Optimiser (MIP)", "saving_pct_of_total"].iloc[0]
    naive_y = spend.loc[spend["method"] == "Naive cheapest carrier", "saving_pct_of_total"].iloc[0]
    print("\nHEADLINE (synthetic data):")
    print(f"  The optimiser reproduces the planner's own-truck vs subcontract decision "
          f"{x:.0f}% of the time, and identifies {y:.1f}% of total freight spend as "
          f"theoretically addressable where it disagrees.")
    print(f"  The naive cheapest-carrier rule captures {naive_y:.1f}% on its own.")
    print(f"\nCharts and tables saved to {RESULTS_DIR}/")