"""Keep exported companion requirements aligned with the separately built mod."""

import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from pixelheart_core.interior_runtime import (
    INTERIORS_MOD_ID, INTERIORS_MIN_VERSION, INTERIORS_MIN_SMAPI_VERSION,
    minimum_interiors_version,
)


class InteriorRuntimeCompatibilityTests(unittest.TestCase):
    def test_export_contract_matches_the_tracked_companion_build(self):
        runtime = Path(__file__).resolve().parents[1] / "runtime/Pixelheart.Interiors"
        manifest = json.loads((runtime / "manifest.json.in").read_text(encoding="utf-8"))
        project = ET.parse(runtime / "Pixelheart.Interiors.csproj").getroot()
        self.assertEqual(INTERIORS_MOD_ID, manifest["UniqueID"])
        self.assertEqual(INTERIORS_MIN_VERSION, manifest["Version"])
        self.assertEqual(INTERIORS_MIN_VERSION, project.findtext("PropertyGroup/Version"))
        self.assertEqual(INTERIORS_MIN_SMAPI_VERSION, manifest["MinimumApiVersion"])

    def test_required_stable_version_raises_older_and_prerelease_minima(self):
        for specified in ("", "0.0.1", "0.1", "0.1.99", "0.1.99+build", "0.2-beta", "0.2.0-beta.1", "٠.١.٠"):
            with self.subTest(specified=specified):
                self.assertEqual(minimum_interiors_version(specified), "0.2.0")

    def test_equal_and_higher_versions_keep_authored_spelling_and_metadata(self):
        for specified in ("0.2", "0.2.0", "0.2.0+build", "0.2.0+build-with-hyphen",
                          "0.2.1-alpha", "0.3.0-beta", "0.10.0", "1.0.0-beta"):
            with self.subTest(specified=specified):
                self.assertEqual(minimum_interiors_version(specified), specified)

    def test_long_numeric_components_do_not_need_unbounded_integer_conversion(self):
        self.assertEqual(minimum_interiors_version("0." + "0" * 5000 + "1.0"), "0.2.0")


if __name__ == "__main__":
    unittest.main()
