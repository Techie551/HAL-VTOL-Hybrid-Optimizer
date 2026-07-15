"""
vtol_optimizer.core — compatibility shim
==========================================
The test file (tests/test_series_hybrid.py) imports from this module.
Re-exports everything from the main __init__ with one addition:
an 'engine_load_target' alias for 'engine_load_fraction' on SeriesHybridDesign.

Usage (deprecated — prefer importing from vtol_optimizer directly):
    from vtol_optimizer.core import HALConstraints, SeriesHybridDesign, evaluate_design
"""

from vtol_optimizer import (
    HALConstraints,
    SeriesHybridDesign,
    evaluate_design,
    isa_density_kg_m3,
    MissionPhaseResult,
    EvaluationResult,
)
from vtol_optimizer import (
    FIXED_STRUCTURE_KG,
    ROTOR_MASS_DENSITY,
    FUEL_TANK_KG,
    SYSTEMS_KG,
    BATTERY_SPEC_ENERGY,
    ENGINE_RATED_KW,
    ENGINE_PEAK_KW,
    BSFC_best,
    MAX_C_CONTINUOUS,
    MAX_C_PEAK,
    INVERTER_EFF,
    ROTOR_EFF,
    PROP_EFF,
    G,
)

# ── engine_load_target alias ─────────────────────────────────────────────────
# The original test uses engine_load_target=0.92.  We patch SeriesHybridDesign
# to accept that as an alias so the test passes without modification.
_original_init = SeriesHybridDesign.__init__

def _patched_init(self, battery_kwh=None, fuel_kg=None, rotor_disk_area_m2=None,
                  engine_load_fraction=None, cruise_altitude_m=None, loiter_speed_mps=None,
                  **kwargs):
    # Accept the old name as well
    if engine_load_fraction is None and "engine_load_target" in kwargs:
        engine_load_fraction = kwargs.pop("engine_load_target")
    _original_init(
        self,
        battery_kwh=battery_kwh,
        fuel_kg=fuel_kg,
        rotor_disk_area_m2=rotor_disk_area_m2,
        engine_load_fraction=engine_load_fraction,
        cruise_altitude_m=cruise_altitude_m,
        loiter_speed_mps=loiter_speed_mps,
        **kwargs,
    )

SeriesHybridDesign.__init__ = _patched_init

__all__ = [
    "HALConstraints",
    "SeriesHybridDesign",
    "evaluate_design",
    "isa_density_kg_m3",
    "MissionPhaseResult",
    "EvaluationResult",
    # constants
    "FIXED_STRUCTURE_KG",
    "ROTOR_MASS_DENSITY",
    "FUEL_TANK_KG",
    "SYSTEMS_KG",
    "BATTERY_SPEC_ENERGY",
    "ENGINE_RATED_KW",
    "ENGINE_PEAK_KW",
    "BSFC_best",
    "MAX_C_CONTINUOUS",
    "MAX_C_PEAK",
    "INVERTER_EFF",
    "ROTOR_EFF",
    "PROP_EFF",
    "G",
]
