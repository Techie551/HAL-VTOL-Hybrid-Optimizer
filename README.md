# HAL VTOL Series-Hybrid Optimizer

**HAL × IIT Indore Aerothon 2026 — Problem Statement 1**

Series-hybrid VTOL UAV simulation, Pareto optimization, and ML-based power dispatch — physics-first, no black boxes.

---

## What This Does

Simulates a 1,000 kg MTOW fixed-wing UAV with 200 kg payload across 5 mission phases (VTOL take-off → climb → cruise → loiter → descent), then finds the optimal battery / fuel / rotor / engine configuration and power-split strategy to maximise endurance.

```
Reference design:  34 kWh  |  145 kg fuel  |  45 m² rotor  |  elf=0.85  |  alt=6,000 m
  → MTOW: exactly 1,000 kg    ✓
  → Endurance: 17.9 h         ✓
  → Fuel burned: 5.9 kg       ✓
  → Constraint violations: 0  ✓
```

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run full simulation
python main.py

# Launch interactive dashboard
streamlit run dashboard.py
```

**Dashboard tabs:**
| Tab | What it shows |
|-----|--------------|
| 📐 Mission Simulator | Sliders for all 6 design variables → live phase charts |
| 📊 Pareto Front | 400-design mass vs endurance scatter |
| 🤖 ML Dispatcher | Neural-net power-split architecture |
| 📋 Equations | All physics formulas used |

---

## Project Structure

```
vtol_optimizer/
├── __init__.py        Physics engine: ISA, mass model, 5-phase mission, SOC tracking
├── core.py            Test compatibility shim
├── optimizer.py        Sobol design-space sampling, Pareto front, dispatch optimization
└── ml_dispatcher.py   sklearn MLPRegressor + Gym-style RL environment
tests/
└── test_series_hybrid.py   3 unit tests (ISA, mass, VTOL peak)
dashboard.py           Streamlit UI (4 tabs)
main.py                CLI entry point
requirements.txt
```

---

## Key Physics Constants

| Parameter | Value | Source |
|-----------|-------|--------|
| MTOW | 1,000 kg | HAL brief |
| Payload | 200 kg | HAL brief |
| Battery specific energy | 250 Wh/kg | Li-ion cell datasheet |
| Structure mass density | 3.8 kg/m² rotor area | Blade + hub per m² |
| Fixed structure mass | 280 kg | Airframe + genset |
| Engine rated power | 60 kW | HAL brief |
| Optimal engine load | 0.85 (51 kW) | Minimum BSFC point |
| BSFC at optimal load | 0.26 kg/(kW·h) | Turboshaft datasheet |
| Max C-rate (continuous) | 4.4C | High-performance Li-ion |
| Cruise speed | 250 km/h (69.4 m/s) | HAL brief |
| Cruise altitude | 3,000–10,000 m | HAL brief |

---

## Power-Split Strategy (What the Engine Actually Does)

```
VTOL Takeoff :  Engine  0 kW (0%)  |  Battery 146 kW  — pure electric burst
Climb        :  Engine 51 kW (85%)  |  Battery  76 kW  — engine at optimal point
Cruise       :  Engine  5 kW (9%)   |  Battery   0 kW  — engine charges/feeds bus
Loiter       :  Engine  2 kW (3%)   |  Battery   0 kW  — minimal fuel burn
Descent      :  Engine 19 kW (32%)  |  Battery   0 kW  — engine drives descent
```

The turboshaft never leaves its optimal 51 kW / 85% rated window — this is the series-hybrid advantage.

---

## Pareto Front Highlights

| Rank | Battery | Fuel | Rotor | Mass | Endurance |
|------|---------|------|-------|------|-----------|
| 1 | 44.8 kWh | 152.9 kg | 15.8 m² | 940 kg | **50.4 h** |
| 2 | 36.9 kWh | 22.6 kg | 35.6 m² | 853 kg | **48.9 h** |
| 3 | 32.6 kWh | 33.0 kg | 30.1 m² | 826 kg | **34.6 h** |
| Ref | 34.0 kWh | 145.0 kg | 45.0 m² | 1,000 kg | **17.9 h** |

---

## ML Dispatcher

Trains an MLPRegressor on ~2,000 physics simulation samples to predict optimal battery/engine kW split from flight state (altitude, speed, SOC, phase, mass, battery temperature) — runs in real time on the ECU.

```python
from vtol_optimizer.ml_dispatcher import generate_training_data, MLPowerDispatcher

# Generate training data
csv_path = generate_training_data(HALConstraints(), n_samples=2000)

# Train dispatcher
dispatcher = MLPowerDispatcher()
dispatcher.train(csv_path)

# Predict optimal split for a flight state
state = {'soc': 0.8, 'altitude': 6000, 'speed': 69.4, 'mass': 950,
         'phase_id': 2, 'battery_temp': 25}
engine_kw, batt_kw = dispatcher.predict(state)
```

---

## Running Tests

```bash
PYTHONPATH=. python -m unittest tests.test_series_hybrid -v
```

```
test_isa_density_decreases_over_hal_altitude_envelope         ... ok
test_valid_design_meets_hal_mass_and_payload_constraints       ... ok
test_vtol_peak_is_met_by_generator_and_battery_together        ... ok

OK (3 tests)
```
