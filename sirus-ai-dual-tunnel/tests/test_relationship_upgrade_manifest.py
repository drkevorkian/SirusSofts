"""Ensure approved Gentoo upgrade actually ships offline relationship dependencies."""
from pathlib import Path
import re
import unittest


class ManifestTests(unittest.TestCase):
    def test_relationship_modules_shipped_in_upgrade(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "gentoo" / "upgrade-in-place.sh").read_text(encoding="utf-8")
        match = re.search(r"(?m)^research_modules=\(([^)]*)\)", script)
        self.assertIsNotNone(match, "Missing install/backup/rollback module manifest")
        shipped = set(match.group(1).split())
        expected = {
            "continuous_research.py", "manual_relationships.py",
            "manual_relationship_index.py", "manual_relationship_context.py",
            "manual_relationship_compare.py", "relationship_scheduler.py",
            "feasibility_review.py", "research_evidence.py",
        }
        self.assertFalse(expected - shipped, "Modules not included in upgrade: " +
                         ", ".join(sorted(expected - shipped)))
        for module in shipped:
            self.assertTrue((root / "gentoo" / module).is_file(),
                            "Manifest lists missing file " + module)
        self.assertIn("research_modules[@]", script)
        self.assertIn('python3 -m py_compile "$research_src"', script)


if __name__ == "__main__":
    unittest.main()
