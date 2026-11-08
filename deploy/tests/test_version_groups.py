"""Run from deploy/: python3 -m unittest discover -s tests -p test_version_groups.py"""
import unittest
from version_groups import project_identity, grouped_projects


class VersionFamilies(unittest.TestCase):
    def test_gentoo_versions(self):
        names = ["drkevorkian/Gentoo-Installer",
                 "drkevorkian/Gentoo-Installer-2",
                 "drkevorkian/Gentoo_InstallerV3"]
        families = grouped_projects([{"full_name": n, "site_visibility": "private"} for n in names])
        self.assertEqual(len(families), 1)
        self.assertTrue(families[0]["grouped"])
        self.assertEqual([x["version_label"] for x in families[0]["versions"]],
                         ["Original / unversioned", "v2", "v3"])

    def test_ai_bridge_versions(self):
        families = grouped_projects([{"full_name": "drkevorkian/AI_BridgeV-2"},
                                     {"full_name": "drkevorkian/AI_Bridge"}])
        self.assertEqual(len(families), 1)
        self.assertEqual(families[0]["key"], "drkevorkian/ai-bridge")

    def test_owner_isolation(self):
        families = grouped_projects([{"full_name": "someone/Gentoo-Installer-2"},
                                     {"full_name": "drkevorkian/Gentoo-Installer"}])
        self.assertEqual(len(families), 2)
        self.assertTrue(all(not x["grouped"] for x in families))

    def test_singletons_remain_visible(self):
        families = grouped_projects([{"full_name": "drkevorkian/worldcom"},
                                     {"full_name": "drkevorkian/Sentinal_trader"}])
        self.assertEqual(len(families), 2)
        self.assertTrue(all(len(x["versions"]) == 1 for x in families))

    def test_visibility_is_independent(self):
        records = [{"full_name": "a/App", "site_visibility": "public", "hosting_requested": 1},
                   {"full_name": "a/App-v2", "site_visibility": "private", "hosting_requested": 0}]
        families = grouped_projects(records)
        self.assertEqual([p["site_visibility"] for p in families[0]["versions"]], ["public", "private"])
        self.assertEqual([p["hosting_requested"] for p in families[0]["versions"]], [1, 0])
        self.assertEqual(records[0].keys(), {"full_name", "site_visibility", "hosting_requested"})

    def test_unrelated_numbers(self):
        self.assertEqual(project_identity("a/foo-2026")[0], "a/foo-2026")
        self.assertEqual(project_identity("a/.528")[0], "a/528")

    def test_invalid_names(self):
        for name in ["", "a/../x", "a//b", "-x", "a/b/c"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                project_identity(name)


if __name__ == "__main__":
    unittest.main()
