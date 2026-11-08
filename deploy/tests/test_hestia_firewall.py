import pathlib
import sys
import unittest
import json

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ubuntu24"))
from firewall_rules import verify_rule


class HestiaFirewallTests(unittest.TestCase):
    def good(self):
        return json.dumps({"2": {"ACTION": "ACCEPT", "IP": "0.0.0.0/0",
                                 "PORT": "8083", "PROTOCOL": "TCP", "COMMENT": "HESTIA"}})

    def test_public_rule_can_be_safely_narrowed(self):
        self.assertEqual(verify_rule(self.good(), "2", "127.0.0.1/32"),
                         "127.0.0.1/32")
        self.assertEqual(verify_rule(self.good(), "2", "203.0.113.5/32"),
                         "203.0.113.5/32")

    def test_refuses_other_rule_and_broad_allowlist(self):
        rule = json.dumps({"2": {"ACTION": "ACCEPT", "IP": "0.0.0.0/0",
                                 "PORT": "22", "PROTOCOL": "TCP"}})
        for payload, rule_id, cidr in [
            (rule, "2", "127.0.0.1/32"),
            (self.good(), "2", "0.0.0.0/0"),
            (self.good(), "2", "1.2.3.0/23"),
            (self.good(), "0", "127.0.0.1/32"),
            (self.good(), "2", "2001:db8::1/128"),
            ("this is not JSON", "2", "127.0.0.1/32"),
        ]:
            with self.assertRaises(ValueError):
                verify_rule(payload, rule_id, cidr)


if __name__ == "__main__":
    unittest.main()
