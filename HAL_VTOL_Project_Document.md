# HAL VTOL Series-Hybrid UAV Optimizer
## Project Document — HAL × IIT Indore Aerothon 2026

**Team:** Zypher  
**Author:** Anurag  
**Date:** July 2026  
**Repository:** github.com/Techie551/HAL-VTOL-Hybrid-Optimizer

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Background & Literature Review](#3-background--literature-review)
4. [System Architecture](#4-system-architecture)
5. [Physics Engine](#5-physics-engine)
6. [Mass Model](#6-mass-model)
7. [Mission Profile & Phases](#7-mission-profile--phases)
8. [Power-Split Strategy](#8-power-split-strategy)
9. [Pareto Optimization](#9-pareto-optimization)
10. [ML Power Dispatcher](#10-ml-power-dispatcher)
11. [Dashboard](#11-dashboard)
12. [Results](#12-results)
13. [Project Structure](#13-project-structure)
14. [How to Run](#14-how-to-run)
15. [Physics Equations Reference](#15-physics-equations-reference)
16. [Future Work](#16-future-work)

---

## 1. Executive Summary

This project delivers a **physics-first optimization framework** for a series-hybrid VTOL UAV designed to HAL's specifications for the IIT Indore Aerothon 2026. The system simulates the full mission envelope of a 1,000 kg MTOW fixed-wing UAV carrying 200 kg payload, identifies the Pareto-optimal design frontier across 6 design variables, and trains an MLP neural network to predict optimal engine/battery power split in real time.

**Key deliverables:**
- Physics engine: ISA atmosphere, actuator-disk VTOL, drag, battery SOC, fuel burn
- Pareto optimizer: Sobol quasi-random sampling, 500 designs, endurance–mass trade-off
- ML dispatcher: MLPRegressor trained on 2,000 physics-simulation samples
- Interactive dashboard: Streamlit 4-tab UI with live sliders and Pareto front visualization
- Reference design: exactly 1,000 kg MTOW, 17.9 h endurance, 0 constraint violations

---

## 2. Problem Statement

### 2.1 HAL Mission Requirements

| Parameter | Value | Source |
|---|---|---|
| Maximum Takeoff Mass (MTOW) | 1,000 kg | HAL brief |
| Payload | 200 kg | HAL brief |
| Cruise Speed | 250 km/h | HAL brief |
| Cruise Altitude | 3,000 – 10,000 m | HAL brief |
| VTOL Capability | Required | HAL brief |
| Minimum Loiter | 30 minutes | HAL brief |
| Turboshaft Rating | ≤ 60 kW continuous | HAL brief |
| Battery Capacity | ≤ 50 kWh | HAL brief |

### 2.2 Design Challenge

The optimization problem is to **maximize endurance** while satisfying all HAL constraints. The design space has 6 continuous variables:

| Variable | Lower Bound | Upper Bound | Units |
|---|---|---|---|
| Battery capacity | 5.0 | 50.0 | kWh |
| Fuel load | 10.0 | 200.0 | kg |
| Rotor disk area | 15.0 | 80.0 | m² |
| Engine load fraction | 0.50 | 1.00 | — |
| Cruise altitude | 3,000 | 10,000 | m |
| Loiter speed | 30 | 80 | m/s |

### 2.3 Objective

$$\max \text{ endurance}(x) \quad \text{s.t.} \quad \text{MTOW} \leq 1000 \text{ kg}, \text{ SOC}_{final} \geq 10\%$$

---

## 3. Background & Literature Review

### 3.1 VTOL UAV Classes

| Architecture | Propulsion | Endurance | Complexity |
|---|---|---|---|
| Pure Electric | Battery only | 30–60 min | Low |
| Pure Turbine | Jet fuel only | 3–6 h | Medium |
| Parallel Hybrid | Engine + Battery (mechanical) | 2–4 h | High |
| **Series Hybrid** | Engine → Generator → Battery → Motors | **5–50 h** | **Medium** |

### 3.2 Why Series-Hybrid is Optimal for This Mission

1. **Engine stays at optimal point**: The turboshaft runs at a constant 51 kW (85% rated), which is its minimum-BSFC point. There is no direct mechanical link to the propeller.
2. **Battery handles transients**: VTOL burst, climb power spikes, and cruise surplus/deficit are managed by the battery, which charges during excess engine output.
3. **No weight penalty of parallel architecture**: No separate electric motors for VTOL + mechanical transmission for cruise.

### 3.3 Prior Work

| Reference | Approach | Limitation |
|---|---|---|
| NASA X-57 (parallel hybrid) | Mechanical distribution | Complex, heavy transmission |
| Volocopter VC200 (electric) | Pure electric | 30-min endurance ceiling |
| This work | Series-hybrid + Sobol optimization + ML | — |

---

## 4. System Architecture

### 4.1 Power Train Topology

```
┌─────────────────────────────────────────────────────────────────┐
│                         SERIES HYBRID POWER TRAIN                 │
│                                                                  │
│   ┌──────────┐    ┌────────────┐    ┌───────┐    ┌──────────┐  │
│   │Turboshaft│───▶│  Generator  │───▶│ DC bus│◀───│ Battery  │  │
│   │  60 kW   │    │   (95%)    │    │       │    │  34 kWh  │  │
│   └──────────┘    └────────────┘    └───┬───┘    └──────────┘  │
│                                          │                       │
│                    ┌─────────────────────┼──────────────────┐   │
│                    ▼                     ▼                   ▼   │
│              ┌──────────┐         ┌───────────┐       ┌──────┐  │
│              │  VTOL    │         │  Pusher   │       │ECU / │  │
│              │ Rotors   │         │Propeller  │       │ ML   │  │
│              │(45 m²)  │         │(fixed wing)│      │Dispatcher│
│              └──────────┘         └───────────┘       └──────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Specifications

| Component | Specification |
|---|---|
| Turboshaft | 60 kW rated, 72 kW peak (2 min), BSFC = 0.26 kg/kWh at optimal |
| Generator | 95% efficient DC generator |
| Battery | Li-ion, 250 Wh/kg specific energy, 4.4C continuous / 5C peak |
| VTOL Rotors | 45 m² disk area (default), 3.8 kg/m² blade+hub mass density |
| Inverter | 95% efficient DC/AC |
| Fixed-wing pusher | 65% propeller efficiency |

---

## 5. Physics Engine

### 5.1 ISA Atmosphere Model

The International Standard Atmosphere (ISA) models air density from sea level to 11,000 m:

```
T(h) = T₀ - L·h          [K]
P(h) = P₀ · (1 - L·h/T₀)^(g·M/(R·L))
ρ(h) = P / (R·T)
```

**Constants used:**
| Symbol | Value | Unit |
|---|---|---|
| T₀ | 288.15 | K (sea-level temperature) |
| P₀ | 101,325 | Pa (sea-level pressure) |
| L | 0.0065 | K/m (lapse rate) |
| g | 9.81 | m/s² |
| M | 0.0289644 | kg/mol (molar mass of air) |
| R | 8.31446 | J/(mol·K) (universal gas constant) |
| R_air | 287.05 | J/(kg·K) (specific gas constant) |

### 5.2 VTOL Hover Power (Actuator Disk Theory)

From momentum theory, hover induced velocity:

$$v_{ind} = \sqrt{\frac{W/A}{2 \rho}}$$

where W/A is the disk loading (N/m²). Bus power required:

$$P_{bus} = \frac{T \cdot v_{ind}}{\eta_{disk} \cdot \eta_{inverter}} \times 1.08 \text{ (transient margin)}$$

### 5.3 Cruise Power (Steady Level Flight)

Parasite drag: $D = \frac{1}{2} \rho v^2 C_D A$

For the reference UAV with VTOL rotor mounts: $C_D A = 0.032 \text{ m}^2$

$$P_{mech} = \frac{D \cdot v}{\eta_{prop}} = \frac{0.5 \rho v^3 C_D A}{\eta_{prop}}$$

### 5.4 Climb Power

$$P_{climb} = \frac{W \cdot \dot{h}}{\eta_{climb}}$$

where $\eta_{climb} = 0.62$ (accounts for fuselage drag in climb attitude).

### 5.5 Loiter Power

Minimum drag speed (maximum endurance) is approximately:

$$P_{loiter} = 0.80 \times P_{cruise}(v_{cruise})$$

This occurs when induced drag equals parasite drag.

---

## 6. Mass Model

### 6.1 Mass Budget Equation

$$MTOW = m_{payload} + m_{structure} + m_{fuel} + m_{battery} + m_{systems}$$

### 6.2 Component Masses

| Component | Formula | Value (Reference) |
|---|---|---|
| Payload | Fixed | 200 kg |
| Battery mass | $m_{batt} = \frac{kWh}{250} \times 1000$ | 136 kg (34 kWh) |
| Rotor mass | $m_{rotor} = A_{rotor} \times 3.8$ | 171 kg (45 m²) |
| Structure mass | $m_{struct} = 280 + m_{rotor}$ | 451 kg |
| Fuel system mass | $m_{fuel} = fuel\_kg + 8$ | 153 kg (145+8) |
| Systems/margins | Fixed | 60 kg |
| **MTOW** | Sum | **1,000 kg** ✓ |

### 6.3 Structural Baseline

The 280 kg fixed structure includes:
- Wings + control surfaces: ~85 kg
- Fuselage + landing gear: ~100 kg
- Tail + provisions: ~25 kg
- VTOL rotor hubs + structure: ~45 kg
- Generator + power electronics: ~55 kg

---

## 7. Mission Profile & Phases

### 7.1 The 5-Phase Mission

```
┌──────────┐    ┌────────┐    ┌────────┐    ┌────────┐    ┌──────────┐
│ VTOL     │───▶│ Climb  │───▶│ Cruise │───▶│ Loiter │───▶│ Descent  │
│ Takeoff  │    │ 8m/s   │    │400 km  │    │ 30 min │    │ /Landing │
│ 60 s     │    │→6,000m │    │250km/h │    │100 kt  │    │  300 s   │
└──────────┘    └────────┘    └────────┘    └────────┘    └──────────┘
```

### 7.2 Phase Details

| Phase | Duration | Engine | Battery | Fuel Flow |
|---|---|---|---|---|
| **VTOL Takeoff** | 60 s | 5 kW (idle) | Full burst (~146 kW) | ~0.02 kg |
| **Climb** | 750 s | 51 kW (optimal) | Gap filler | ~2.8 kg |
| **Cruise** | ~5,767 s | 51 kW (optimal) | 0–charge | ~21.6 kg |
| **Loiter** | 1,800 s | 2 kW (min) | 0 kW | ~0.3 kg |
| **Descent** | 300 s | 19 kW (descent) | 0 kW | ~0.4 kg |

### 7.3 Battery SOC Tracking

Battery state-of-charge is tracked per phase:

$$SOC_{new} = SOC_{old} - \frac{P_{batt} \cdot \Delta t}{kWh_{battery} \cdot \eta_{inverter}} + \frac{P_{charge} \cdot \Delta t}{kWh_{battery}}$$

Minimum SOC reserve: 10% (hard constraint).

---

## 8. Power-Split Strategy

### 8.1 The Optimal Policy

For a series hybrid, the optimal power-split is **analytically derivable**:

> **Run the engine at its best-efficiency point (minimum BSFC). Let the battery fill or absorb the difference.**

For a 60 kW turboshaft, the best BSFC point is at approximately **85% rated load (51 kW)**.

### 8.2 Phase-by-Phase Dispatch

| Phase | Engine kW | Battery kW | Rationale |
|---|---|---|---|
| VTOL | 5 (idle) | Full burst | Engine can't spin up fast enough for VTOL |
| Climb | 51 (85%) | Gap | Engine at optimal, battery fills climb peak |
| Cruise | 51 (85%) | 0 / charge | Engine exceeds need → battery charges |
| Loiter | 2 (min) | 0 | Minimal fuel burn, engine at floor |
| Descent | 19 | 0 | Engine drives descent, battery idle |

### 8.3 Engine BSFC Map

Brake-specific fuel consumption is minimum at high load:

$$BSFC = \frac{0.26 \text{ kg}}{kW \cdot h} \quad \text{at } 51 \text{ kW (85\% load)}$$

The series-hybrid architecture allows the engine to always operate at this point — unlike a parallel hybrid where engine speed varies with vehicle speed.

---

## 9. Pareto Optimization

### 9.1 Design Space Exploration

Three sampling strategies are implemented:

| Strategy | Description | Coverage |
|---|---|---|
| Grid search | Uniform lattice across 6D space | Predictable but sparse |
| Random sampling | Pure Monte Carlo | Fast, unbiased |
| **Sobol sequence** | **Quasi-random low-discrepancy** | **Best coverage per N** |

### 9.2 Sobol Sequence Advantage

For N=500 samples in 6 dimensions, Sobol achieves **uniform space-filling** compared to Monte Carlo's clustering. This is critical because:

- The feasible region is a small fraction of the total design space
- Sobol avoids clustering in corners and edges
- Pareto front is resolved with fewer function evaluations

### 9.3 Pareto Dominance

Design A dominates design B if:

$$ endurance_A \geq endurance_B \quad \text{and} \quad mass_A \leq mass_B \quad \text{and} \quad \text{at least one strict}$$

### 9.4 Optimization Results

**Reference design** (34 kWh, 145 kg fuel, 45 m² rotor, elf=0.92):
- MTOW: exactly 1,000 kg ✓
- Endurance: 17.9 h
- Constraint violations: 0

**Best endurance design** (Pareto rank 1):
- Battery: 44.8 kWh, Fuel: 152.9 kg, Rotor: 15.8 m², Mass: 940 kg
- Endurance: **50.4 h**
- Trade-off: Smaller rotor (less VTOL margin) enables much longer endurance

---

## 10. ML Power Dispatcher

### 10.1 Why ML for Power Dispatch?

The analytical DP solution gives the optimal policy for a **known mission profile**. But in real flight:

1. Wind gusts change actual climb rate and drag
2. Battery temperature degrades capacity over time
3. Engine wear shifts the optimal BSFC point
4. Mission may deviate from planned profile

An ML model trained on 2,000 physics simulations can **generalize to unseen conditions**.

### 10.2 Training Data Generation

```python
# 2,000 random designs → evaluate → optimal dispatch per phase
generate_training_data(hal, n_samples=2000)
```

Each sample produces 5 phase-level records with features:

| Feature | Unit | Range |
|---|---|---|
| phase_id | — | 0–4 |
| altitude_m | m | 3,000–10,000 |
| speed_mps | m/s | 0–80 |
| soc_pct | % | 10–100 |
| mass_kg | kg | 400–1,000 |
| battery_temp_c | °C | ~25 (nominal) |

Labels: engine_kw, battery_kw, fuel_flow_kg_h

### 10.3 MLP Architecture

```
Input (6 features)
    ↓
Dense(64, ReLU) + BatchNorm
    ↓
Dense(32, ReLU) + BatchNorm
    ↓
Dense(16, ReLU)
    ↓
Output (3): [engine_kw, battery_kw, fuel_flow_kg_h]
```

Training: Adam optimizer, early stopping, 80/20 train/val split.

### 10.4 RL Environment (Gym-style)

For future policy-gradient training, a `PowerDispatchEnv` is provided:

- **State**: SOC, altitude, speed, mass, phase_id, battery_temp
- **Action**: (battery_fraction, engine_fraction) ∈ [0,1]²
- **Reward**: $r = -(fuel\_kg + 100 \times constraint\_violations)$

---

## 11. Dashboard

The Streamlit dashboard provides 4 interactive tabs:

### Tab 1: 📐 Mission Simulator
- 6 sliders: battery kWh, fuel kg, rotor area, engine load, altitude, loiter speed
- Live mass budget display
- Phase-by-phase power/energy table
- SOC trajectory chart

### Tab 2: 📊 Pareto Front
- Scatter plot: mass (x) vs endurance (y) for 300 sampled designs
- Pareto-optimal points highlighted in blue
- Reference design marked in red
- Hover for design details

### Tab 3: 🤖 ML Dispatcher
- Architecture diagram (MLP layers)
- Feature importance display
- Predicted vs actual power split for sample flight states
- Model performance metrics (R², MAE)

### Tab 4: 📋 Equations
- All physics formulas rendered
- Constant reference table
- Phase-by-phase power calculations

---

## 12. Results

### 12.1 Reference Design (HAL Baseline)

| Parameter | Value | HAL Limit | Status |
|---|---|---|---|
| MTOW | 1,000.0 kg | 1,000 kg | ✓ Exactly met |
| Battery mass | 136.0 kg | — | — |
| Structure mass | 451.0 kg | — | — |
| Fuel system mass | 153.0 kg | — | — |
| Systems | 60.0 kg | — | — |
| **Total** | **1,000.0 kg** | **1,000 kg** | **✓ PASS** |
| Endurance | 17.9 h | — | — |
| Fuel burned | 5.9 kg | — | — |
| Constraint violations | 0 | 0 | ✓ PASS |

### 12.2 Pareto Front Highlights

| Rank | Battery (kWh) | Fuel (kg) | Rotor (m²) | Mass (kg) | Endurance (h) |
|---|---|---|---|---|---|
| 1 | 44.8 | 152.9 | 15.8 | 940 | **50.4** |
| 2 | 36.9 | 22.6 | 35.6 | 853 | **48.9** |
| 3 | 32.6 | 33.0 | 30.1 | 826 | **34.6** |
| Ref | 34.0 | 145.0 | 45.0 | 1,000 | **17.9** |

### 12.3 Power-Split Analysis

For the reference design across the mission:

| Phase | Engine (kW) | Engine (% rated) | Battery (kW) | Fuel (kg/h) |
|---|---|---|---|---|
| VTOL | 5 | 8% | 146.3 | ~0 |
| Climb | 51 | 85% | 76.2 | 13.3 |
| Cruise | 51 | 85% | 0 (charging) | 13.3 |
| Loiter | 2 | 3% | 0 | 0.5 |
| Descent | 19 | 32% | 0 | 4.9 |

---

## 13. Project Structure

```
HAL-VTOL-Hybrid-Optimizer/
├── vtol_optimizer/
│   ├── __init__.py          # Physics engine: ISA, mass model, 5-phase mission
│   ├── core.py              # Test compatibility shim
│   ├── optimizer.py          # Sobol sampling, Pareto front, DP dispatch
│   └── ml_dispatcher.py      # sklearn MLPRegressor + Gym RL environment
├── tests/
│   └── test_series_hybrid.py # 3 unit tests
├── dashboard.py              # Streamlit 4-tab UI
├── main.py                   # CLI: simulation + Pareto + dispatch
├── requirements.txt          # Dependencies
├── README.md                 # Quick-start guide
└── HAL_VTOL_Project_Document.md  # This document
```

---

## 14. How to Run

### 14.1 Install Dependencies

```bash
pip install -r requirements.txt
```

Key dependencies: `numpy`, `scikit-learn`, `streamlit`, `plotly`, `pandas`

### 14.2 Run Simulation

```bash
python main.py
```

Output: Mass budget, phase table, Pareto front table, optimal dispatch table.

### 14.3 Run Dashboard

```bash
streamlit run dashboard.py
# Opens at http://localhost:8501
```

### 14.4 Run Tests

```bash
PYTHONPATH=. python -m unittest tests.test_series_hybrid -v
```

### 14.5 Train ML Dispatcher

```bash
python main.py --train-ml
```

Generates 2,000-sample CSV and trains MLPRegressor.

---

## 15. Physics Equations Reference

### Atmosphere
$$T(h) = T_0 - Lh \quad [K]$$
$$P(h) = P_0\left(1 - \frac{Lh}{T_0}\right)^{gM/(RL)} \quad [Pa]$$
$$\rho(h) = \frac{P(h)}{R_{air} \cdot T(h)} \quad [kg/m³]$$

### VTOL Hover
$$v_{ind} = \sqrt{\frac{mg/A}{2\rho}} \quad [m/s]$$
$$P_{bus} = \frac{mg \cdot v_{ind}}{\eta_{rotor} \cdot \eta_{inv}} \times 1.08 \quad [kW]$$

### Cruise Drag
$$D = \frac{1}{2}\rho v^2 C_D A \quad [N]$$
$$P_{cruise} = \frac{D \cdot v}{\eta_{prop}} \quad [kW]$$

### Battery SOC
$$\Delta SOC = \frac{P_{batt} \cdot \Delta t}{kWh_{batt}} - \frac{P_{charge} \cdot \Delta t}{kWh_{batt} \cdot \eta_{inv}}$$

### Engine Fuel
$$\dot{m}_{fuel} = BSFC \times P_{engine} \quad [kg/h]$$

### Mass
$$MTOW = m_{payload} + m_{struct} + m_{rotor} + m_{batt} + m_{fuel} + m_{systems}$$
$$m_{batt} = \frac{kWh}{250} \times 1000 \quad [kg]$$
$$m_{rotor} = A_{rotor} \times 3.8 \quad [kg]$$

---

## 16. Future Work

1. **Sensitivity Analysis**: Monte Carlo uncertainty propagation on all physics constants
2. **Multi-objective Optimization**: Add emission / cost objectives via NSGA-II
3. **Real-time ML**: Deploy MLPRegressor on embedded ECU (TensorFlow Lite)
4. **RL Policy Training**: Train PPO on `PowerDispatchEnv` for adaptive dispatch
5. **Thermal Model**: Battery temperature-dependent capacity and C-rate limits
6. **Wind Gust Model**: Stochastic mission profile for robust dispatch
7. **Structural Optimization**: Couple rotor sizing with structural weight model
8. **Hardware-in-the-Loop**: Validate physics model against real turboshaft data

---

*Document version 1.0 — HAL × IIT Indore Aerothon 2026*  
*github.com/Techie551/HAL-VTOL-Hybrid-Optimizer*
