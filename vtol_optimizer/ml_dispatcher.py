"""
HAL VTOL Series-Hybrid — ML Power Dispatcher
=============================================
Supervised learning (MLP) for real-time optimal power-split prediction,
plus a Gym-style RL environment for policy-gradient training.

Components
───────────
1. generate_training_data()
   → runs DesignSpace.random_sampling, evaluates each design,
   → dispatches optimal power-split per phase via DP,
   → saves (features, labels) to CSV.

2. train_dispatcher() / load_dispatcher()
   → scikit-learn MLPRegressor trained on CSV,
   → saves/loads model as pickle.

3. PowerDispatchEnv
   → Gym environment wrapping evaluate_design(),
   → actions: (battery_kw_fraction, engine_kw_fraction),
   → reward: fuel saved vs. baseline + constraint penalties.
"""

from __future__ import annotations
import csv
import pickle
import math
from pathlib import Path
from typing import Dict, List, Tuple, Optional

# Optional imports — ML features degrade gracefully if packages are missing
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except Exception:
    NUMPY_AVAILABLE = False

try:
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    SKLEARN_AVAILABLE = True
except Exception:
    SKLEARN_AVAILABLE = False

from vtol_optimizer import (
    HALConstraints, SeriesHybridDesign, evaluate_design, isa_density_kg_m3,
    ENGINE_RATED_KW, BSFC_best, INVERTER_EFF, R_AIR, T0, R_L, P0, ISA_K,
)
from vtol_optimizer.optimizer import DesignSpace, mission_dispatch_optimize


# ═══════════════════════════════════════════════════════════════════════════════
# TRAINING DATA GENERATION
# ═══════════════════════════════════════════════════════════════════════════════

def _phase_to_id(phase_name: str) -> int:
    mapping = {
        "VTOL Takeoff":     0,
        "Climb":            1,
        "Cruise":           2,
        "Loiter":           3,
        "Descent / Landing": 4,
    }
    return mapping.get(phase_name, -1)


def _compute_optimal_for_sample(d: SeriesHybridDesign, hal: HALConstraints,
                               loiter_seconds: float) -> List[Tuple]:
    """
    Run physics simulation + optimal dispatch for one design.
    Returns per-phase (feature_vector, label_vector) tuples.
    """
    result   = evaluate_design(hal, d, loiter_seconds=loiter_seconds)
    dispatch = mission_dispatch_optimize(hal, d, result)

    rows = []
    altitude = d.cruise_altitude_m
    speed    = d.loiter_speed_mps
    mass     = result.takeoff_mass_kg
    battery_kwh = d.battery_kwh
    rho      = isa_density_kg_m3(altitude)

    # Initial conditions per phase
    soc = 1.0
    phase_speeds = {
        "VTOL Takeoff":     0.0,
        "Climb":            40.0,
        "Cruise":           69.4,
        "Loiter":           d.loiter_speed_mps,
        "Descent / Landing": 60.0,
    }

    for p in result.phases:
        phase_id = _phase_to_id(p.phase_name)
        if phase_id < 0:
            continue

        spd = phase_speeds.get(p.phase_name, speed)
        eng_kw  = dispatch[p.phase_name]["engine_kw"]
        batt_kw = dispatch[p.phase_name]["battery_kw"]
        fuel_fl = dispatch[p.phase_name]["fuel_flow_kg_h"]

        # Features: [phase_id, altitude, speed, soc, mass, battery_temp_est]
        features = [
            float(phase_id),
            float(altitude),
            float(spd),
            float(soc),
            float(mass),
            25.0,  # battery_temp_c — nominal
        ]
        # Labels: [engine_kw, battery_kw, fuel_flow_kg_h]
        labels = [eng_kw, batt_kw, fuel_fl]

        rows.append((features, labels))
        soc = p.soc_final_pct / 100.0

    return rows


def generate_training_data(hal: HALConstraints,
                            n_samples: int = 2000,
                            output_path: Optional[str] = None,
                            seed: int = 42) -> str:
    """
    Generate training data by random sampling of the design space.

    Parameters
    ----------
    hal          : HALConstraints — mission constraints
    n_samples    : int — number of designs to sample
    output_path  : str — CSV path (default: dispatch_training_data.csv)
    seed         : int — RNG seed for reproducibility

    Returns
    -------
    Path to saved CSV file.

    CSV columns
    ------------
    phase_id, altitude_m, speed_mps, soc_pct, mass_kg, battery_temp_c,
    engine_kw, battery_kw, fuel_flow_kg_h
    """
    if output_path is None:
        output_path = "dispatch_training_data.csv"
    output_path = str(output_path)

    if NUMPY_AVAILABLE:
        import numpy as np
        np.random.seed(seed)
        rng = np.random.default_rng(seed)
    else:
        import random
        random.seed(seed)
        rng = None

    space = DesignSpace()
    designs = space.random_sampling(hal, n_samples)

    fieldnames = [
        "phase_id", "altitude_m", "speed_mps", "soc_pct", "mass_kg",
        "battery_temp_c", "engine_kw", "battery_kw", "fuel_flow_kg_h",
    ]

    rows_written = 0
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for d in designs:
            # Skip designs that clearly violate mass constraint
            approx_mass = (hal.payload +
                            d.battery_kwh / 250 * 1_000 +
                            d.fuel_kg * 1.2 +
                            d.rotor_disk_area_m2 * 15 + 220 + 60)
            if approx_mass > hal.MTOW * 1.05:
                continue

            loiter_s = hal.loiter_seconds
            try:
                phase_rows = _compute_optimal_for_sample(d, hal, loiter_s)
            except Exception:
                continue

            for features, labels in phase_rows:
                row = dict(zip(fieldnames, features + labels))
                # Map float lists to scalar where needed
                row["phase_id"]    = int(features[0])
                row["altitude_m"]   = features[1]
                row["speed_mps"]   = features[2]
                row["soc_pct"]     = features[3]
                row["mass_kg"]     = features[4]
                row["battery_temp_c"] = features[5]
                row["engine_kw"]   = labels[0]
                row["battery_kw"]  = labels[1]
                row["fuel_flow_kg_h"] = labels[2]
                writer.writerow(row)
                rows_written += 1

    print(f"[ML] Generated {rows_written} training samples → {output_path}")
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# MLP DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

class MLPowerDispatcher:
    """
    Scikit-learn MLPRegressor that predicts the optimal (engine_kw, battery_kw)
    setpoints for a given flight condition in a series-hybrid VTOL.

    Input features  (6-dim): [phase_id, altitude_m, speed_mps, soc_pct, mass_kg, temp_c]
    Output targets  (3-dim): [engine_kw, battery_kw, fuel_flow_kg_h]

    The model is trained on physics-simulation data generated by
    generate_training_data() and dispatched via DP.
    """

    def __init__(self,
                 hidden_layer_sizes: Tuple[int, ...] = (64, 32, 16),
                 max_iter: int = 500,
                 random_state: int = 42):
        if not SKLEARN_AVAILABLE:
            raise RuntimeError(
                "scikit-learn is not installed. Install with: pip install scikit-learn"
            )
        self.hidden_layer_sizes = hidden_layer_sizes
        self.max_iter          = max_iter
        self.random_state      = random_state
        self._scaler: Optional[StandardScaler] = None
        self._model: Optional[MLPRegressor]     = None

    def fit(self, csv_path: str) -> Dict[str, float]:
        """
        Train the MLP on a CSV generated by generate_training_data().

        Returns a dict of metrics: {"train_r2": ..., "val_r2": ..., "n_samples": ...}
        """
        if not NUMPY_AVAILABLE:
            raise RuntimeError("numpy is required. Install with: pip install numpy")

        import numpy as np
        from sklearn.metrics import r2_score, mean_absolute_error

        # Load CSV
        data = np.loadtxt(csv_path, delimiter=",", skiprows=1)
        X = data[:, :6]   # features
        y = data[:, 6:]   # labels

        # Split for validation
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, random_state=self.random_state
        )

        # Standardise
        self._scaler = StandardScaler()
        X_train_s = self._scaler.fit_transform(X_train)
        X_val_s   = self._scaler.transform(X_val)

        # Train MLP
        self._model = MLPRegressor(
            hidden_layer_sizes=self.hidden_layer_sizes,
            max_iter=self.max_iter,
            random_state=self.random_state,
            early_stopping=True,
            validation_fraction=0.1,
            n_iter_no_change=20,
            solver="adam",
            activation="relu",
        )
        self._model.fit(X_train_s, y_train)

        # Evaluate
        y_pred_train = self._model.predict(X_train_s)
        y_pred_val   = self._model.predict(X_val_s)

        metrics = {
            "train_r2":       r2_score(y_train, y_pred_train),
            "val_r2":         r2_score(y_val,   y_pred_val),
            "train_mae_kw":   mean_absolute_error(y_train, y_pred_train),
            "val_mae_kw":     mean_absolute_error(y_val,   y_pred_val),
            "n_samples":      int(len(y_train)),
        }
        return metrics

    def predict(self, X: "np.ndarray") -> "np.ndarray":
        """Predict (engine_kw, battery_kw, fuel_flow_kg_h) for input features."""
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")
        X_s = self._scaler.transform(X)
        return self._model.predict(X_s)

    def save(self, path: str):
        if self._model is None:
            raise RuntimeError("Nothing to save — model not trained.")
        path = str(path)
        with open(path, "wb") as f:
            pickle.dump({"model": self._model, "scaler": self._scaler}, f)
        print(f"[ML] Model saved → {path}")

    @classmethod
    def load(cls, path: str) -> "MLPowerDispatcher":
        path = str(path)
        with open(path, "rb") as f:
            obj = pickle.load(f)
        disp = cls()
        disp._model  = obj["model"]
        disp._scaler = obj["scaler"]
        print(f"[ML] Model loaded ← {path}")
        return disp


def train_dispatcher(csv_path: str,
                    model_path: str = "dispatcher_model.pkl",
                    **ml_kwargs) -> Tuple["MLPowerDispatcher", Dict[str, float]]:
    """
    Convenience: train and save a dispatcher in one call.

    Returns (dispatcher, metrics).
    """
    disp = MLPowerDispatcher(**ml_kwargs)
    metrics = disp.fit(csv_path)
    disp.save(model_path)
    return disp, metrics


# ═══════════════════════════════════════════════════════════════════════════════
# RL ENVIRONMENT (Gym-style)
# ═══════════════════════════════════════════════════════════════════════════════

class PowerDispatchEnv:
    """
    A simple Gym-compatible environment for training RL policies for
    series-hybrid power dispatch.

    State (observation space)
    ─────────────────────────
    soc            : float [0, 1]     — battery state of charge
    altitude       : float [3000, 10000] m
    speed          : float [30, 80]    m/s
    mass           : float [400, 1000] kg
    phase_id       : int   [0, 4]
    battery_temp   : float [15, 55]    °C

    Action space (continuous)
    ────────────────────────
    battery_fraction : float [0, 1] — fraction of bus power drawn from battery
    engine_fraction  : float [0, 1] — fraction of engine rated power used

    Reward
    ─────
    r = -(fuel_consumed_kg + 100 × constraint_violation_penalty)
    Constraint violations:
      • SOC < 0.10  → +10 penalty
      • SOC < 0     → +50 penalty (terminal)
      • engine > 60 kW → +10 penalty
      • phase transition violated → +5 penalty
    """

    # ── Gym interface ──────────────────────────────────────────────────────────

    def __init__(self, hal: HALConstraints = None, design: SeriesHybridDesign = None):
        if hal is None:
            hal = HALConstraints()
        if design is None:
            design = SeriesHybridDesign(
                battery_kwh=34.0, fuel_kg=145.0,
                rotor_disk_area_m2=45.0, engine_load_target=0.92,
                cruise_altitude_m=6_000.0, loiter_speed_mps=51.0,
            )
        self.hal    = hal
        self.design = design
        self.result = evaluate_design(hal, design, loiter_seconds=hal.loiter_seconds)
        self.phases = self.result.phases
        self.phase_idx = 0
        self.soc    = 1.0
        self.done   = False

    @property
    def observation_space(self) -> List[Tuple[float, float]]:
        return [
            (0.0, 1.0),       # soc
            (3_000.0, 10_000.0),  # altitude
            (30.0, 80.0),     # speed
            (400.0, 1_000.0), # mass
            (0.0, 4.0),       # phase_id
            (15.0, 55.0),     # battery_temp
        ]

    @property
    def action_space(self) -> List[Tuple[float, float]]:
        return [(0.0, 1.0), (0.0, 1.0)]  # battery_fraction, engine_fraction

    def reset(self) -> Dict[str, float]:
        """Reset to start of mission. Returns initial observation."""
        self.phase_idx = 0
        self.soc       = 1.0
        self.done      = False
        return self._obs()

    def step(self, action: Tuple[float, float]) -> Tuple[Dict, float, bool, Dict]:
        """
        Execute one control step.

        action[0] = battery_fraction ∈ [0, 1]
        action[1] = engine_fraction  ∈ [0, 1]

        Returns (obs, reward, done, info)
        """
        batt_frac, eng_frac = action

        phase = self.phases[self.phase_idx]
        bus_kw = phase.required_bus_peak_kw

        # Power split
        eng_kw  = min(eng_frac * ENGINE_RATED_KW, bus_kw)
        batt_kw = (batt_frac * bus_kw)

        # Fuel
        fuel_kg_h = BSFC * eng_kw
        fuel_step_kg = fuel_kg_h * (phase.duration_s / 3_600.0)

        # SOC update
        batt_drawn_kwh = batt_kw * phase.duration_s / 3_600.0 / INVERTER_EFF
        soc_new = max(0.0, self.soc - batt_drawn_kwh / self.design.battery_kwh)

        # Constraint penalties
        penalty = 0.0
        if soc_new < 0.10:
            penalty += 10.0
        if soc_new < 0.0:
            penalty += 50.0
            self.done = True
        if eng_kw > ENGINE_RATED_KW:
            penalty += 10.0

        reward = -(fuel_step_kg + penalty)

        # Advance phase
        self.soc = soc_new
        if not self.done:
            self.phase_idx += 1
            if self.phase_idx >= len(self.phases):
                self.done = True

        info = {"fuel_kg": fuel_step_kg, "penalty": penalty,
                "eng_kw": eng_kw, "batt_kw": batt_kw,
                "soc": self.soc, "phase_idx": self.phase_idx}

        return self._obs(), reward, self.done, info

    def render(self, mode="human"):
        phase = self.phases[self.phase_idx] if self.phase_idx < len(self.phases) else None
        print(f"  Phase: {phase.phase_name if phase else 'DONE'}  "
              f"SOC: {self.soc:.2%}  Mass: {self.result.takeoff_mass_kg:.0f} kg")

    def _obs(self) -> Dict[str, float]:
        phase = self.phases[min(self.phase_idx, len(self.phases) - 1)]
        return {
            "soc":         self.soc,
            "altitude":    self.design.cruise_altitude_m,
            "speed":      phase.required_bus_peak_kw / max(self.result.takeoff_mass_kg, 1) * 1_000,
            "mass":       self.result.takeoff_mass_kg,
            "phase_id":   float(self.phase_idx),
            "battery_temp": 25.0,
        }
