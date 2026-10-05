import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from engine import Engine

def run(e, name, secs=70):
    e.run_scenario(name)
    for _ in range(secs): e.tick()

def real(e): return [i for i in e.incs if any(not a["noise"] for a in i["al"])]

class T(unittest.TestCase):
    def test_fiber_cut_root_cause_and_auto_fix(self):
        e = Engine(seed=1); run(e, "fiber-cut"); i = real(e)[0]
        self.assertEqual((i["root"]["src"], i["root"]["type"]), ("DWDM1", "LOS"))
        self.assertEqual(i["st"], "Resolved"); self.assertTrue(i["rem"]["auto"])
        self.assertEqual(set(i["imp"]), {"VPN-ACME", "VPN-BANK", "FTTH-AUSTIN"})

    def test_dedup_counts_repeats(self):
        e = Engine(seed=1); [e.ingest("P1", "LINK_DOWN") for _ in range(5)]
        a = [a for a in e.alarms if a["k"] == "P1LINK_DOWN"]
        self.assertEqual((len(a), a[0]["n"]), (1, 5))

    def test_medium_risk_waits_for_approval(self):
        e = Engine(seed=1); run(e, "cell-site", 30); i = real(e)[0]
        self.assertEqual((i["root"]["src"], i["rem"]["st"]), ("CSR1", "Proposed"))
        self.assertTrue(e.approve(i["id"]))
        for _ in range(20): e.tick()
        self.assertEqual(i["st"], "Resolved"); self.assertFalse(i["rem"]["auto"])

    def test_auto_off_never_runs_unapproved(self):
        e = Engine(auto=False, seed=1); run(e, "pon", 30); self.assertEqual(real(e)[0]["rem"]["st"], "Proposed")

    def test_prediction_triggers_proactive_fix(self):
        e = Engine(seed=1); run(e, "optical", 60)
        self.assertGreater(e.risk["DWDM1"]["p"], .7); self.assertEqual(real(e)[0]["st"], "Resolved")

    def test_noise_reduction(self):
        e = Engine(seed=1); run(e, "storm", 20)
        self.assertGreater(e.raw, 50); self.assertLess(len(real(e)), 3)

if __name__ == "__main__": unittest.main()
