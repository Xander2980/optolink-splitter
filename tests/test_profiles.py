import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from advanced_profiles import Catalog

class CatalogTests(unittest.TestCase):
    def test_real_catalog(self):
        c=Catalog()
        self.assertGreater(len(c.lists["VScotHO1_72"]),800)
        self.assertEqual(c.match("20cb0148")["name"],"VScotHO1_72")
        self.assertEqual(c.match("20cb0159")["name"],"VScotHO1_72")
        self.assertIsNone(c.match("20cb015a"))
        self.assertIsNone(c.match("20cb0147"))

    def test_additional_list(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/"other.tsv").write_text("Model\tParameter\tAddress\tConversion\tReadWrite\tByteLength\nOther\tTemp\t0x0100\tDiv10\tR\t2\n")
            self.assertEqual(Catalog(p).lists["Other"][0]["length"],2)

    def test_exact_mapping(self):
        c=Catalog()
        p=SimpleNamespace(datapoint_metadata={"flammenstatus":{"addr":0x55de,"len":1,"scale_type":1,"list_index":0},"kesseltemperatur":{"addr":0x0802,"len":2,"scale_type":1,"list_index":1}},items=[("a",),("a",)],cycle_groups={"a":1})
        self.assertEqual(c.resolve(c.match("20cb0148"),p),{"burner":"flammenstatus"})
