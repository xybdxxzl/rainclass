import unittest
from unittest.mock import Mock, patch

from Scripts.LLM import _build_user_content, _download_image_b64, _parse_answer, ask_llm, strip_html


class LLMTests(unittest.TestCase):
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
