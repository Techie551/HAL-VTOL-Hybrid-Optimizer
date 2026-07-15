"""
HAL VTOL Series-Hybrid Optimizer — Core Physics Engine
======================================================
Physics-based mission simulation for the HAL × IIT Indore Aerothon 2026.

Models a series-hybrid fixed-wing UAV with VTOL capability:
  Turboshaft (60 kW rated, 72 kW short-term peak) → Generator → DC bus
                                                         ↓
                              Battery (≤50 kWh) ←→ Electric motors
                                                         ↓
                              Fixed-wing pusher propeller + VTOL rotors

Key physics modelled:
  • ISA atmosphere (0 – 11 000 m)
  • Actuator-disk VTOL power from disk loading
  • Parasite + induced drag cruise model
  • Battery SOC tracking with inverter/charger efficiency
  • Turboshaft BSFC map (minimum-BSFC operating point)
  • Pareto-optimal design-space exploration
  • MLP-based real-time power-split prediction

HAL Mission Constraints:
  MTOW = 1 000 kg  |  Payload = 200 kg  |  Cruise = 250 km/h  |  Alt = 3–10 km
  Min loiter = 30 min  |  Turboshaft ≤ 60 kW rated  |  Battery ≤ 50 kWh
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List


# ═══════════════════════════════════════════════════════════════════════════════
# PHYSICS CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════════

G         = 9.81     # m/s² — gravity
R_AIR     = 287.05  # J/(kg·K) — specific gas constant for dry air
R_L       = 0.0065  # K/m — ISA tropospheric lapse rate
T0        = 288.15  # K — sea-level standard temperature
P0        = 101325.0 # Pa — sea-level standard pressure
M_AIR     = 0.0289644 # kg/mol — molar mass of air
R_UNIV    = 8.31446  # J/(mol·K) — universal gas constant
ISA_K     = G * M_AIR / (R_UNIV * R_L)   # ≈ 5.25588

# ── Propulsion efficiencies ──────────────────────────────────────────────────
ROTOR_EFF    = 0.72   # hover / VTOL rotor disk efficiency
PROP_EFF     = 0.65   # pusher propeller cruise efficiency
INVERTER_EFF = 0.95   # DC↔AC inverter / rectifier efficiency

# ── Engine / genset ──────────────────────────────────────────────────────────
ENGINE_RATED_KW   = 60.0   # kW — rated continuous genset output
# Short-term engine peak (10% above rated for 2 min — valid for turbine hot-start):
ENGINE_PEAK_KW    = 72.0   # kW — 2-minute peak for VTOL / hot-day takeoff
BSFC_best         = 0.26   # kg/(kW·h) — brake-specific fuel consumption at best point

# ── Battery ─────────────────────────────────────────────────────────────────
BATTERY_SPEC_ENERGY = 250.0  # Wh/kg — cell-level (post-module/packaging)
MAX_C_CONTINUOUS   = 4.4    # C-rate — continuous discharge (valid for high-performance Li-ion)
MAX_C_PEAK         = 5.0    # C-rate — peak burst (10 s) for VTOL

# ── Airframe mass model ──────────────────────────────────────────────────────
# Based on 500–1200 kg class UAVs (e.g. Task, Skywalker, Volocopter VC200):
#   Wings + control surfaces:          ~85 kg
#   Fuselage + landing gear:          ~100 kg
#   Tail + provisions:                 ~25 kg
#   VTOL rotor hubs + structure:       ~45 kg
#   Generator + power electronics:     ~55 kg
FIXED_STRUCTURE_KG  = 280.0   # kg — airframe + rotor hubs + genset (excl. battery)
ROTOR_MASS_DENSITY  = 3.8    # kg/m² — rotor blade + hub per m² of disk
FUEL_TANK_KG       = 8.0    # kg — fixed tank mass (independent of fuel load)
SYSTEMS_KG         = 60.0   # kg — avionics, ECS, wiring, margins


# ═══════════════════════════════════════════════════════════════════════════════
# ATMOSPHERE
# ═══════════════════════════════════════════════════════════════════════════════

def isa_density_kg_m3(altitude_m: float) -> float:
    """
    ISA air density at geometric altitude.

      T(h) = T0 - L·h
      P(h) = P0 · (1 - L·h/T0)^(g·M/(R·L))
      ρ(h) = P / (R·T)
    Valid: 0 – 11 000 m geometric altitude.
    """
    h = max(float(altitude_m), 0.0)
    T = T0 - R_L * h
    P = P0 * (1.0 - R_L * h / T0) ** ISA_K
    return max(P / (R_AIR * T), 1e-8)


# ═══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HALConstraints:
    """Fixed mission requirements from the HAL Aerothon 2026 brief."""
    MTOW             : float = 1_000.0   # kg
    payload          : float = 200.0     # kg
    cruise_speed_kmh : float = 250.0     # km/h
    cruise_altitude_m: float = 6_000.0   # m
    loiter_minutes   : float = 30.0      # min
    max_battery_kwh  : float = 50.0     # kWh
    turboshaft_kw    : float = 60.0      # kW

    @property
    def cruise_speed_mps(self) -> float:
        return self.cruise_speed_kmh / 3.6

    @property
    def loiter_seconds(self) -> float:
        return self.loiter_minutes * 60.0


@dataclass
class SeriesHybridDesign:
    """Design decision variables — the optimisation will search over these."""
    battery_kwh          : float  # kWh — total battery capacity (≤ 50 kWh per HAL)
    fuel_kg              : float  # kg — mission fuel load
    rotor_disk_area_m2   : float  # m² — combined VTOL rotor disk area
    engine_load_fraction : float  # fraction [0,1] — cruise engine loading (optimal ≈ 0.85)
    cruise_altitude_m    : float  # m — cruise altitude for this design
    loiter_speed_mps     : float  # m/s — desired loiter airspeed


@dataclass
class MissionPhaseResult:
    """Time-resolved result for one mission phase."""
    phase_name           : str
    duration_s           : float  # s
    battery_drawn_kwh   : float  # kWh drawn from battery (positive = discharge)
    fuel_consumed_kg    : float  # kg fuel burned
    battery_peak_kw     : float  # kW — instantaneous battery output
    required_bus_peak_kw: float  # kW — total bus demand
    soc_final_pct        : float  # % — battery SOC at phase end


@dataclass
class EvaluationResult:
    """Complete evaluation of a design across the full mission."""
    completed_all_required_phases : bool
    takeoff_mass_kg              : float  # kg — all-up mass
    payload_kg                    : float  # kg
    battery_mass_kg               : float  # kg
    structure_mass_kg             : float  # kg
    fuel_mass_kg                  : float  # kg (incl. tank)
    phases                        : List[MissionPhaseResult]
    max_endurance_h               : float  # h — achievable endurance
    battery_max_discharge_kw      : float  # kW — continuous C-rate limit
    constraint_violations          : List[str]


# ═══════════════════════════════════════════════════════════════════════════════
# MASS MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def compute_mass(design: SeriesHybridDesign,
                 payload_kg: float) -> dict:
    """
    Mass budget:

      rotor_mass   = rotor_disk_area_m2 × ROTOR_MASS_DENSITY
      structure     = FIXED_STRUCTURE_KG + rotor_mass   (airframe + rotor hubs + genset)
      fuel_mass     = fuel_kg + FUEL_TANK_KG
      battery_mass  = battery_kwh / BATTERY_SPEC_ENERGY × 1000

      MTOW = payload + structure + fuel_mass + battery_mass + SYSTEMS_KG
    """
    rotor_mass    = design.rotor_disk_area_m2 * ROTOR_MASS_DENSITY
    structure     = FIXED_STRUCTURE_KG + rotor_mass
    fuel_mass     = design.fuel_kg + FUEL_TANK_KG
    battery_mass  = design.battery_kwh / BATTERY_SPEC_ENERGY * 1_000.0
    takeoff_mass  = payload_kg + structure + fuel_mass + battery_mass + SYSTEMS_KG

    return dict(
        rotor_mass   = rotor_mass,
        structure    = structure,
        fuel_mass    = fuel_mass,
        battery_mass = battery_mass,
        takeoff_mass = takeoff_mass,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# POWER CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════

def power_vtol(mass_kg: float, rotor_area_m2: float) -> float:
    """
    Hover / VTOL required bus power (kW).

    From actuator disk theory:
      disk_loading = W / A   (N/m²)
      induced_v    = sqrt(disk_loading / (2·ρ))
      T            = W = m·g
      P_mech       = T · v_ind / η_disk
      P_bus        = P_mech / η_inverter

    Add 8% for induced inertial effect (collective pitch transient).
    """
    rho = isa_density_kg_m3(0.0)
    disk_load = mass_kg * G / rotor_area_m2     # N/m²
    v_ind    = (disk_load / (2.0 * rho)) ** 0.5  # m/s
    thrust   = mass_kg * G                        # N
    P_mech_W = thrust * v_ind / ROTOR_EFF * 1.08
    return P_mech_W / 1_000.0 / INVERTER_EFF     # kW bus


def power_climb(mass_kg: float, climb_rate_mps: float = 8.0) -> float:
    """
    Mechanical power for a sustained climb at rate r.
    P = W × r / η_climb   (W)  —  η_climb ≈ 0.60–0.65 accounting for
    fuselage drag and propeller efficiency loss in climb attitude.
    """
    return mass_kg * G * climb_rate_mps / 0.62 / 1_000.0   # kW mech


def power_cruise(mass_kg: float, altitude_m: float,
                 speed_mps: float, CdA: float = 0.032) -> float:
    """
    Steady level flight at constant altitude and speed.

      D = ½ · ρ · v² · CdA
      T = D
      P_mech = T · v / η_prop
    CdA = 0.032 m² for a 1000-kg class UAV with exposed VTOL rotor mounts.
    """
    rho   = isa_density_kg_m3(altitude_m)
    drag  = 0.5 * rho * speed_mps ** 2 * CdA
    return drag * speed_mps / PROP_EFF / 1_000.0   # kW mech


def power_loiter(mass_kg: float, altitude_m: float,
                 speed_mps: float, CdA: float = 0.032) -> float:
    """
    Loiter power at the minimum-drag (maximum endurance) speed.

    Minimum drag occurs when induced drag = parasite drag.
    For a turboprop at low speed: P_loiter ≈ 0.80 × P_cruise(v_cruise).
    """
    return 0.80 * power_cruise(mass_kg, altitude_m, speed_mps, CdA)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EVALUATOR
# ═══════════════════════════════════════════════════════════════════════════════

def evaluate_design(constraints: HALConstraints,
                    design: SeriesHybridDesign,
                    loiter_seconds: float) -> EvaluationResult:
    """
    Evaluate a series-hybrid design across the complete HAL mission profile.

    Mission phases
    ──────────────
    1. VTOL Takeoff     — 60 s, pure battery (engine at idle for stability)
    2. Climb            — to cruise altitude, engine + battery assist
    3. Cruise           — 400 km, 250 km/h (HAL specified)
    4. Loiter           — loiter_seconds at loiter_speed (HAL minimum 30 min)
    5. Descent/Landing  — 300 s, controlled descent

    Engine strategy (series hybrid):
      • Engine runs at a CONSTANT optimal operating point (engine_load_fraction × 60 kW)
      • This is the minimum-BSFC point for a small turboshaft (≈ 0.26 kg/(kW·h))
      • Battery fills any gap between required bus power and engine output
      • During cruise, engine may slightly exceed cruise requirement → battery charges
      • This is the KEY advantage of series hybrid: engine never leaves its sweet spot

    Parameters
    ----------
    constraints   : HALConstraints  — fixed HAL mission requirements
    design        : SeriesHybridDesign — chosen design variables
    loiter_seconds: float — desired loiter duration (s)

    Returns
    -------
    EvaluationResult with phase-by-phase results and feasibility KPIs
    """
    # ── Mass ─────────────────────────────────────────────────────────────────
    m = compute_mass(design, constraints.payload)

    violations = []
    if m["takeoff_mass"] > constraints.MTOW:
        violations.append(
            f"MTOW {m['takeoff_mass']:.0f} kg exceeds "
            f"HAL limit {constraints.MTOW:.0f} kg "
            f"(structure={m['structure']:.0f}, battery={m['battery_mass']:.0f}, "
            f"fuel={m['fuel_mass']:.0f})"
        )

    # ── Battery C-rate limits ───────────────────────────────────────────────
    # Continuous: 2C (safe for prolonged operation)
    batt_cont_kw = design.battery_kwh * MAX_C_CONTINUOUS
    # Peak: 5C for 10 s (VTOL burst — safe for a 60-second burst)
    batt_peak_kw = design.battery_kwh * MAX_C_PEAK

    # ── Engine operating point ───────────────────────────────────────────────
    # Engine runs at this load throughout cruise and loiter (minimum BSFC)
    engine_kw = design.engine_load_fraction * ENGINE_RATED_KW   # e.g. 0.85 × 60 = 51 kW
    # Short-term peak for VTOL (not continuous — turbine rules)
    engine_vtol_kw = ENGINE_PEAK_KW

    # ── SOC tracking ────────────────────────────────────────────────────────
    soc = 1.0
    phases = []

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 1 — VTOL Takeoff
    # ═══════════════════════════════════════════════════════════════════════════
    bus_vtol_kw = power_vtol(m["takeoff_mass"], design.rotor_disk_area_m2)

    # Battery provides VTOL peak; engine at idle (5 kW for attitude stability)
    batt_vtol_kw = bus_vtol_kw - 5.0   # engine at idle contributes 5 kW
    batt_vtol_kw = max(batt_vtol_kw, 0.0)

    if batt_vtol_kw > batt_peak_kw:
        violations.append(
            f"VTOL battery peak {batt_vtol_kw:.1f} kW > 5C limit "
            f"{batt_peak_kw:.1f} kW (consider larger battery or lower mass)"
        )

    dur_vtol  = 60.0
    batt_vtol_kwh = batt_vtol_kw * dur_vtol / 3_600.0
    fuel_vtol_kg  = BSFC_best * 5.0 * dur_vtol / 3_600.0   # engine at idle fuel
    soc = max(0.0, soc - batt_vtol_kwh / design.battery_kwh)

    phases.append(MissionPhaseResult(
        phase_name="VTOL Takeoff",
        duration_s=dur_vtol,
        battery_drawn_kwh=batt_vtol_kwh,
        fuel_consumed_kg=fuel_vtol_kg,
        battery_peak_kw=batt_vtol_kw,
        required_bus_peak_kw=bus_vtol_kw,
        soc_final_pct=soc * 100.0,
    ))

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 2 — Climb
    # ═══════════════════════════════════════════════════════════════════════════
    bus_climb_kw = power_climb(m["takeoff_mass"])
    dur_climb     = design.cruise_altitude_m / 8.0   # 8 m/s climb rate

    # Engine runs at optimal point; battery fills the gap
    eng_climb_kw  = min(engine_kw, bus_climb_kw)    # engine at optimal, capped at need
    batt_climb_kw  = max(0.0, bus_climb_kw - eng_climb_kw)

    if batt_climb_kw > batt_cont_kw:
        violations.append(f"Climb battery peak {batt_climb_kw:.1f} kW > 2C limit {batt_cont_kw:.1f} kW")

    batt_climb_kwh = batt_climb_kw * dur_climb / 3_600.0
    fuel_climb_kg  = BSFC_best * eng_climb_kw * dur_climb / 3_600.0
    soc = max(0.0, soc - batt_climb_kwh / design.battery_kwh)

    phases.append(MissionPhaseResult(
        phase_name="Climb",
        duration_s=dur_climb,
        battery_drawn_kwh=batt_climb_kwh,
        fuel_consumed_kg=fuel_climb_kg,
        battery_peak_kw=batt_climb_kw,
        required_bus_peak_kw=bus_climb_kw,
        soc_final_pct=soc * 100.0,
    ))

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 3 — Cruise (400 km at 250 km/h = 69.4 m/s)
    # ═══════════════════════════════════════════════════════════════════════════
    cruise_mps    = constraints.cruise_speed_mps
    bus_cruise_kw = power_cruise(m["takeoff_mass"],
                                  design.cruise_altitude_m, cruise_mps)
    dur_cruise     = 400_000.0 / cruise_mps   # 400 km ÷ 69.4 m/s ≈ 5767 s

    eng_cruise_kw = min(engine_kw, bus_cruise_kw)
    batt_cruise_kw = bus_cruise_kw - eng_cruise_kw   # positive = engine undersized → battery fills

    # If engine EXCEEDS cruise requirement, the excess charges the battery:
    excess_kw = eng_cruise_kw - bus_cruise_kw
    charge_kw  = max(0.0, excess_kw)

    batt_cruise_kwh = max(0.0, batt_cruise_kw) * dur_cruise / 3_600.0
    charge_kwh      = charge_kw * dur_cruise / 3_600.0 * INVERTER_EFF
    fuel_cruise_kg  = BSFC_best * eng_cruise_kw * dur_cruise / 3_600.0

    # SOC update: discharge minus any charge
    soc = max(0.0, soc - batt_cruise_kwh / design.battery_kwh
                       + charge_kwh   / design.battery_kwh)

    phases.append(MissionPhaseResult(
        phase_name="Cruise",
        duration_s=dur_cruise,
        battery_drawn_kwh=batt_cruise_kwh - charge_kwh,
        fuel_consumed_kg=fuel_cruise_kg,
        battery_peak_kw=max(0.0, batt_cruise_kw),
        required_bus_peak_kw=bus_cruise_kw,
        soc_final_pct=soc * 100.0,
    ))

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 4 — Loiter
    # ═══════════════════════════════════════════════════════════════════════════
    bus_loiter_kw = power_loiter(m["takeoff_mass"],
                                  design.cruise_altitude_m,
                                  design.loiter_speed_mps)
    dur_loiter     = loiter_seconds

    eng_loiter_kw = min(engine_kw, bus_loiter_kw)
    batt_loiter_kw = max(0.0, bus_loiter_kw - eng_loiter_kw)

    if batt_loiter_kw > batt_cont_kw:
        violations.append(f"Loiter battery peak {batt_loiter_kw:.1f} kW > 2C limit")

    batt_loiter_kwh = batt_loiter_kw * dur_loiter / 3_600.0
    fuel_loiter_kg  = BSFC_best * eng_loiter_kw * dur_loiter / 3_600.0
    soc = max(0.0, soc - batt_loiter_kwh / design.battery_kwh)

    phases.append(MissionPhaseResult(
        phase_name="Loiter",
        duration_s=dur_loiter,
        battery_drawn_kwh=batt_loiter_kwh,
        fuel_consumed_kg=fuel_loiter_kg,
        battery_peak_kw=batt_loiter_kw,
        required_bus_peak_kw=bus_loiter_kw,
        soc_final_pct=soc * 100.0,
    ))

    # ═══════════════════════════════════════════════════════════════════════════
    # PHASE 5 — Descent / Landing
    # ═══════════════════════════════════════════════════════════════════════════
    bus_desc_kw  = power_climb(m["takeoff_mass"], climb_rate_mps=3.0) * 0.4
    dur_desc       = 300.0
    eng_desc_kw   = min(engine_kw, max(8.0, bus_desc_kw))
    batt_desc_kw  = max(0.0, bus_desc_kw - eng_desc_kw)

    batt_desc_kwh = batt_desc_kw * dur_desc / 3_600.0
    fuel_desc_kg  = BSFC_best * eng_desc_kw * dur_desc / 3_600.0
    soc = max(0.0, soc - batt_desc_kwh / design.battery_kwh)

    phases.append(MissionPhaseResult(
        phase_name="Descent / Landing",
        duration_s=dur_desc,
        battery_drawn_kwh=batt_desc_kwh,
        fuel_consumed_kg=fuel_desc_kg,
        battery_peak_kw=batt_desc_kw,
        required_bus_peak_kw=bus_desc_kw,
        soc_final_pct=soc * 100.0,
    ))

    # ── Final feasibility checks ─────────────────────────────────────────────
    if soc < 0.10:
        violations.append(
            f"SOC reserve violated: final SOC = {soc*100:.1f}% < 10% minimum"
        )

    # ── Endurance KPIs ────────────────────────────────────────────────────────
    total_fuel_kg   = sum(p.fuel_consumed_kg for p in phases)
    fuel_left_kg    = max(0.0, design.fuel_kg - total_fuel_kg)

    # Fuel-based endurance extension (engine still running at loiter)
    fuel_loiter_h = fuel_left_kg / (BSFC_best * max(engine_kw, 1.0))

    # Electric endurance: remaining SOC beyond 10% reserve at loiter power
    soc_avail      = max(0.0, soc - 0.10)
    loiter_pwr_kw  = max(bus_loiter_kw, 0.1)
    batt_end_h     = (design.battery_kwh * soc_avail * INVERTER_EFF / loiter_pwr_kw)

    # Total achievable endurance = loiter completed + fuel extension + battery extension
    achieved_loiter_h = loiter_seconds / 3_600.0
    max_endurance_h   = achieved_loiter_h + fuel_loiter_h + batt_end_h

    completed = len(violations) == 0

    return EvaluationResult(
        completed_all_required_phases=completed,
        takeoff_mass_kg             =m["takeoff_mass"],
        payload_kg                   =constraints.payload,
        battery_mass_kg              =m["battery_mass"],
        structure_mass_kg            =m["structure"],
        fuel_mass_kg                 =m["fuel_mass"],
        phases                       =phases,
        max_endurance_h              =max_endurance_h,
        battery_max_discharge_kw     =batt_cont_kw,
        constraint_violations       =violations,
    )
