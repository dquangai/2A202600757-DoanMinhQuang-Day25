"""Extension 5 — Carbon-aware Scheduling (deck §11).

For each interruptible job in workloads.csv, compare:
  - Carbon at the default region (us-east-1)
  - Carbon at the cleanest region (europe-north1)
  - Cost at each region (electricity cost)

Report: gCO2e saved and % reduction if all interruptible jobs migrate.

Run: python missions/m_ext5_carbon_scheduling.py
"""
from __future__ import annotations
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from missions._common import load_csv, num, catalog_by_type, ROOT
from finops import sustainability

# Assume each GPU burns power at its rated TDP (watts) for the whole job.
# Power consumption -> kWh -> carbon + electricity cost.

DEFAULT_REGION = "us-east-1"
CLEANEST_REGION = "europe-north1"
DAYS = 30


def run(verbose: bool = True) -> dict:
    jobs = load_csv("workloads.csv")
    cat = catalog_by_type()

    # Build comparison table for all regions
    regions = list(sustainability.REGION_CARBON.keys())

    rows = []
    total_carbon_default_g = 0.0
    total_carbon_clean_g = 0.0
    total_cost_default = 0.0
    total_cost_clean = 0.0

    for j in jobs:
        interruptible = bool(int(num(j["interruptible"])))
        if not interruptible:
            continue

        gtype = j["gpu_type"]
        ngpu = int(num(j["num_gpus"]))
        hpd = num(j["hours_per_day"])
        days = int(num(j.get("days", DAYS)))

        # Watts from catalog
        watts = num(cat[gtype]["watts"]) * ngpu
        total_hours = hpd * days
        total_wh = watts * total_hours        # Wh
        total_kwh = total_wh / 1000.0

        carbon_default = sustainability.carbon_g(total_wh, DEFAULT_REGION)     # gCO2
        carbon_clean = sustainability.carbon_g(total_wh, CLEANEST_REGION)      # gCO2
        cost_default = sustainability.energy_cost_usd(total_wh, DEFAULT_REGION)
        cost_clean = sustainability.energy_cost_usd(total_wh, CLEANEST_REGION)

        carbon_saved = carbon_default - carbon_clean
        carbon_pct = (carbon_saved / carbon_default * 100) if carbon_default > 0 else 0
        cost_delta = cost_default - cost_clean

        total_carbon_default_g += carbon_default
        total_carbon_clean_g += carbon_clean
        total_cost_default += cost_default
        total_cost_clean += cost_clean

        rows.append({
            "job_id": j["job_id"],
            "gpu_type": gtype,
            "num_gpus": ngpu,
            "hours_total": total_hours,
            "kwh": round(total_kwh, 1),
            "carbon_default_kg": round(carbon_default / 1000, 2),
            "carbon_clean_kg": round(carbon_clean / 1000, 2),
            "carbon_saved_kg": round(carbon_saved / 1000, 2),
            "carbon_pct": round(carbon_pct, 1),
            "cost_default_usd": round(cost_default, 2),
            "cost_clean_usd": round(cost_clean, 2),
            "cost_delta_usd": round(cost_delta, 2),
        })

    # All-region comparison table
    all_region_rows = []
    # Use total kWh of interruptible jobs for the comparison
    total_kwh_all = sum(r["kwh"] for r in rows)
    total_wh_all = total_kwh_all * 1000
    for reg in regions:
        carbon = sustainability.carbon_g(total_wh_all, reg)
        elec_cost = sustainability.energy_cost_usd(total_wh_all, reg)
        ci = sustainability.REGION_CARBON[reg]
        price_kwh = sustainability.REGION_PRICE_KWH[reg]
        all_region_rows.append({
            "region": reg,
            "gco2_per_kwh": ci,
            "usd_per_kwh": price_kwh,
            "total_carbon_kg": round(carbon / 1000, 2),
            "total_elec_cost_usd": round(elec_cost, 2),
        })
    all_region_rows.sort(key=lambda x: x["gco2_per_kwh"])

    total_saved_kg = (total_carbon_default_g - total_carbon_clean_g) / 1000
    total_pct = ((total_carbon_default_g - total_carbon_clean_g) / total_carbon_default_g * 100
                 if total_carbon_default_g > 0 else 0)

    if verbose:
        print("== Extension 5: Carbon-aware Scheduling ==")
        print(f"\nInterruptible jobs analysed: {len(rows)}")
        print(f"\n{'Job':18}{'GPU':7}{'kWh':>8}{'CO2@default':>14}{'CO2@clean':>11}{'Saved':>10}{'%red':>7}{'$saved':>9}")
        print("-" * 90)
        for r in rows:
            print(f"{r['job_id']:18}{r['gpu_type']:7}{r['kwh']:>8.1f}"
                  f"{r['carbon_default_kg']:>13.2f}kg{r['carbon_clean_kg']:>10.2f}kg"
                  f"{r['carbon_saved_kg']:>9.2f}kg{r['carbon_pct']:>6.1f}%"
                  f"  ${r['cost_delta_usd']:>7.2f}")
        print("-" * 90)
        print(f"{'TOTAL':25}{'':>8}"
              f"{total_carbon_default_g/1000:>13.2f}kg{total_carbon_clean_g/1000:>10.2f}kg"
              f"{total_saved_kg:>9.2f}kg{total_pct:>6.1f}%"
              f"  ${total_cost_default - total_cost_clean:>7.2f}")

        print(f"\n== All-region comparison (for {total_kwh_all:.0f} kWh of interruptible work) ==")
        print(f"{'Region':20}{'gCO2/kWh':>12}{'$/kWh':>8}{'Total CO2 (kg)':>16}{'Elec cost ($)':>15}")
        print("-" * 75)
        for r in all_region_rows:
            note = " <- cleanest" if r["region"] == CLEANEST_REGION else ""
            print(f"{r['region']:20}{r['gco2_per_kwh']:>12}{r['usd_per_kwh']:>8.3f}"
                  f"{r['total_carbon_kg']:>16.2f}{r['total_elec_cost_usd']:>15.2f}{note}")

        print(f"\n-> Migrating interruptible jobs to {CLEANEST_REGION} saves {total_saved_kg:.2f} kg CO2 ({total_pct:.1f}% reduction)")
        print(f"-> Also saves ${total_cost_default - total_cost_clean:.2f} in electricity vs {DEFAULT_REGION}")
        print(f"\nTrade-off note: {CLEANEST_REGION} (Norway) is geographically far from US-based users.")
        print("   Latency impact: ~130ms additional RTT. Best for batch/training; avoid for real-time inference.")

    return {
        "jobs": rows,
        "total_carbon_saved_kg": round(total_saved_kg, 2),
        "total_carbon_pct": round(total_pct, 1),
        "all_regions": all_region_rows,
    }


if __name__ == "__main__":
    run()
