import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib import request

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from andie_backend.autonomy.memory_store import MemoryStore
except ModuleNotFoundError:
    from autonomy.memory_store import MemoryStore


class EffectivenessLoadPhase5JTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.memory = MemoryStore(str(Path(self.temp_dir.name) / 'skill_memory.json'))
        self.process = psutil.Process()

    def _seed(self, count_per_scope: int = 30):
        scopes = [
            ('portfolio_escalation', 'mission_critical', 'media_ops', [0.93, 0.90, 0.88]),
            ('portfolio_escalation', 'balanced', 'media_ops', [0.17, 0.21, 0.19]),
            ('workflow_recovery', 'mission_critical', 'trading_ops', [0.54, 0.51, 0.49]),
        ]
        for intent_type, governance_profile, portfolio_group, scores in scopes:
            for index in range(count_per_scope):
                self.memory.record_effectiveness_trend(
                    intent_type=intent_type,
                    governance_profile=governance_profile,
                    portfolio_group=portfolio_group,
                    effectiveness_score=scores[index % len(scores)],
                )

    def test_sustained_write_rollup_and_memory_growth_are_bounded(self):
        start_rss = self.process.memory_info().rss
        write_start = time.perf_counter()
        self._seed(count_per_scope=30)
        write_elapsed = time.perf_counter() - write_start

        rollup_start = time.perf_counter()
        summary = self.memory.get_effectiveness_summary()
        media_rollup = self.memory.get_effectiveness_portfolio_rollup('media_ops')
        mission_rollup = self.memory.get_effectiveness_governance_rollup('mission_critical')
        rollup_elapsed = time.perf_counter() - rollup_start

        rss_growth = max(0, self.process.memory_info().rss - start_rss)

        self.assertEqual(summary['registry_entries'], 3)
        self.assertGreater(media_rollup['window_90d']['sample_count'], 0)
        self.assertGreater(mission_rollup['window_90d']['sample_count'], 0)
        self.assertLess(write_elapsed, 8.0, f'write_elapsed={write_elapsed:.4f}s')
        self.assertLess(rollup_elapsed, 1.0, f'rollup_elapsed={rollup_elapsed:.4f}s')
        self.assertLess(rss_growth, 64 * 1024 * 1024, f'rss_growth={rss_growth}')

    def test_live_replay_round_trip_is_bounded(self):
        base_url = 'http://127.0.0.1:8010'
        execution_id = f'phase5j-load-{int(time.time())}'
        payload = {
            'execution_id': execution_id,
            'skill': 'restart_server_safe',
            'result': 'success',
            'context_key': 'hls',
            'source': 'live',
            'record_execution': False,
            'intent_type': 'portfolio_escalation',
            'governance_profile': 'mission_critical',
            'portfolio_group': 'media_ops',
            'effectiveness_score': 0.91,
        }
        post_start = time.perf_counter()
        req = request.Request(
            base_url + '/autonomy/outcome',
            data=json.dumps(payload).encode('utf-8'),
            headers={'content-type': 'application/json'},
            method='POST',
        )
        with request.urlopen(req, timeout=15) as resp:
            outcome = json.loads(resp.read().decode('utf-8'))
        post_elapsed = time.perf_counter() - post_start

        replay_start = time.perf_counter()
        with request.urlopen(base_url + f'/api/replay/{execution_id}', timeout=15) as resp:
            replay = json.loads(resp.read().decode('utf-8'))
        replay_elapsed = time.perf_counter() - replay_start

        event_types = {str(event.get('type') or event.get('to_state') or '') for event in (replay.get('events') or [])}

        self.assertEqual(outcome['status'], 'ok')
        self.assertTrue(replay.get('found'))
        self.assertIn('coordinator.effectiveness_baseline_updated', event_types)
        self.assertIn('coordinator.effectiveness_trend_updated', event_types)
        self.assertLess(post_elapsed, 5.0, f'post_elapsed={post_elapsed:.4f}s')
        self.assertLess(replay_elapsed, 5.0, f'replay_elapsed={replay_elapsed:.4f}s')


if __name__ == '__main__':
    unittest.main()
