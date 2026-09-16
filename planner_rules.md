# Planner Rules (written before any cost model was built)

These are the manual rules I used as transport planner at a family-owned 3PL
in Vietnam (Hanoi → HCMC corridor). They are written from memory BEFORE the
cost model was built, so the optimiser comparison is not circular.
All data in this project is synthetic, calibrated from my operating experience.

## Context
- Fleet: 10 in-house container trucks, 5 in-house reefer trucks, based in Hanoi.
- Round trip ~12 days (4 days south, 1–2 days loading in HCMC, return loaded).
- Two order types:
  - Bulk: one customer, full truck, multi-stop (Nghe An/Ha Tinh, Dak Lak, HCMC).
  - Retail: many customers consolidated, direct Hanoi → HCMC.
- Subcontracted long-haul carriers: A, B, C (paid a flat rate per trip).

## Rules
1. In-house first. Orders go on our own trucks if a suitable truck is available.
2. When trucks are short, bulk goes to subcontractors first. Bulk customers
   are time-sensitive; retail customers accept 4–5 days to HCMC and can wait
   for our next available truck.
3. Retail stays in-house wherever possible, even if that means waiting
   for a later truck.
4. Sub choice is by relationship, not price: call my preferred carrier first
   (A, then B, then C), and move down the list only if they have no truck.
5. Cut-off: orders arriving after the second daily departure (~3–4pm) roll
   to the next day.
6. Reefer cargo only goes on reefer trucks (ours or a sub's).
7. Retail trucks are loaded until the handler says they are full.
8. Never split one retail customer's order across trucks. If it doesn't
   fit, the whole order moves to the next truck (even next day).
9. Parcels that would overload a truck are split across trucks.

## Deviations (human noise, ~12% of decisions)
- Calling a carrier other than my first preference (availability,
  who answered, a favour owed).
- Pushing a retail order to the next truck when it technically fit.
- Keeping a bulk order in-house when a truck was free, even though retail
  was waiting.