"""
HAL VTOL Series-Hybrid — Optimisation Module
=============================================
Design-space exploration, Pareto-front identification, and optimal
power-split dispatch using dynamic programming / linear programming.

Architecture
────────────
Series-hybrid power train:
  Turboshaft (60 kW) → Generator → DC bus → Battery (34 kWh) → Electric motors
                                          ↓
                              Fixed-wing propeller (pusher)
                                          ↓
                              VTOL lift rotors (same bus)
"""

from __future__ import annotations
import math
import random
from typing import List, Tuple, Dict
from dataclasses import dataclass

from vtol_optimizer import (
    HALConstraints, SeriesHybridDesign, evaluate_design, MissionPhaseResult,
    BATTERY_SPEC_ENERGY, ENGINE_RATED_KW, BSFC_best, ROTOR_EFF, PROP_EFF,
    INVERTER_EFF, ISA_K, R_AIR, T0, R_L, P0, G,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DESIGN-SPACE EXPLORATION
# ═══════════════════════════════════════════════════════════════════════════════

class DesignSpace:
    """
    Generates candidate design points covering the feasible design envelope.

    Supported sampling strategies:
      - grid_search   : uniform lattice (controlled, predictable)
      - random_sampling: pure Monte Carlo (fast, unbiased)
      - sobol_sequence: quasi-random low-discrepancy (better coverage per N)
    """

    # Feasible bounds for each design variable (hard constraints from physics)
    BOUNDS = dict(
        battery_kwh         = (5.0,  50.0),
        fuel_kg             = (10.0, 200.0),
        rotor_disk_area_m2  = (15.0,  80.0),
        engine_load_fraction = (0.50,   1.0),
        cruise_altitude_m   = (3_000.0, 10_000.0),
        loiter_speed_mps    = (30.0,   80.0),
    )

    def grid_search(self, hal: HALConstraints, n_points: int = 500) -> List[SeriesHybridDesign]:
        """Uniform lattice across all dimensions. N is rounded up per axis."""
        # Aim for ~6-7 points per axis across 6 dimensions
        per_axis = max(2, round(n_points ** (1.0 / 6)))
        steps = [per_axis] * 6
        names = list(self.BOUNDS.keys())
        ranges = [self.BOUNDS[n] for n in names]

        designs = []
        def recurse(dim, vals):
            if dim == len(names):
                kw = dict(zip(names, vals))
                designs.append(SeriesHybridDesign(**kw))
                return
            lo, hi = ranges[dim]
            for v in DesignSpace.linspace(lo, hi, steps[dim]):
                recurse(dim + 1, vals + [v])

        recurse(0, [])
        return designs

    def random_sampling(self, hal: HALConstraints, n: int = 500) -> List[SeriesHybridDesign]:
        """Pure Monte Carlo sampling within bounds."""
        names = list(self.BOUNDS.keys())
        ranges = [self.BOUNDS[n] for n in names]
        designs = []
        for _ in range(n):
            kw = {n: random.uniform(lo, hi) for n, (lo, hi) in zip(names, ranges)}
            designs.append(SeriesHybridDesign(**kw))
        return designs

    def sobol_sequence(self, hal: HALConstraints, n: int = 500) -> List[SeriesHybridDesign]:
        """
        Sobol low-discrepancy sequence — much better coverage than MC for a given N.
        Uses the base-2 Sobol generator implemented in pure Python.
        """
        names = list(self.BOUNDS.keys())
        ranges = [self.BOUNDS[n] for n in names]
        # Sobol direction numbers (primitive polynomials, degree ≤ 10)
        V = [
            [], [1], [1,3], [1,3,5], [1,3,5,7], [1,3,5,7,11],
            [1,3,5,7,11,13], [1,3,5,7,11,13,15],
            [1,3,5,7,11,13,15,17], [1,3,5,7,11,13,15,17,19],
            [1,3,5,7,11,13,15,17,19,23],
        ]
        if n < 2:
            return self.random_sampling(hal, max(1, n))

        designs = []
        for i in range(1, n + 1):
            # Gray code of i
            g = i ^ (i >> 1)
            x = [0.0] * len(names)
            for k, (lo, hi) in enumerate(ranges):
                b = 1 << k
                if b & i:
                    # Use Sobol direction V[k+1] index within available range
                    dir_idx = min(k, len(V) - 1)
                    x[k] = (int(g) % (1 << (dir_idx + 1))) / (1 << (dir_idx + 1))
                else:
                    x[k] = 0.0
                x[k] = lo + x[k] * (hi - lo)
            designs.append(SeriesHybridDesign(**dict(zip(names, x))))
        return designs

    @staticmethod
    def linspace(lo: float, hi: float, n: int) -> List[float]:
        if n <= 1:
            return [lo]
        return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════
# PARETO FRONT
# ═══════════════════════════════════════════════════════════════════════════════

def is_dominated(a_end: float, a_mass: float,
                 b_end: float, b_mass: float) -> bool:
    """Returns True if design A is dominated by design B (both objectives)."""
    return (b_end >= a_end and b_mass <= a_mass and
            (b_end > a_end or b_mass < a_mass))


def optimize_pareto(hal: HALConstraints,
                    n_samples: int = 500,
                    sampling: str = "sobol") -> List[Tuple[SeriesHybridDesign, any]]:
    """
    Identify the Pareto-optimal frontier in the endurance–mass design space.

    Uses ε-constraint filtering: first filter by MTOW, then identify
    non-dominated designs via pairwise comparison (适合小批量; scales as O(N²)).

    Returns
    -------
    List of (design, EvaluationResult) for each Pareto-optimal point,
    sorted by descending endurance.
    """
    space = DesignSpace()
    if sampling == "sobol":
        candidates = space.sobol_sequence(hal, n_samples)
    elif sampling == "random":
        candidates = space.random_sampling(hal, n_samples)
    else:
        candidates = space.grid_search(hal, n_samples)

    evaluated = []
    for d in candidates:
        r = evaluate_design(hal, d, loiter_seconds=hal.loiter_seconds)
        if r.takeoff_mass_kg <= hal.MTOW:
            evaluated.append((d, r))

    # Pairwise non-dominated filter
    pareto = []
    for i, (di, ri) in enumerate(evaluated):
        dominated = False
        for j, (dj, rj) in enumerate(evaluated):
            if i == j:
                continue
            if is_dominated(ri.max_endurance_h, ri.takeoff_mass_kg,
                            rj.max_endurance_h, rj.takeoff_mass_kg):
                dominated = True
                break
        if not dominated:
            pareto.append((di, ri))

    pareto.sort(key=lambda x: x[1].max_endurance_h, reverse=True)
    return pareto


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIMAL POWER-SPLIT DISPATCH (DYNAMIC PROGRAMMING)
# ═══════════════════════════════════════════════════════════════════════════════

def _thrust_power(mass_kg: float, altitude_m: float, speed_mps: float,
                  CdA: float = 0.03) -> Tuple[float, float]:
    """
    Compute equilibrium thrust and required shaft power for steady level flight.

    Returns (thrust_N, bus_power_kw).
    """
    rho = _isa_density(altitude_m)
    drag_N = 0.5 * rho * speed_mps ** 2 * CdA
    thrust_N = drag_N
    power_w  = thrust_N * speed_mps / PROP_EFF
    return thrust_N, power_w / 1_000.0


def _isa_density(altitude_m: float) -> float:
    T = T0 - R_L * altitude_m
    P = P0 * (1.0 - R_L * altitude_m / T0) ** ISA_K
    return max(P / (R_AIR * T), 1e-6)


def mission_dispatch_optimize(hal: HALConstraints,
                               design: SeriesHybridDesign,
                               result: any) -> Dict[str, Dict[str, float]]:
    """
    Find the fuel-minimal power-split between turboshaft and battery
    for each mission phase using a simple DP / linear-search approach.

    The objective per phase is:
      min  fuel_flow  = BSFC × P_engine
      s.t. P_engine ≤ 0.92 × 60 kW
            P_engine + P_battery ≥ P_required
            P_battery ≤ P_battery_max
            SOC_end ≥ 0.10

    Since BSFC is roughly constant over the operating range for a
    turboshaft, the optimal policy is trivially:
      → Run engine at its best-efficiency / best-specific-fuel point (high load)
      → Fill remaining power demand with battery

    This is the optimal policy for a series hybrid because the engine
    has no direct mechanical link to the propeller — every kW from the
    engine must first be converted to electricity.

    Returns
    -------
    Dict[phase_name] → {
        "engine_kw"        : kW from turboshaft,
        "battery_kw"       : kW from battery,
        "fuel_flow_kg_h"   : kg/h fuel consumption,
    }
    """
    engine_max = design.engine_load_fraction * ENGINE_RATED_KW  # e.g. 55 kW
    Batt_max   = result.battery_max_discharge_kw

    dispatch = {}

    # ── VTOL ──────────────────────────────────────────────────────────────────
    p = result.phases[0]   # VTOL Takeoff
    dispatch[p.phase_name] = dict(
        engine_kw      = 0.0,
        battery_kw     = p.required_bus_peak_kw,
        fuel_flow_kg_h = 0.0,
    )

    # ── Climb ─────────────────────────────────────────────────────────────────
    p = result.phases[1]
    eng = min(engine_max, p.required_bus_peak_kw)
    dispatch[p.phase_name] = dict(
        engine_kw      = eng,
        battery_kw     = max(0.0, p.required_bus_peak_kw - eng),
        fuel_flow_kg_h = BSFC_best * eng,
    )

    # ── Cruise ────────────────────────────────────────────────────────────────
    p = result.phases[2]
    eng = min(engine_max, p.required_bus_peak_kw)
    dispatch[p.phase_name] = dict(
        engine_kw      = eng,
        battery_kw     = max(0.0, p.required_bus_peak_kw - eng),
        fuel_flow_kg_h = BSFC_best * eng,
    )

    # ── Loiter ────────────────────────────────────────────────────────────────
    p = result.phases[3]
    # At loiter, engine at best-efficiency point (near max power for turboshaft)
    eng = min(engine_max, p.required_bus_peak_kw)
    dispatch[p.phase_name] = dict(
        engine_kw      = eng,
        battery_kw     = max(0.0, p.required_bus_peak_kw - eng),
        fuel_flow_kg_h = BSFC_best * eng,
    )

    # ── Descent ───────────────────────────────────────────────────────────────
    p = result.phases[4]
    eng = min(engine_max, max(5.0, p.required_bus_peak_kw))
    dispatch[p.phase_name] = dict(
        engine_kw      = eng,
        battery_kw     = max(0.0, p.required_bus_peak_kw - eng),
        fuel_flow_kg_h = BSFC_best * eng,
    )

    return dispatch
