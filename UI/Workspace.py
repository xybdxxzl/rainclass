"""课堂题目和答案工作区。后台线程仅通过 Qt 信号发送数据。"""
import html
import math
import threading
import time
from PyQt5 import QtCore, QtWidgets
from Scripts.LLM import ask_llm, route_config, strip_html, has_images, stream_explanation
from Scripts.Providers import PRESETS


class Workspace(QtWidgets.QWidget):
    recomputed = QtCore.pyqtSignal(object, object, int)
    failed = QtCore.pyqtSignal(str)
    detail_chunk = QtCore.pyqtSignal(object, str)
    detail_done = QtCore.pyqtSignal(object, object)
    detail_error = QtCore.pyqtSignal(object, str)

    def __init__(self, ui):
        super().__init__(ui.Window)
        self.ui = ui
        self.reviews = []
        self.active = None
        self.pending_question = None
        self.count = self.calls = self.token_total = self.usage_missing = 0
        self.latency_total = 0
        self.cost = 0.0
        self.cost_missing = False
        self.recomputing = False
        self.streaming = False
        self.detail_review = None
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        status = QtWidgets.QHBoxLayout()
        self.connection = QtWidgets.QLabel('● 账号状态待确认')
        self.course = QtWidgets.QLabel('课程：暂无')
        self.course.setTextFormat(QtCore.Qt.PlainText)
        self.course.setToolTip('可在课程列表查看所有正在监听的课程')
        status.addWidget(self.connection)
        status.addWidget(self.course, 1)
        self.metrics = QtWidgets.QLabel('已答 0 题 · 延迟 -- · Token -- · 估算 ¥--')
        status.addWidget(self.metrics)
        outer.addLayout(status)
        controls = QtWidgets.QHBoxLayout()
        self.auto = QtWidgets.QCheckBox('自动提交')
        self.auto.setToolTip('关闭后新题只生成答案；现有待答停止自动提交')
        controls.addWidget(self.auto)
        controls.addWidget(QtWidgets.QLabel('当前模型'))
        self.models = QtWidgets.QComboBox()
        self.models.setMinimumWidth(170)
        self.models.setMaximumWidth(230)
        controls.addWidget(self.models, 1)
        self.latency = QtWidgets.QLabel('API 延迟 --')
        controls.addWidget(self.latency)
        self.courses_btn = QtWidgets.QToolButton()
        self.courses_btn.setText('课程列表')
        self.courses_btn.setCheckable(True)
        controls.addWidget(self.courses_btn)
        outer.addLayout(controls)
        self.courses_btn.toggled.connect(ui.Table.setVisible)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        outer.addWidget(self.splitter, 1)
        left = QtWidgets.QGroupBox('实时题目')
        ll = QtWidgets.QVBoxLayout(left)
        self.pending = QtWidgets.QComboBox()
        self.pending.addItem('暂无待核对题目', None)
        ll.addWidget(self.pending)
        self.countdown = QtWidgets.QLabel('等待课堂题目')
        ll.addWidget(self.countdown)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(7)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        ll.addWidget(self.progress)
        self.question = QtWidgets.QTextBrowser()
        self.question.setPlainText('开始监听后，题干和选项会显示在这里。\n\n幻灯片缩略图需等待接口验证。')
        ll.addWidget(self.question, 1)
        self.image_hint = QtWidgets.QLabel('幻灯片缩略图：接口待验证')
        self.image_hint.setWordWrap(True)
        ll.addWidget(self.image_hint)
        self.splitter.addWidget(left)
        right_host = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_host)
        right_layout.setContentsMargins(0,0,0,0)
        right_layout.setSpacing(6)
        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumHeight(0)
        right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        right = QtWidgets.QGroupBox('AI 建议')
        rl = QtWidgets.QVBoxLayout(right)
        self.answer = QtWidgets.QLabel('等待答案')
        self.answer.setTextFormat(QtCore.Qt.PlainText)
        self.answer.setWordWrap(True)
        self.answer.setStyleSheet('font-size: 18pt; font-weight: 600; color: #2856b8;')
        rl.addWidget(self.answer)
        self.confidence = QtWidgets.QLabel('置信度 --')
        self.confidence.setToolTip('模型自评，仅供参考，不代表实际正确率')
        rl.addWidget(self.confidence)
        self.explanation = QtWidgets.QTextBrowser()
        self.explanation.setPlainText('模型解析会显示在这里。')
        self.explanation.setMinimumHeight(65)
        self.explanation.setMaximumHeight(95)
        rl.addWidget(self.explanation)
        self.detail_toggle = QtWidgets.QToolButton()
        self.detail_toggle.setText('展开解析与追问')
        self.detail_toggle.setCheckable(True)
        rl.addWidget(self.detail_toggle)
        self.detail_panel = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(self.detail_panel)
        dl.setContentsMargins(0,0,0,0)
        self.detail_output = QtWidgets.QPlainTextEdit()
        self.detail_output.setReadOnly(True)
        self.detail_output.setMaximumHeight(110)
        self.detail_output.setPlaceholderText('此处流式显示模型生成的解题说明。')
        dl.addWidget(self.detail_output)
        detail_row = QtWidgets.QHBoxLayout()
        self.detail_question = QtWidgets.QLineEdit()
        self.detail_question.setPlaceholderText('可输入追问；留空生成详细解析')
        self.detail_send = QtWidgets.QPushButton('请求解析')
        self.detail_send.setToolTip('会额外调用当前模型并产生费用，不会修改答案。')
        detail_row.addWidget(self.detail_question, 1)
        detail_row.addWidget(self.detail_send)
        dl.addLayout(detail_row)
        rl.addWidget(self.detail_panel)
        self.detail_panel.hide()
        self.detail_toggle.toggled.connect(self.detail_panel.setVisible)
        rl.addStretch()
        self.submit = QtWidgets.QPushButton('填入并提交')
        self.submit.setObjectName('primary')
        self.submit.setStyleSheet('QPushButton { background: #3367d6; color: white; border: none; border-radius: 7px; padding: 8px 16px; } QPushButton:disabled { background: #dce3ee; color: #8b98ab; }')
        self.edit = QtWidgets.QPushButton('修改答案')
        self.only = QtWidgets.QPushButton('仅勾选')
        self.only.setToolTip('暂停本题的自动提交，在助手内选择答案；不会修改雨课堂页面。')
        self.recalculate = QtWidgets.QPushButton('换模型重算')
        self.recalculate.setToolTip('使用顶部选择的当前模型重新请求，原倒计时继续运行。')
        right_scroll.setWidget(right)
        right_layout.addWidget(right_scroll, 1)
        actions = QtWidgets.QGridLayout()
        actions.setContentsMargins(0,0,0,0)
        actions.setSpacing(6)
        for index, button in enumerate((self.submit, self.edit, self.only, self.recalculate)):
            actions.addWidget(button, index // 2, index % 2)
            button.setEnabled(False)
        right_layout.addLayout(actions)
        self.splitter.addWidget(right_host)
        self.splitter.setSizes([380, 340])
        self.pending.currentIndexChanged.connect(self.select_review)
        self.auto.toggled.connect(self.toggle_auto)
        self.models.currentIndexChanged.connect(self.select_model)
        self.submit.clicked.connect(self.submit_current)
        self.edit.clicked.connect(self.edit_current)
        self.only.clicked.connect(self.pause_current)
        self.recalculate.clicked.connect(self.recalculate_current)
        self.recomputed.connect(self.apply_recomputed)
        self.failed.connect(self.recompute_failed)
        self.detail_chunk.connect(self.show_detail_chunk)
        self.detail_done.connect(self.finish_detail)
        self.detail_error.connect(self.fail_detail)
        self.detail_send.clicked.connect(self.request_detail)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(200)

    def load_config(self):
        cfg = self.ui.config
        self.auto.blockSignals(True)
        self.auto.setChecked(cfg.get('auto_submit', True))
        self.auto.blockSignals(False)
        llm = cfg.get('llm_config', {})
        self.models.blockSignals(True)
        self.models.clear()
        self.models.addItem(llm.get('model') or '未配置模型', None)
        current = (llm.get('base_url'), llm.get('model'))
        for model in dict.fromkeys(llm.get('model_choices') or []):
            if isinstance(model, str) and model and model != current[1]:
                self.models.addItem(model, ('model', model))
        for index, profile in llm.get('profiles', {}).items():
            if not isinstance(profile, dict) or not profile.get('api_key') or not profile.get('base_url') or not profile.get('model'):
                continue
            if (profile['base_url'], profile['model']) == current:
                continue
            try:
                name = PRESETS[int(index)][0]
            except (ValueError, IndexError, TypeError):
                name = '自定义'
            self.models.addItem('%s · %s' % (name, profile['model']), ('profile', index))
        self.models.blockSignals(False)

    def toggle_auto(self, checked):
        self.ui.config['auto_submit'] = checked
        if not checked:
            for review in self.reviews:
                review.pause_auto()
        self.ui.save_runtime_config()
        self.ui.add_message_signal.emit('自动提交%s；%s' % ('已开启' if checked else '已关闭', '对新题生效' if checked else '待答题目已暂停自动提交'), 0)

    def select_model(self, index):
        key = self.models.itemData(index)
        if key is None:
            return
        llm = self.ui.config.get('llm_config', {})
        if key[0] == 'model':
            previous = llm.get('model', '')
            choices = llm.setdefault('model_choices', [])
            if previous and previous not in choices:
                choices.append(previous)
            llm['model'] = key[1]
            llm['pricing'] = {}  # 每个模型价格不同，需在设置中重新确认。
            llm['structured_mode'] = 'auto'
            self.ui.save_runtime_config()
            self.load_config()
            self.ui.add_message_signal.emit('已切换模型：%s；费用单价需重新设置' % llm['model'], 0)
            return
        profiles = llm.get('profiles', {})
        profile = profiles.get(str(key[1]))
        if not profile:
            return
        current = {k: llm.get(k) for k in ('base_url','api_key','model','timeout','vision_model','model_choices','system_prompt','pricing','structured_mode') if k in llm}
        old_index = next((str(i) for i,p in enumerate(PRESETS) if p[1] and p[1].rstrip('/') == str(current.get('base_url','')).rstrip('/')), '0')
        profiles[old_index] = current
        for key in ('base_url','api_key','model','timeout'):
            llm[key] = profile.get(key, 30 if key == 'timeout' else '')
        for key in ('vision_model','model_choices','system_prompt','pricing'):
            llm[key] = profile.get(key, {} if key == 'pricing' else [] if key == 'model_choices' else '')
        llm['structured_mode'] = profile.get('structured_mode', 'auto')
        self.ui.save_runtime_config()
        self.load_config()
        self.ui.add_message_signal.emit('已切换模型：%s' % llm.get('model', ''), 0)

    def show_question(self, data):
        if 'started' in data:
            self.active = None
            self.pending_question = data
            self.pending.blockSignals(True)
            self.pending.setCurrentIndex(-1)
            self.pending.blockSignals(False)
        problem = data['problem']
        text = strip_html(problem.get('body') or problem.get('content') or problem.get('title') or '')[0]
        lines = [data['course'], '', text]
        for i, option in enumerate(problem.get('options') or []):
            if isinstance(option, dict):
                key, value = option.get('key') or chr(65+i), option.get('value') or option.get('content') or ''
            else:
                key, value = chr(65+i), option
            lines.append('%s. %s' % (key, strip_html(value)[0]))
        self.question.setPlainText('\n'.join(lines))
        self.image_hint.setText('图片题：请在雨课堂查看原图核对' if has_images(problem) else '幻灯片缩略图：接口待验证')
        self.answer.setText(data.get('status', '正在请求模型…'))
        self.explanation.setPlainText('')

    def add_review(self, review):
        self.reviews.append(review)
        self.pending.blockSignals(True)
        if self.pending.count() == 1 and self.pending.itemData(0) is None:
            self.pending.clear()
        self.pending.addItem('%s · %s' % (review.course, review.result.get('question_type', '待核对')), review)
        self.pending.setCurrentIndex(self.pending.count()-1)
        self.pending.blockSignals(False)
        self.select_review(self.pending.currentIndex())

    def select_review(self, index):
        review = self.pending.itemData(index)
        self.active = review
        if review:
            self.pending_question = None
            self.show_question({'course': review.course, 'problem': review.problem, 'type': review.problemtype})
            self.show_result(review)
            if review is not self.detail_review:
                self.detail_output.clear()
        self.refresh()

    def show_result(self, review):
        self.answer.setText('推荐答案：' + '、'.join(map(str, review.answers)))
        confidence = review.result.get('confidence')
        self.confidence.setText('置信度 %.0f%%（模型自评）' % (confidence*100) if confidence is not None else '置信度：未提供')
        self.explanation.setPlainText(review.result.get('explanation') or '模型未提供解析。')

    def refresh(self):
        review = self.active
        active = bool(review and not review.event.is_set())
        for button in (self.submit, self.edit, self.only):
            button.setEnabled(active)
        self.recalculate.setEnabled(active and not self.recomputing)
        self.detail_send.setEnabled(bool(review) and not self.streaming)
        if not review:
            data = self.pending_question
            if data:
                limit = data.get('limit', -1)
                if limit == -1:
                    self.countdown.setText('题目不限时')
                    self.progress.setValue(100)
                elif isinstance(limit, (int,float)) and limit > 0:
                    remaining = max(0, limit - (time.monotonic() - data['started']))
                    self.countdown.setText('题目剩余：%d 秒' % math.ceil(remaining))
                    self.progress.setValue(max(0,min(100,int(remaining/limit*100))))
            return
        self.show_result(review)
        if not active:
            self.countdown.setText('本题已取消' if review.cancelled else '已交由提交线程处理')
            self.progress.setValue(0)
            return
        now = time.monotonic()
        target = review.deadline if review.auto_submit else review.question_deadline
        remain = max(0, target-now) if target is not None else None
        self.countdown.setText(('自动提交倒计时：%d 秒' if review.auto_submit else '手动提交 · 题目剩余：%d 秒') % math.ceil(remain) if remain is not None else '手动提交 · 不限时')
        total = getattr(review, 'display_duration', 1)
        self.progress.setValue(max(0,min(100,int(remain / max(total,1) * 100))) if remain is not None else 100)

    def submit_current(self):
        if self.active and not self.active.event.is_set():
            if not self.active.valid_input:
                self.ui.add_message_signal.emit('请先在答案窗口补全答案后再提交', 0)
                return
            self.active.finish()

    def edit_current(self):
        if self.active and not self.active.event.is_set():
            self.ui.open_review_dialog(self.active)

    def pause_current(self):
        if self.active and self.active.pause_auto():
            self.ui.open_review_dialog(self.active)
            self.ui.add_message_signal.emit('本题已暂停自动提交，请在助手中勾选或修改，之后手动提交。', 0)

    def recalculate_current(self):
        review = self.active
        if not review or review.event.is_set() or self.recomputing:
            return
        budget = None if review.question_deadline is None else review.question_deadline-time.monotonic()-5
        if budget is not None and budget <= 1:
            self.ui.add_message_signal.emit('题目剩余时间不足，无法重算', 0)
            return
        revision = review.revision
        cfg = route_config(review.problem, self.ui.config.get('llm_config', {}))
        self.recomputing = True
        self.explanation.setPlainText('正在用当前模型重算；原倒计时仍在运行。')
        def run():
            try:
                result = ask_llm(review.problem, review.problemtype, cfg, time_budget=budget, return_details=True)
                if not result:
                    raise ValueError('模型未返回有效答案')
                self.recomputed.emit(review, result, revision)
            except Exception as e:
                self.failed.emit('重算失败：%s' % e)
        threading.Thread(target=run, daemon=True).start()

    def apply_recomputed(self, review, result, revision):
        self.recomputing = False
        self.update_metrics({'result': result})
        if review.update_if_unchanged(result['answers'], result, revision):
            dialog = self.ui.review_dialog_for(review)
            if dialog:
                dialog.set_answers(result['answers'])
            self.ui.add_message_signal.emit('模型重算完成，已更新推荐答案', 0)
        else:
            self.ui.add_message_signal.emit('重算期间答案已修改、格式不一致或题目已结束，未覆盖当前答案', 0)

    def recompute_failed(self, message):
        self.recomputing = False
        self.update_metrics({'unknown': 1})
        self.ui.add_message_signal.emit(message, 0)
        if self.active:
            self.show_result(self.active)

    def request_detail(self):
        review = self.active
        if not review or self.streaming:
            return
        cfg = route_config(review.problem, self.ui.config.get('llm_config', {}))
        question = self.detail_question.text().strip()
        answers = list(review.answers)
        self.detail_review = review
        self.detail_output.clear()
        self.streaming = True
        def run():
            try:
                result = stream_explanation(review.problem, review.problemtype, answers, cfg, question,
                                            lambda chunk: self.detail_chunk.emit(review,chunk))
                self.detail_done.emit(review,result)
            except Exception as e:
                self.detail_error.emit(review,str(e))
        threading.Thread(target=run, daemon=True).start()

    def show_detail_chunk(self, review, chunk):
        if self.active is review and self.detail_review is review:
            self.detail_output.moveCursor(__import__('PyQt5.QtGui',fromlist=['QTextCursor']).QTextCursor.End)
            self.detail_output.insertPlainText(chunk)
            self.detail_output.ensureCursorVisible()

    def finish_detail(self, review, result):
        self.streaming = False
        self.update_metrics({'result':result})
        self.ui.add_message_signal.emit('详细解析已生成；本次请求不会修改答案', 0)

    def fail_detail(self, review, error):
        self.streaming = False
        self.update_metrics({'unknown':1})
        if self.active is review:
            self.detail_output.appendPlainText('\n请求失败：' + error)
        self.ui.add_message_signal.emit('详细解析请求失败：' + error, 0)

    def update_metrics(self, item):
        self.count += item.get('submitted', 0)
        if item.get('unknown'):
            self.cost_missing = True
            self.usage_missing += 1
        result = item.get('result')
        if result:
            ms = result.get('latency_ms')
            if isinstance(ms, (int,float)):
                self.calls += 1
                self.latency_total += ms
                self.latency.setText('API 延迟 %d ms' % ms)
            usage = result.get('usage') or {}
            if isinstance(usage.get('total_tokens'), int):
                self.token_total += usage['total_tokens']
            else:
                self.usage_missing += 1
            pricing = result.get('pricing') or {}
            inp, out = usage.get('prompt_tokens'), usage.get('completion_tokens')
            cached = usage.get('prompt_cache_hit_tokens')
            if cached is None and isinstance(usage.get('prompt_tokens_details'), dict):
                cached = usage['prompt_tokens_details'].get('cached_tokens')
            if pricing.get('enabled') and all(isinstance(x,int) for x in (inp,out,cached)) and 0 <= cached <= inp:
                try:
                    self.cost += ((inp-cached)*float(pricing['input']) + cached*float(pricing['cached']) + out*float(pricing['output']))/1000000
                except (KeyError, TypeError, ValueError):
                    self.cost_missing = True
            else:
                self.cost_missing = True
        latency = '%.1fs' % (self.latency_total/self.calls/1000) if self.calls else '--'
        tokens = str(self.token_total) + ('+' if self.usage_missing else '') if self.calls and (self.token_total or not self.usage_missing) else '--'
        cost = ('¥%.4f' % self.cost) if self.calls and not self.cost_missing else '¥--'
        self.metrics.setText('已答 %d 题 · 平均 %s · Token %s · 估算 %s' % (self.count, latency, tokens, cost))
        self.metrics.setToolTip('仅统计本次运行中成功返回的模型请求；缺少用量或单价时费用不可估算。以服务商账单为准。')
