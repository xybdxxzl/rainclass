import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from PyQt5 import QtWidgets
from Scripts.LLM import parse_result, route_config, has_images, ask_llm, stream_explanation
from Scripts.AnswerReview import AnswerReview
from Scripts.Utils import get_initial_data

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

class WorkspaceLogicTests(unittest.TestCase):
    def setUp(self):
        self.problem = {'body': '题目', 'options': [{'key':'A','value':'甲'}, {'key':'B','value':'乙'}]}

    def test_structured_result_and_semantic_validation(self):
        result = parse_result('{"choices":["b"],"confidence":0.8,"explanation":"正确"}', 1, self.problem)
        self.assertEqual(result['answers'], ['B'])
        self.assertEqual(result['confidence'], 0.8)
        self.assertIsNone(parse_result('{"choices":["C"]}', 1, self.problem))
        self.assertIsNone(parse_result('{"choices":["A","B"]}', 1, self.problem))
        self.assertIsNone(parse_result('{"choices":["A","A"]}', 2, self.problem))
        self.assertIsNone(parse_result('{"blanks":["甲"]}', 4, {'blanks':['','']}))
        self.assertIsNone(parse_result('{"blanks":[null]}', 4, {}))
        self.assertIsNone(parse_result('{"choices":["A"],"confidence":2}', 1, self.problem)['confidence'])

    def test_image_routing_only_applies_when_image_present(self):
        cfg = {'model':'fast', 'vision_model':'vision', 'pricing':{'enabled':True}}
        self.assertEqual(route_config(self.problem, cfg)['model'], 'fast')
        img = {'body':'题目<img src="/a.png">'}
        self.assertTrue(has_images(img))
        self.assertEqual(route_config(img,cfg)['model'], 'vision')
        self.assertEqual(route_config(img,cfg)['pricing'], {})
        self.assertEqual(cfg['model'], 'fast')

    @patch('Scripts.LLM.requests.post')
    def test_explicit_schema_mode_sends_schema_and_validates_result(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {'choices':[{'message':{'content':'{"choices":["B"],"confidence":0.7}'}}]}
        cfg={'base_url':'https://example.test/v1','api_key':'test','model':'test','structured_mode':'schema'}
        self.assertEqual(ask_llm(self.problem,1,cfg,return_details=True)['answers'],['B'])
        fmt=post.call_args.kwargs['json']['response_format']
        self.assertEqual(fmt['type'],'json_schema')
        self.assertTrue(fmt['json_schema']['strict'])
        self.assertIn('choices',fmt['json_schema']['schema']['required'])

    @patch('Scripts.LLM.requests.post')
    def test_result_contains_usage_and_does_not_accept_invalid_choice(self, post):
        post.return_value = Mock(status_code=200)
        post.return_value.json.return_value = {'choices':[{'message':{'content':'{"choices":["B"],"confidence":0.7,"explanation":"解释"}'}}], 'usage':{'total_tokens':42}}
        cfg={'base_url':'https://api.deepseek.com','api_key':'test','model':'fast'}
        result=ask_llm(self.problem,1,cfg,return_details=True)
        self.assertEqual(result['usage']['total_tokens'],42)
        self.assertEqual(result['answers'],['B'])
        self.assertEqual(post.call_args.kwargs['json']['response_format'], {'type':'json_object'})
        post.return_value.json.return_value['choices'][0]['message']['content']='{"choices":["C"]}'
        self.assertIsNone(ask_llm(self.problem,1,cfg,return_details=True))

    def test_pausing_and_editing_prevent_stale_auto_or_recalculation(self):
        review=AnswerReview('课',self.problem,1,['A'],10)
        self.assertTrue(review.pause_auto())
        self.assertFalse(review.finish_if_auto())
        old=review.revision
        review.mark_invalid()
        self.assertFalse(review.update_if_unchanged(['B'],{},old))
        self.assertFalse(review.valid_input)
        review.update(['B'])
        self.assertTrue(review.valid_input)
        self.assertTrue(review.finish())
        self.assertFalse(review.finish())

    @patch('Scripts.LLM.requests.post')
    def test_streamed_explanation_keeps_chunks_and_usage(self, post):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_lines.return_value = iter([
            b'data: {"choices":[{"delta":{"content":"first"}}]}',
            b'data: {"choices":[{"delta":{"content":" second"}}]}',
            b'data: {"choices":[],"usage":{"total_tokens":12}}', b'data: [DONE]'])
        post.return_value = response
        chunks = []
        result = stream_explanation(self.problem,1,['A'],
                                    {'base_url':'https://api.deepseek.com','api_key':'test','model':'fast'},'',chunks.append)
        self.assertEqual(''.join(chunks),'first second')
        self.assertEqual(result['usage']['total_tokens'],12)
        self.assertTrue(post.call_args.kwargs['stream'])

    def test_ui_pauses_pending_and_handles_missing_usage(self):
        from UI.MainWindow import MainWindow_Ui
        with patch.object(MainWindow_Ui,'check_config',return_value=get_initial_data()), patch.object(MainWindow_Ui,'check_login',return_value=(True,{'name':'测试'})):
            main=QtWidgets.QMainWindow(); ui=MainWindow_Ui(); ui.setupUi(main)
            ui.save_runtime_config=Mock()
            review=AnswerReview('课',self.problem,1,['A'],10,result={'confidence':0.8})
            ui.workspace.add_review(review)
            ui.workspace.auto.setChecked(False)
            self.assertFalse(review.auto_submit)
            self.assertFalse(review.finish_if_auto())
            ui.workspace.update_metrics({'result':{'latency_ms':200,'usage':None}})
            self.assertIn('¥--',ui.workspace.metrics.text())
            review.mark_invalid(); ui.workspace.submit_current(); self.assertFalse(review.event.is_set())
            review.update(['B']); ui.workspace.submit_current(); self.assertTrue(review.event.is_set())
            ui.workspace.show_question({'course':'另一门课','problem':self.problem,'type':1,'started':1})
            ui.workspace.refresh()
            self.assertIsNone(ui.workspace.active)
            self.assertFalse(ui.workspace.submit.isEnabled())
            main.close()

    def test_selecting_alternative_model_clears_stale_pricing(self):
        from UI.MainWindow import MainWindow_Ui
        config = get_initial_data()
        config['llm_config'].update(model='fast', model_choices=['accurate'],
                                    pricing={'enabled':True, 'input':1,'cached':1,'output':1})
        with patch.object(MainWindow_Ui,'check_config',return_value=config), patch.object(MainWindow_Ui,'check_login',return_value=(True,{'name':'测试'})):
            main=QtWidgets.QMainWindow(); ui=MainWindow_Ui(); ui.setupUi(main); ui.save_runtime_config=Mock()
            ui.workspace.models.setCurrentIndex(1)
            self.assertEqual(config['llm_config']['model'],'accurate')
            self.assertEqual(config['llm_config']['pricing'],{})
            self.assertIn('fast',config['llm_config']['model_choices'])
            main.close()
