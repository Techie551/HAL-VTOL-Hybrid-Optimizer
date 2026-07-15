import unittest

from vtol_optimizer.core import (
    HALConstraints,
    SeriesHybridDesign,
    evaluate_design,
    isa_density_kg_m3,
)


class SeriesHybridPhysicsTests(unittest.TestCase):
    def test_isa_density_decreases_over_hal_altitude_envelope(self):
        self.assertGreater(isa_density_kg_m3(3_000), isa_density_kg_m3(10_000))
        self.assertGreater(isa_density_kg_m3(10_000), 0.0)

    def test_valid_design_meets_hal_mass_and_payload_constraints(self):
        result = evaluate_design(
            HALConstraints(),
            SeriesHybridDesign(
                battery_kwh=34.0,
                fuel_kg=145.0,
                rotor_disk_area_m2=45.0,
                engine_load_target=0.92,
                cruise_altitude_m=6_000.0,
                loiter_speed_mps=51.0,
            ),
            loiter_seconds=3_600.0,
        )
        self.assertLessEqual(result.takeoff_mass_kg, 1_000.0)
        self.assertEqual(result.payload_kg, 200.0)
        self.assertTrue(result.completed_all_required_phases)

    def test_vtol_peak_is_met_by_generator_and_battery_together(self):
        result = evaluate_design(
            HALConstraints(),
            SeriesHybridDesign(
                battery_kwh=34.0,
                fuel_kg=145.0,
                rotor_disk_area_m2=45.0,
                engine_load_target=0.92,
                cruise_altitude_m=6_000.0,
                loiter_speed_mps=51.0,
            ),
            loiter_seconds=0.0,
        )
        takeoff = result.phases[0]
        self.assertGreater(takeoff.battery_peak_kw, 0.0)
        self.assertLessEqual(takeoff.battery_peak_kw, result.battery_max_discharge_kw)
        self.assertGreater(takeoff.required_bus_peak_kw, 60.0)


if __name__ == "__main__":
    unittest.main()
