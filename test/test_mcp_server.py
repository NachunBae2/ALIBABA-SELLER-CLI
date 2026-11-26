import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch


PATH = Path(__file__).resolve().parents[1] / 'accio' / 'gateway-server' / 'mcp_server.py'
SPEC = importlib.util.spec_from_file_location('seller_mcp_server', PATH)
MCP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MCP)


def body(answer):
    return json.loads(answer['content'][0]['text'])


class McpCoverage(unittest.TestCase):
    def test_tools_list_has_each_cli_group_and_async_result(self):
        tools = MCP.dispatch({'method': 'tools/list'})['tools']
        names = {tool['name'] for tool in tools}
        self.assertEqual(names, {'seller_central_read', 'seller_cli',
                                'seller_ads_locked', 'seller_central_result'})
        programs = next(t for t in tools if t['name'] == 'seller_cli')['inputSchema']['properties']['program']['enum']
        self.assertEqual(set(programs), {'data', 'inquiry', 'manage', 'accio', 'ads'})

    def test_ads_write_and_campaign_create_are_exit_69_without_execution(self):
        for command in ('campaign create', 'campaign set-budget'):
            answer = MCP.dispatch({'method': 'tools/call', 'params': {
                'name': 'seller_ads_locked',
                'arguments': {'command': command, 'account': 'fixture', 'confirm': True}}})
            self.assertTrue(answer['isError'])
            self.assertEqual(body(answer)['rc'], 69)
            self.assertEqual(body(answer)['reason'], 'ads_write_locked')

    def test_write_groups_require_literal_confirm_true(self):
        for program, command in (('manage', 'products hide'), ('inquiry', 'reply'),
                                 ('inquiry', 'flow'), ('data', 'auth refresh')):
            answer = MCP.grouped_cli({'program': program, 'command': command,
                                      'account': 'fixture', 'confirm': False})
            self.assertTrue(answer['isError'])
            self.assertEqual(body(answer)['reason'], 'confirm_true_required')

    def test_ads_group_is_locked_before_cli_lookup(self):
        answer = MCP.grouped_cli({'program': 'ads', 'command': 'campaign create',
                                  'account': 'fixture', 'confirm': True})
        self.assertEqual(body(answer)['rc'], 69)


if __name__ == '__main__':
    unittest.main()
