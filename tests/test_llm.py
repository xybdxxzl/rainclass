import unittest
import requests
from unittest.mock import Mock, patch

from Scripts.LLM import _build_user_content, _download_image_b64, _parse_answer, ask_llm, strip_html


class LLMTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {'base_url': 'https://api.deepseek.com', 'api_key': 'test',
                    'model': 'deepseek-chat', 'timeout': 5}
        self.response = Mock(status_code=200)
        self.response.json.return_value = {'choices': [{'message': {'content': '{"choices":["A"]}'}}]}

    @patch('Scripts.LLM.time.sleep')
    @patch('Scripts.LLM.requests.post')
    def test_read_timeout_retries_and_deepseek_timeout_floor(self, post, sleep):
        post.side_effect = [requests.ReadTimeout('slow'), self.response]
        retry = Mock()
        self.assertEqual(ask_llm({'body': '题目'}, 1, self.cfg, time_budget=115, on_retry=retry), ['A'])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['timeout'][1], 30)
        retry.assert_called_once()
        sleep.assert_called_once_with(1)

    @patch('Scripts.LLM.time.sleep')
    @patch('Scripts.LLM.requests.post')
    def test_retry_limit(self, post, sleep):
        post.side_effect = requests.ReadTimeout('slow')
        with self.assertRaisesRegex(RuntimeError, '已尝试3次'):
            ask_llm({'body': '题目'}, 1, self.cfg, time_budget=115)
        self.assertEqual(post.call_count, 3)

    @patch('Scripts.LLM.requests.post')
    def test_auth_error_does_not_retry(self, post):
        response = Mock(status_code=401, text='invalid API key')
        response.raise_for_status.side_effect = requests.HTTPError('unauthorized')
        post.return_value = response
        with self.assertRaisesRegex(RuntimeError, 'HTTP 401'):
            ask_llm({'body': '题目'}, 1, self.cfg)
        post.assert_called_once()

    @patch('Scripts.LLM.time.sleep')
    @patch('Scripts.LLM.requests.post')
    def test_server_error_retries(self, post, sleep):
        response = Mock(status_code=503, text='busy')
        response.raise_for_status.side_effect = requests.HTTPError('busy')
        post.side_effect = [response, self.response]
        self.assertEqual(ask_llm({'body': '题目'}, 1, self.cfg), ['A'])
        response.close.assert_called_once()

    @patch('Scripts.LLM.time.monotonic', side_effect=[100, 100, 100, 102])
    @patch('Scripts.LLM.requests.post', side_effect=requests.ReadTimeout('slow'))
    def test_exhausted_budget_stops_retry(self, post, monotonic):
        with self.assertRaisesRegex(RuntimeError, '已尝试1次'):
            ask_llm({'body': '题目'}, 1, self.cfg, time_budget=2)
        post.assert_called_once()

    @patch('Scripts.LLM.requests.post')
    def test_expired_question_does_not_request(self, post):
        with self.assertRaisesRegex(RuntimeError, '预算已耗尽'):
            ask_llm({'body': '题目'}, 1, self.cfg, time_budget=0)
        post.assert_not_called()

    @patch('Scripts.LLM.time.monotonic', side_effect=[100, 100, 104])
    @patch('Scripts.LLM.requests.post')
    def test_late_answer_is_rejected(self, post, monotonic):
        post.return_value = self.response
        with self.assertRaisesRegex(RuntimeError, '超过作答时间预算'):
            ask_llm({'body': '题目'}, 1, self.cfg, time_budget=3)

    def test_html_images_and_linebreaks(self):
        text, images = strip_html('<P>第一行<br>第二行</P><IMG data-src="//cdn/a.png?a=1&amp;b=2">')
        self.assertEqual(text, '第一行\n第二行')
        self.assertEqual(images, ['//cdn/a.png?a=1&b=2'])

    @patch('Scripts.LLM._download_image_b64', return_value='data:image/png;base64,YQ==')
    def test_all_images_keep_option_labels(self, download):
        problem = {'body': '<img src="/body.png">', 'options': [
            {'key': chr(65 + i), 'value': '<img src="/%s.png">' % i}
            for i in range(5)]}
        content = _build_user_content(problem, 1)
        self.assertEqual(download.call_count, 6)
        self.assertEqual([x['text'] for x in content[1:] if x['type'] == 'text'],
                         ['题干图片'] + ['选项%s图片' % chr(65 + i) for i in range(5)])

    @patch('Scripts.LLM._download_image_b64', return_value=None)
    def test_missing_image_stops_answer(self, download):
        with self.assertRaisesRegex(RuntimeError, '图片加载失败'):
            _build_user_content({'images': ['/missing.png']}, 5)

    @patch('Scripts.LLM.requests.get')
    def test_inline_and_relative_images(self, get):
        inline = 'data:image/png;base64,YQ=='
        self.assertEqual(_download_image_b64(inline), inline)
        get.assert_not_called()
        get.return_value = Mock(status_code=200, headers={'Content-Type': 'image/png'}, content=b'a')
        self.assertEqual(_download_image_b64('/test.png'), inline)
        self.assertEqual(get.call_args.args[0], 'https://www.yuketang.cn/test.png')

    def test_answer_formats(self):
        self.assertEqual(_parse_answer('{"answer":"  第一条。\\n第二条。  "}', 5), ['第一条。\n第二条。'])
        self.assertIsNone(_parse_answer('{"answer":[]}', 5))
        self.assertIsNone(_parse_answer('{"answer":" "}', 5))
        self.assertIsNone(_parse_answer('{"blanks":"abc"}', 4))
        self.assertEqual(_parse_answer('{"choices":["c","a"]}', 2), ['A', 'C'])
        self.assertEqual(_parse_answer('{"blanks":["甲","乙"]}', 4), ['甲', '乙'])

    @patch('Scripts.LLM.requests.post')
    def test_short_answer_request(self, post):
        post.return_value = Mock()
        post.return_value.json.return_value = {'choices': [{'message': {'content': '{"answer":"参考答案"}'}}]}
        answer = ask_llm({'body': '简述原理'}, '5', {
            'base_url': 'https://example.test/v1/chat/completions', 'api_key': 'test', 'model': 'test'})
        self.assertEqual(answer, ['参考答案'])
        self.assertEqual(post.call_args.args[0], 'https://example.test/v1/chat/completions')
        self.assertIn('简答题', post.call_args.kwargs['json']['messages'][1]['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
