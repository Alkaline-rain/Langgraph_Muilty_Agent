import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from code_assistant.benchmark_v2 import main, summarize


class V2BenchmarkTests(unittest.TestCase):
    def test_preview_never_calls_model_or_docker(self):
        output = io.StringIO()
        with patch('sys.argv', ['benchmark_v2', '--limit', '1']), redirect_stdout(output), \
             patch('code_assistant.benchmark_v2.DockerSandbox.preflight') as preflight, \
             patch('code_assistant.benchmark_v2.AgentEngine') as engine:
            main()
        self.assertIn('"call_ceiling": 27', output.getvalue())
        preflight.assert_not_called()
        engine.assert_not_called()

    def test_unknown_cost_not_counted_as_zero(self):
        row = {'variant': 'agent_skills', 'success': False, 'calls': None, 'provider_tokens': None,
               'elapsed_ms': 100, 'reason': 'internal_error'}
        result = summarize([row])['agent_skills']
        self.assertIsNone(result['provider_tokens'])
        self.assertIsNone(result['mean_calls_known'])
        self.assertEqual(result['unknown_call_count_runs'], 1)


if __name__ == '__main__':
    unittest.main()
