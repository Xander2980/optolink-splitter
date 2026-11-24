import json
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

    def test_reported_device_and_software_boundaries(self):
        c = Catalog()
        for ident in ("20cb0348", "20cb034f", "20cb0359", "20CB034F"):
            with self.subTest(ident=ident):
                self.assertEqual(c.match(ident)["name"], "VScotHO1_72")
        for ident in ("20cb0347", "20cb035a", "20cb024f", "20cb044f", "20ca034f"):
            with self.subTest(ident=ident):
                self.assertIsNone(c.match(ident))

    def test_invalid_identification_is_not_matched(self):
        c = Catalog()
        for ident in (None, 1234, "", "20cb03", "20cbgg4f", "20cb03+f", "20cb03 f"):
            with self.subTest(ident=ident):
                self.assertIsNone(c.match(ident))

    def test_legacy_combined_version_profiles_remain_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = {"name": "legacy", "device_id": "20cb", "version_min": 328, "version_max": 345}
            (root / "legacy.json").write_text(json.dumps(profile), encoding="utf-8")
            c = Catalog(root)
            self.assertEqual(c.errors, [])
            self.assertEqual(c.match("20cb014f")["name"], "legacy")
            self.assertIsNone(c.match("20cb034f"))

    def test_invalid_split_version_constraints_are_rejected(self):
        valid = {"name": "split", "device_id": "20cb", "hardware_indices": [1, 3],
                 "software_min": 72, "software_max": 89}
        invalid = [{"hardware_indices": value} for value in ([], None, "03", [True], [-1], [256])]
        invalid += [{"software_min": -1}, {"software_min": True}, {"software_min": 90},
                    {"software_max": 256}, {"software_max": "89"}, {"version_min": 328}]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for changes in invalid:
                with self.subTest(changes=changes):
                    (root / "split.json").write_text(json.dumps({**valid, **changes}), encoding="utf-8")
                    c = Catalog(root)
                    self.assertTrue(c.errors)
                    self.assertIsNone(c.match("20cb034f"))
            for missing in ("hardware_indices", "software_min", "software_max"):
                with self.subTest(missing=missing):
                    profile = dict(valid)
                    del profile[missing]
                    (root / "split.json").write_text(json.dumps(profile), encoding="utf-8")
                    self.assertTrue(Catalog(root).errors)

    def test_overlapping_profiles_are_not_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            split = {"name": "split", "device_id": "20cb", "hardware_indices": [1, 3],
                     "software_min": 72, "software_max": 89}
            (root / "split.json").write_text(json.dumps(split), encoding="utf-8")
            duplicate = {**split, "name": "other"}
            (root / "other.json").write_text(json.dumps(duplicate), encoding="utf-8")
            self.assertIsNone(Catalog(root).match("20cb034f"))
            legacy = {"name": "other", "device_id": "20cb", "version_min": 328, "version_max": 345}
            (root / "other.json").write_text(json.dumps(legacy), encoding="utf-8")
            self.assertIsNone(Catalog(root).match("20cb014f"))

    def test_additional_list(self):
        with tempfile.TemporaryDirectory() as temp:
            p=Path(temp)
            (p/"other.tsv").write_text("Model\tParameter\tAddress\tConversion\tReadWrite\tByteLength\nOther\tTemp\t0x0100\tDiv10\tR\t2\n")
            self.assertEqual(Catalog(p).lists["Other"][0]["length"],2)

    def test_exact_mapping(self):
        c=Catalog()
        p=SimpleNamespace(datapoint_metadata={"flammenstatus":{"addr":0x55de,"len":1,"scale_type":1,"list_index":0},"kesseltemperatur":{"addr":0x0802,"len":2,"scale_type":1,"list_index":1}},items=[("a",),("a",)],cycle_groups={"a":1})
        self.assertEqual(c.resolve(c.match("20cb0148"),p),{"burner":"flammenstatus"})
