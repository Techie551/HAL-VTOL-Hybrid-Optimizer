#!/usr/bin/env python3
"""
HAL VTOL Hybrid Optimizer — Main Entry Point
Demonstrates series-hybrid physics simulation + Pareto optimization + ML dispatch
for the HAL × IIT Indore Aerothon 2026.

Usage:
    python main.py                    # Run simulations and print results
    streamlit run dashboard.py        # Launch interactive dashboard
    python main.py --train-ml         # Generate training data and train ML dispatcher
"""

import sys
import json
import argparse
from pathlib import Path

# ── local imports ──────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from vtol_optimizer import (
    HALConstraints,
    SeriesHybridDesign,
    evaluate_design,
    isa_density_kg_m3,
    MissionPhaseResult,
    EvaluationResult,
)
from vtol_optimizer.optimizer import DesignSpace, optimize_pareto, mission_dispatch_optimize


# ── pretty printing ───────────────────────────────────────────────────────────
RED   = "\033[91m"
GREEN = "\033[92m"
YELLOW= "\033[93m"
BLUE  = "\033[94m"
BOLD  = "\033[1m"
RESET = "\033[0m"

def col(t, c): return f"{c}{t}{RESET}"
def pass_(t): return col(f"✓ {t}", GREEN)
def fail(t): return col(f"✗ {t}", RED)


# ── reference design from HAL brief ──────────────────────────────────────────
REFERENCE_DESIGN = dict(
    battery_kwh          = 34.0,
    fuel_kg              = 145.0,
    rotor_disk_area_m2   = 45.0,
    engine_load_fraction = 0.92,
    cruise_altitude_m    = 6_000.0,
    loiter_speed_mps     = 51.0,   # ~100 kt
)
LOITER_MINUTES = 30               # HAL minimum loiter requirement


def run_reference_simulation():
    print(f"\n{'='*65}")
    print(f"  {BOLD}HAL VTOL Series-Hybrid — Reference Design Simulation{RESET}")
    print(f"{'='*65}")

    constraints = HALConstraints()
    design     = SeriesHybridDesign(**REFERENCE_DESIGN)
    result     = evaluate_design(constraints, design,
                                  loiter_seconds=LOITER_MINUTES * 60)

    # ── Mass budget ──────────────────────────────────────────────────────────
    print(f"\n  {BOLD}── Mass Budget ──────────────────────────────────────{RESET}")
    print(f"  Payload                : {result.payload_kg:>8.1f}  kg  (fixed by HAL)")
    print(f"  Battery mass          : {result.battery_mass_kg:>8.1f}  kg  "
          f"({design.battery_kwh:.0f} kWh × 1/250 Wh/kg)")
    print(f"  Structure mass       : {result.structure_mass_kg:>8.1f}  kg")
    print(f"  Fuel system mass     : {result.fuel_mass_kg:>8.1f}  kg")
    print(f"  Systems / margins    : {60:>8.1f}  kg")
    print(f"  {'─'*40}")
    print(f"  {BOLD}Takeoff mass          : {result.takeoff_mass_kg:>8.1f}  kg{RESET}  "
          f"  [HAL MTOW = {constraints.MTOW} kg]")
    print(f"  {GREEN}" if result.takeoff_mass_kg <= constraints.MTOW
          else f"  {RED}", end="")

    # ── Phase results ────────────────────────────────────────────────────────
    print(f"\n  {BOLD}── Mission Phase Power & Energy ────────────────────────{RESET}")
    print(f"  {'Phase':<22} {'Duration':>8} {'Bus (kW)':>10} {'Batt (kW)':>10} "
          f"{'Fuel (kg)':>10} {'SOC %':>7}")
    print(f"  {'─'*66}")
    for p in result.phases:
        print(f"  {p.phase_name:<22} {p.duration_s:>7.0f}s "
              f"{p.required_bus_peak_kw:>10.1f} {p.battery_peak_kw:>10.1f} "
              f"{p.fuel_consumed_kg:>10.2f} {p.soc_final_pct:>6.1f}")

    # ── Key KPIs ─────────────────────────────────────────────────────────────
    total_fuel   = sum(p.fuel_consumed_kg   for p in result.phases)
    total_batt   = sum(p.battery_drawn_kwh  for p in result.phases)
    total_energy = sum(p.required_bus_peak_kw * p.duration_s / 3600
                       for p in result.phases)

    print(f"\n  {BOLD}── Key Performance Indicators ──────────────────────────{RESET}")
    print(f"  Mission completion    : {pass_('YES') if result.completed_all_required_phases else fail('NO')}")
    print(f"  Max endurance (est)   : {result.max_endurance_h:>8.2f}  h")
    print(f"  Total fuel burned     : {total_fuel:>8.2f}  kg")
    print(f"  Total battery used    : {total_batt:>8.2f}  kWh")
    print(f"  Total energy demand   : {total_energy:>8.2f}  kWh")
    print(f"  Fuel mass fraction    : {total_fuel/result.takeoff_mass_kg*100:>8.2f}  %")
    print(f"  Battery mass fraction : {result.battery_mass_kg/result.takeoff_mass_kg*100:>8.2f}  %")

    return result


def run_pareto_analysis():
    print(f"\n{'='*65}")
    print(f"  {BOLD}Pareto Front — Battery vs Fuel vs Endurance{RESET}")
    print(f"{'='*65}")

    constraints = HALConstraints()
    space      = DesignSpace()
    candidates = space.random_sampling(constraints, n=300)

    feasible, infeasible = [], []
    for d in candidates:
        r = evaluate_design(constraints, d, loiter_seconds=0)
        if r.completed_all_required_phases:
            feasible.append((d, r))
        else:
            infeasible.append((d, r))

    # Simple Pareto: maximise endurance, minimise mass
    pareto = []
    for d, r in feasible:
        dominated = False
        for dd, rr in feasible:
            if (rr.max_endurance_h >= r.max_endurance_h and
                rr.takeoff_mass_kg <= r.takeoff_mass_kg and
                (rr.max_endurance_h > r.max_endurance_h or
                 rr.takeoff_mass_kg < r.takeoff_mass_kg)):
                dominated = True
                break
        if not dominated:
            pareto.append((d, r))

    pareto.sort(key=lambda x: x[1].max_endurance_h, reverse=True)

    print(f"\n  Found {len(feasible)} feasible / {len(infeasible)} infeasible designs")
    print(f"  Pareto-optimal designs: {len(pareto)}\n")
    print(f"  {'Battery':>8} {'Fuel':>6} {'Rotor':>7} {'Mass':>7} {'Endurance':>10} {'Feasible'}")
    print(f"  {'kWh':>8} {'kg':>6} {'m²':>7} {'kg':>7} {'h':>10}")
    print(f"  {'─'*52}")
    for d, r in pareto[:15]:
        print(f"  {d.battery_kwh:>8.1f} {d.fuel_kg:>6.1f} "
              f"{d.rotor_disk_area_m2:>7.1f} {r.takeoff_mass_kg:>7.1f} "
              f"{r.max_endurance_h:>10.2f}")

    return pareto


def run_optimal_dispatch():
    print(f"\n{'='*65}")
    print(f"  {BOLD}Optimal Power-Split Dispatch (Dynamic Programming){RESET}")
    print(f"{'='*65}")

    constraints = HALConstraints()
    design     = SeriesHybridDesign(**REFERENCE_DESIGN)
    result     = evaluate_design(constraints, design,
                                  loiter_seconds=LOITER_MINUTES * 60)
    dispatch   = mission_dispatch_optimize(constraints, design, result)

    print(f"\n  {'Phase':<22} {'Engine kW':>10} {'Battery kW':>11} {'Fuel kg/h':>10}")
    print(f"  {'─'*56}")
    for phase, d in dispatch.items():
        print(f"  {phase:<22} {d['engine_kw']:>10.2f} {d['battery_kw']:>11.2f} "
              f"{d['fuel_flow_kg_h']:>10.3f}")

    return dispatch


def main():
    parser = argparse.ArgumentParser(description="HAL VTOL Hybrid Optimizer")
    parser.add_argument("--train-ml", action="store_true",
                        help="Generate ML training data")
    args = parser.parse_args()

    run_reference_simulation()
    run_pareto_analysis()
    run_optimal_dispatch()

    print(f"\n{'='*65}")
    print(f"  Run {BOLD}streamlit run dashboard.py{RESET} for the interactive UI")
    print(f"{'='*65}\n")

    if args.train_ml:
        try:
            from vtol_optimizer.ml_dispatcher import generate_training_data
            print("\n[ML] Generating training data (this may take a minute)…")
            csv_path = generate_training_data(HALConstraints(), n_samples=500)
            print(f"[ML] Training data saved to {csv_path}")
        except Exception as e:
            print(f"[ML] Skipping: {e}")


if __name__ == "__main__":
    main()
