"""监听异常必须进入可观察状态，且停止时关闭课程连接。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests
from Scripts.Monitor import monitor
from Scripts.Utils import get_initial_data


class MonitorStateTests(unittest.TestCase):
    def make_ui(self):
        config = get_initial_data()
        config['sessionid'] = 'test'
        return SimpleNamespace(config=config, is_active=True, state_signal=Mock(), add_message_signal=Mock(), account_id='a')

    @patch('Scripts.Monitor.get_on_lesson', side_effect=requests.Timeout('slow'))
    def test_non_connection_error_is_shown_and_stop_is_responsive(self, get_lessons):
        ui = self.make_ui()
        def stop(_):
            ui.is_active = False
        with patch('Scripts.Monitor.time.sleep', side_effect=stop):
            monitor(ui)
        states = [call.args[0] for call in ui.state_signal.emit.call_args_list]
        self.assertTrue(any(item.get('state') == 'reconnecting' for item in states))
        self.assertEqual(states[-1]['state'], 'stopped')

    @patch('Scripts.Monitor.get_on_lesson', side_effect=PermissionError('expired'))
    def test_expired_login_is_distinct_from_network_failure(self, get_lessons):
        ui = self.make_ui()
        monitor(ui)
        self.assertFalse(ui.is_active)
        states = [call.args[0] for call in ui.state_signal.emit.call_args_list]
        self.assertTrue(any(item.get('kind') == 'account' and item.get('status') == 'expired' for item in states))
        self.assertEqual(states[-1]['state'], 'expired')

    @patch('Scripts.Monitor.get_on_lesson', return_value=[])
    def test_empty_course_scan_does_not_claim_push_listener_is_ready(self, get_lessons):
        ui = self.make_ui()
        with patch('Scripts.Monitor.time.sleep', side_effect=lambda _: setattr(ui, 'is_active', False)):
            monitor(ui)
        states = [call.args[0] for call in ui.state_signal.emit.call_args_list]
        self.assertTrue(any(item.get('state') == 'waiting' for item in states))
        self.assertFalse(any(item.get('state') == 'listening' for item in states))
