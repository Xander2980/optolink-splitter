import unittest
from types import SimpleNamespace
from advanced_runtime import AdvancedRuntime
from advanced_config import AdvancedConfig
from advanced_diagnostics import Diagnostics

class CatalogRuntimeTests(unittest.TestCase):
    def test_burner_state_and_no_write_grant(self):
        a=AdvancedRuntime({})
        p=SimpleNamespace(datapoint_metadata={"flammenstatus":{"addr":0x55de,"len":1,"scale_type":1,"list_index":0}},items=[("a",)],cycle_groups={"a":1})
        a.apply_catalog_profile("20cb0148",p)
        self.assertEqual(a.diag.heating_names,{"burner":"flammenstatus"})
        self.assertEqual(a.diag.burner_value(11),1)
        self.assertEqual(a.diag.burner_value(0),0)
        self.assertIsNone(a.diag.burner_value(3))
        self.assertIsNone(a.diag.profile)
        a.apply_catalog_profile("20cb015a",p)
        self.assertEqual(a.diag.heating_names,{})

    def test_manual_override(self):
        a=Diagnostics(AdvancedConfig.parse({"heating_names":{"burner":"manual"},"burner_states":{"3":1}}))
        self.assertEqual(a.burner_value(3),1)
        self.assertIsNone(a.burner_value(11))
