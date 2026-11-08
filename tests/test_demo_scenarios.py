"""Mechanism-level regressions for the six research experiments."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from crossvenue.data import MODEL_ORDER, build_scenarios, model_config, read_jsonl, write_fixtures
from crossvenue.engine import run_replay


class DemonstrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for scenario in build_scenarios():
            for model in MODEL_ORDER:
                cls.runs[(scenario['id'], model)] = run_replay(
                    scenario['events'], model_config(model, scenario['config_overrides']),
                    scenario=scenario['id'], model=model,
                )

    def metrics(self, scenario, model):
        return self.runs[(scenario, model)]['metrics']

    def test_known_cost_and_depth_remove_apparent_opportunities(self):
        self.assertGreater(Decimal(self.metrics('fee_trap', 'screen')['execution_contribution_usdt']), 0)
        self.assertLess(Decimal(self.metrics('fee_trap', 'fees')['execution_contribution_usdt']), 0)
        self.assertEqual(self.metrics('fee_trap', 'guarded')['submitted_pairs'], 0)
        self.assertGreater(Decimal(self.metrics('depth_trap', 'fees')['execution_contribution_usdt']), 0)
        self.assertEqual(self.metrics('depth_trap', 'depth')['submitted_pairs'], 0)

    def test_late_execution_exposes_inventory_and_halt_does_not_erase_it(self):
        late = self.metrics('latency_race', 'guarded')
        partial = self.metrics('partial_leg', 'guarded')
        self.assertEqual(late['single_leg_pairs'], 1)
        self.assertEqual(partial['partial_pairs'], 1)
        self.assertEqual(Decimal(late['ending_extra_btc']), Decimal('.02'))
        self.assertEqual(Decimal(partial['ending_extra_btc']), Decimal('.015'))
        self.assertEqual(late['risk_halts'], 1)
        self.assertEqual(partial['risk_halts'], 1)
        self.assertLess(Decimal(late['execution_contribution_usdt']), 0)

    def test_stale_quotes_and_wrong_venue_assets_cannot_be_treated_as_usable(self):
        stale = self.metrics('stale_quote', 'guarded')
        self.assertEqual(stale['submitted_pairs'], 0)
        self.assertGreater(stale['reject_counts'].get('stale_quote', 0), 0)
        for model in MODEL_ORDER:
            with self.subTest(model=model):
                run = self.runs[('venue_inventory', model)]
                self.assertEqual(run['metrics']['submitted_pairs'], 0)
                self.assertGreater(run['metrics']['reject_counts'].get('insufficient_buy_cash', 0), 0)
                for account in run['final_balances'].values():
                    self.assertGreaterEqual(Decimal(account['usdt']), 0)
                    self.assertGreaterEqual(Decimal(account['btc']), 0)

    def test_fixture_bytes_are_reproducible_and_match_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = write_fixtures(root)
            manifest_bytes = (root/'manifest.json').read_bytes()
            second = write_fixtures(root)
            self.assertEqual(first, second)
            self.assertEqual(manifest_bytes, (root/'manifest.json').read_bytes())
            for entry in first['scenarios']:
                payload = (root/entry['file']).read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), entry['sha256'])
                self.assertEqual(len(read_jsonl(root/entry['file'])), entry['rows'])
                self.assertTrue(all(row['source_kind']=='synthetic' for row in read_jsonl(root/entry['file'])))
                json.loads(manifest_bytes)


if __name__ == '__main__':
    unittest.main()
