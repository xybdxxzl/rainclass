import base64
import threading
import unittest
from contextlib import nullcontext
import requests
from unittest.mock import Mock, patch
from PyQt5.QtCore import QBuffer, QIODevice, Qt
from PyQt5.QtGui import QImage

from Scripts.LLM import _build_user_content, _download_image_b64, _parse_answer, ask_llm, classify_error, strip_html
from Scripts.LLM import ImageLoadError, ImageUnreadableError, has_images
from Scripts.LLM import test_connection as check_connection


def _png(width=32, height=32):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(Qt.blue)
    output = QBuffer()
    output.open(QIODevice.WriteOnly)
    if not image.save(output, 'PNG'):
        raise RuntimeError('测试图片生成失败')
    return bytes(output.data())


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
        self.assertEqual(post.call_args.kwargs['timeout'].read_timeout, 30)
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

    @patch('Scripts.LLM.time.monotonic', side_effect=[100, 100, 100, 104])
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
        png = _png()
        inline = 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')
        self.assertEqual(_download_image_b64(inline), inline)
        get.assert_not_called()
        response = Mock()
        response.iter_content.return_value = iter([png])
        get.return_value = nullcontext(response)
        self.assertEqual(_download_image_b64('/test.png'), inline)
        self.assertEqual(get.call_args.args[0], 'https://www.yuketang.cn/test.png')
        self.assertTrue(get.call_args.kwargs['stream'])

    def test_large_inline_image_is_resized_preserving_aspect_ratio(self):
        inline = 'data:image/png;base64,' + base64.b64encode(_png(2400, 1200)).decode('ascii')
        result = _download_image_b64(inline)
        decoded = QImage.fromData(base64.b64decode(result.split(',', 1)[1]))
        self.assertEqual((decoded.width(), decoded.height()), (1600, 800))
        self.assertGreater(decoded.pixelColor(10, 10).blue(), 240)

    @patch('Scripts.LLM._download_image_b64')
    def test_parallel_image_loading_deduplicates_urls_and_keeps_option_order(self, download):
        start_together = threading.Barrier(2)
        def download_image(url, deadline):
            start_together.wait(2)
            return 'data:image/png;base64,YQ=='
        download.side_effect = download_image
        content = _build_user_content({'body': '<img src="/shared.png">', 'options': [
            {'key': 'A', 'value': '<img src="https://www.yuketang.cn/shared.png">'},
            {'key': 'B', 'value': '<img src="/other.png">'}]}, 1)
        self.assertEqual(download.call_count, 2)
        self.assertEqual([part['text'] for part in content[1:] if part['type'] == 'text'],
                         ['题干图片、选项A图片', '选项B图片'])
        self.assertEqual(sum(part['type'] == 'image_url' for part in content), 2)

    @patch('Scripts.LLM.requests.post')
    @patch('Scripts.LLM._download_image_b64')
    def test_image_stage_timeout_is_distinguished_and_does_not_call_model(self, download, post):
        release = threading.Event()
        download.side_effect = lambda url, deadline: release.wait(2)
        try:
            with self.assertRaisesRegex(RuntimeError, '图片.*超时') as failure:
                ask_llm({'body': '<img src="/slow.png">'}, 1, self.cfg, time_budget=.15)
        finally:
            release.set()
        self.assertEqual(classify_error(failure.exception)[0], 'image')
        post.assert_not_called()

    @patch('Scripts.LLM.time.monotonic', side_effect=[100, 104, 104, 111])
    @patch('Scripts.LLM.requests.post')
    @patch('Scripts.LLM._build_user_content', return_value=[
        {'type': 'text', 'text': '题目'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}])
    def test_qwen_image_request_uses_remaining_budget_and_disables_thinking(self, build, post, monotonic):
        post.return_value = self.response
        config = dict(self.cfg, base_url='https://dashscope.aliyuncs.com/compatible-mode/v1',
                      model='qwen3.8-max', timeout=15)
        self.assertEqual(ask_llm({'body': '<img src="/question.png">'}, 1, config), ['A'])
        request = post.call_args.kwargs
        self.assertEqual(request['timeout'].total, 11)
        self.assertFalse(request['json']['enable_thinking'])

    @patch('Scripts.LLM.randrange', return_value=1)
    @patch('Scripts.LLM.requests.post')
    def test_vision_connection_test_uses_vision_model_and_checks_image_answer(self, post, color):
        config = dict(self.cfg, vision_model='deepseek-flash')
        post.return_value = self.response
        self.assertFalse(check_connection(config, vision=True)['ok'])
        self.response.json.return_value = {'choices': [{'message': {'content': '{"choices":["B"]}'}}]}
        result = check_connection(config, vision=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['model'], 'deepseek-flash')
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'deepseek-flash')
        image = next(part for part in payload['messages'][1]['content'] if part['type'] == 'image_url')
        decoded = QImage.fromData(base64.b64decode(image['image_url']['url'].split(',', 1)[1]))
        self.assertGreater(decoded.pixelColor(10, 10).red(), 240)

    @patch('Scripts.LLM.requests.post')
    @patch('Scripts.LLM._download_image_b64')
    def test_option_src_is_used_for_vision_routing_and_request(self, download, post):
        download.return_value = 'data:image/png;base64,YQ=='
        post.return_value = self.response
        cfg = dict(self.cfg, vision_model='vision-model')
        problem = {'body': '读图选择', 'options': [{'key': 'A', 'images': [{'src': '/a.png'}]}]}
        self.assertTrue(has_images(problem))
        self.assertEqual(ask_llm(problem, 1, cfg, return_details=True)['answers'], ['A'])
        payload = post.call_args.kwargs['json']
        self.assertEqual(payload['model'], 'vision-model')
        self.assertEqual(payload['messages'][1]['content'][1]['text'], '选项A图片')
        image_url = payload['messages'][1]['content'][2]['image_url']['url']
        self.assertEqual(image_url, download.return_value)

    @patch('Scripts.LLM.requests.post')
    def test_missing_image_address_does_not_turn_into_text_only_request(self, post):
        problem = {'body': '读图选择', 'options': [{'key': 'A', 'value': '<img alt="图A">'}]}
        with self.assertRaisesRegex(ImageLoadError, '选项A图片'):
            ask_llm(problem, 1, self.cfg)
        post.assert_not_called()

    @patch('Scripts.LLM.requests.post')
    def test_unreadable_compressed_image_gets_one_retry_with_original(self, post):
        inline = 'data:image/png;base64,' + base64.b64encode(_png(2400, 1200)).decode('ascii')
        unclear = Mock(status_code=200)
        unclear.json.return_value = {
            'choices': [{'message': {'content': '{"image_unreadable":true}'}}],
            'usage': {'total_tokens': 30, 'prompt_tokens': 20, 'completion_tokens': 10,
                      'prompt_cache_hit_tokens': 4},
        }
        self.response.json.return_value['usage'] = {
            'total_tokens': 50, 'prompt_tokens': 40, 'completion_tokens': 10,
            'prompt_tokens_details': {'cached_tokens': 6},
        }
        post.side_effect = [unclear, self.response]
        result = ask_llm({'body': '请选择', 'images': [inline], 'options': ['甲']}, 1, self.cfg, return_details=True)
        self.assertEqual(result['answers'], ['A'])
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result['usage'], {'total_tokens': 80, 'prompt_tokens': 60,
                                          'completion_tokens': 20, 'prompt_cache_hit_tokens': 10})
        images = [call.kwargs['json']['messages'][1]['content'][-1]['image_url']['url'] for call in post.call_args_list]
        self.assertTrue(images[0].startswith('data:image/jpeg;'))
        self.assertEqual(images[1], inline)

    @patch('Scripts.LLM.requests.post')
    def test_distinguishes_unreadable_picture_from_valid_indeterminate_answer(self, post):
        inline = 'data:image/png;base64,' + base64.b64encode(_png()).decode('ascii')
        problem = {'body': '判断是否唯一', 'images': [inline], 'blanks': ['']}
        post.return_value = self.response
        self.response.json.return_value = {'choices': [{'message': {'content': '{"blanks":["无法确定"]}'}}]}
        with self.assertRaises(ImageUnreadableError):
            ask_llm(problem, 4, self.cfg, return_details=True)
        post.assert_called_once()
        self.response.json.return_value = {'choices': [{'message': {
            'content': '{"image_unreadable":false,"blanks":["无法确定"],"explanation":"给定条件允许两组解"}'
        }}]}
        self.assertEqual(ask_llm(problem, 4, self.cfg, return_details=True)['answers'], ['无法确定'])

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
