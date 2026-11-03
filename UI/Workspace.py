"""课堂题目和答案工作区。后台线程仅通过 Qt 信号发送数据。"""
import html
import math
import threading
import time
from PyQt5 import QtCore, QtWidgets
from Scripts.LLM import ask_llm, route_config, strip_html, has_images, stream_explanation
from Scripts.Providers import PRESETS
from UI.Components import ElidingLabel, ToggleSwitch, repolish
from UI.Typography import reading_text


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
        self._last_display = None
        self._editor_review = None
        self._editor_signature = None
        self._editing = False
        self.choice_controls = []
        self.answer_editors = []
        self.monitor_details = {}
        self.lesson_states = {}
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        status_card = QtWidgets.QFrame()
        status_card.setObjectName('surface')
        status_layout = QtWidgets.QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 8, 12, 8)
        status_layout.setSpacing(4)
        status = QtWidgets.QHBoxLayout()
        status.setSpacing(12)
        self.connection = QtWidgets.QLabel('● 账号状态待确认')
        self.connection.setObjectName('connection')
        self.connection.setTextFormat(QtCore.Qt.PlainText)
        self.course = ElidingLabel('暂无正在进行的课程')
        self.course.setToolTip('可展开课程列表查看所有正在监听的课程')
        status.addWidget(self.connection)
        status.addSpacing(6)
        status.addWidget(self.course, 1)
        self.courses_btn = QtWidgets.QToolButton()
        self.courses_btn.setText('课程列表')
        self.courses_btn.setProperty('role', 'link')
        self.courses_btn.setCheckable(True)
        self.courses_btn.toggled.connect(ui.toggle_courses)
        status_layout.addLayout(status)
        controls = QtWidgets.QHBoxLayout()
        model_label = QtWidgets.QLabel('答题模型')
        model_label.setObjectName('muted')
        controls.addWidget(model_label)
        self.models = QtWidgets.QComboBox()
        self.models.setAccessibleName('当前答题模型')
        self.models.setMinimumWidth(150)
        self.models.setMaximumWidth(230)
        self.models.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        model_label.setBuddy(self.models)
        controls.addWidget(self.models, 1)
        controls.addStretch()
        self.auto = ToggleSwitch('自动提交')
        self.auto.setToolTip('关闭后新题只生成答案；现有待答停止自动提交')
        controls.addWidget(self.auto)
        controls.addSpacing(12)
        self.monitor_state = QtWidgets.QLabel('· 等待监听')
        self.monitor_state.setObjectName('monitorState')
        status.insertWidget(1, self.monitor_state)
        controls.addWidget(self.courses_btn)
        status_layout.addLayout(controls)
        self.activity_status = QtWidgets.QLabel('本次尚未收到题目')
        self.activity_status.setObjectName('muted')
        self.activity_status.setWordWrap(True)
        status_layout.addWidget(self.activity_status)
        outer.addWidget(status_card)

        metrics_strip = QtWidgets.QFrame()
        metrics_strip.setObjectName('metricsStrip')
        metric_row = QtWidgets.QHBoxLayout(metrics_strip)
        metric_row.setContentsMargins(0, 0, 0, 2)
        metric_row.setSpacing(8)
        values = []
        for title, value in (('本次已答', '0 题'), ('平均响应', '--'), ('Token 用量', '--'), ('估算费用', '¥--')):
            tile = QtWidgets.QFrame()
            tile_layout = QtWidgets.QHBoxLayout(tile)
            tile_layout.setContentsMargins(10, 4, 10, 4)
            title_label = QtWidgets.QLabel(title)
            title_label.setObjectName('metricCaption')
            number = QtWidgets.QLabel(value)
            number.setObjectName('metricValue')
            number.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            tile_layout.addWidget(title_label)
            tile_layout.addWidget(number, 1)
            metric_row.addWidget(tile, 1)
            values.append(number)
        self.answered_metric, self.latency, self.token_metric, self.metrics = values
        outer.addWidget(metrics_strip)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.splitter.setHandleWidth(10)
        self.splitter.setChildrenCollapsible(False)
        outer.addWidget(self.splitter, 1)
        left = QtWidgets.QFrame()
        left.setObjectName('surface')
        left.setMinimumWidth(280)
        ll = QtWidgets.QVBoxLayout(left)
        ll.setContentsMargins(14, 12, 14, 10)
        ll.setSpacing(8)
        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel('实时题目')
        title.setObjectName('sectionTitle')
        self.question_type = QtWidgets.QLabel('等待新题')
        self.question_type.setObjectName('questionType')
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.question_type)
        ll.addLayout(title_row)
        self.pending = QtWidgets.QComboBox()
        self.pending.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pending.setAccessibleName('切换待核对题目')
        self.pending.addItem('暂无待核对题目', None)
        self.pending.hide()
        ll.addWidget(self.pending)
        self.countdown = QtWidgets.QLabel('收到限时题后显示倒计时')
        self.countdown.setObjectName('timer')
        ll.addWidget(self.countdown)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setAccessibleName('题目或自动提交剩余时间')
        ll.addWidget(self.progress)
        self.question = QtWidgets.QTextBrowser()
        self.question.setObjectName('questionContent')
        self.question.setMinimumHeight(45)
        self.question.setMaximumHeight(105)
        self.question.setHtml('<p style="font-size:16px; font-weight:500; margin-top:16px;">等待课堂的下一道题</p><p style="color:#5d6b7e; font-size:15px;">开始监听后，题干与选项会自动出现在这里。</p>')
        reading_text(self.question)
        ll.addWidget(self.question)
        self.manual_host = QtWidgets.QWidget()
        self.manual_layout = QtWidgets.QVBoxLayout(self.manual_host)
        self.manual_layout.setContentsMargins(0, 0, 0, 0)
        self.manual_layout.setSpacing(3)
        manual_scroll = QtWidgets.QScrollArea()
        manual_scroll.setWidgetResizable(True)
        manual_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        manual_scroll.setWidget(self.manual_host)
        manual_scroll.setMinimumHeight(100)
        manual_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.manual_scroll = manual_scroll
        manual_scroll.hide()
        ll.addWidget(manual_scroll, 1)
        self.manual_hint = QtWidgets.QLabel('题目收到后即可手动作答，无需等待模型。')
        self.manual_hint.setObjectName('muted')
        self.manual_hint.setWordWrap(True)
        ll.addWidget(self.manual_hint)
        self.image_hint = QtWidgets.QLabel()
        self.image_hint.setObjectName('feedHint')
        self.image_hint.setWordWrap(True)
        self.image_hint.hide()
        ll.addWidget(self.image_hint)
        self.splitter.addWidget(left)

        right_host = QtWidgets.QFrame()
        right_host.setObjectName('surface')
        right_host.setMinimumWidth(285)
        right_layout = QtWidgets.QVBoxLayout(right_host)
        right_layout.setContentsMargins(12, 12, 12, 10)
        right_layout.setSpacing(8)
        right_scroll = QtWidgets.QScrollArea()
        self.right_scroll = right_scroll
        right_scroll.setWidgetResizable(True)
        right_scroll.setMinimumHeight(30)
        right_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        right = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)
        heading = QtWidgets.QLabel('答案与解析')
        heading.setObjectName('sectionTitle')
        answer_header = QtWidgets.QHBoxLayout()
        answer_header.addWidget(heading)
        answer_header.addStretch()
        right_layout.addLayout(answer_header)
        hero = QtWidgets.QFrame()
        hero.setObjectName('answerHero')
        hl = QtWidgets.QVBoxLayout(hero)
        hl.setContentsMargins(12, 6, 12, 6)
        hl.setSpacing(1)
        self.answer_caption = QtWidgets.QLabel('推荐答案')
        self.answer_caption.setObjectName('eyebrow')
        self.answer = QtWidgets.QLabel('等待答案')
        self.answer.setTextFormat(QtCore.Qt.PlainText)
        self.answer.setWordWrap(True)
        self.answer.setObjectName('answerValue')
        self.answer.setProperty('answerMode', 'status')
        self.confidence = QtWidgets.QLabel('收到题目后自动生成建议')
        self.confidence.setWordWrap(True)
        self.confidence.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        self.confidence.setObjectName('confidence')
        self.confidence.setToolTip('模型自评，仅供参考，不代表实际正确率')
        hl.addWidget(self.answer_caption)
        hl.addWidget(self.answer)
        hl.addWidget(self.confidence)
        rl.addWidget(hero)
        self.model_status = QtWidgets.QLabel('模型：等待题目')
        self.model_status.setWordWrap(True)
        self.model_status.setTextFormat(QtCore.Qt.PlainText)
        self.model_status.setObjectName('muted')
        self.model_status.setMinimumHeight(30)
        self.model_status.setMaximumHeight(65)
        self.model_status.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Minimum)
        right_layout.addWidget(self.model_status)
        self.use_suggestion = QtWidgets.QPushButton('采用模型建议')
        self.use_suggestion.setEnabled(False)
        self.use_suggestion.setMinimumHeight(32)
        self.use_suggestion.clicked.connect(self.adopt_suggestion)
        self.explanation = QtWidgets.QTextBrowser()
        self.explanation.setObjectName('briefExplanation')
        self.explanation.setPlainText('答案与简短解析将显示在这里。')
        self.explanation.setMinimumHeight(52)
        self.explanation.setMaximumHeight(78)
        self.explanation.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored)
        rl.addWidget(self.explanation)
        self.detail_toggle = QtWidgets.QToolButton()
        self.detail_toggle.setProperty('role', 'link')
        self.detail_toggle.setText('解析与追问')
        self.detail_toggle.setCheckable(True)
        self.detail_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.detail_toggle.setArrowType(QtCore.Qt.RightArrow)
        answer_header.addWidget(self.detail_toggle)
        self.detail_panel = QtWidgets.QWidget()
        dl = QtWidgets.QVBoxLayout(self.detail_panel)
        dl.setContentsMargins(0, 0, 0, 0)
        self.detail_output = QtWidgets.QPlainTextEdit()
        self.detail_output.setReadOnly(True)
        self.detail_output.setMaximumHeight(100)
        self.detail_output.setPlaceholderText('请求后在此显示详细说明')
        dl.addWidget(self.detail_output)
        self.detail_question = QtWidgets.QLineEdit()
        self.detail_question.setAccessibleName('向模型追问')
        self.detail_question.setPlaceholderText('输入追问，或留空请求解析')
        dl.addWidget(self.detail_question)
        self.detail_send = QtWidgets.QPushButton('请求详细解析')
        self.detail_send.setToolTip('会额外调用当前模型并产生费用，不会修改答案。')
        dl.addWidget(self.detail_send)
        rl.addWidget(self.detail_panel)
        self.detail_panel.hide()
        self.detail_toggle.toggled.connect(self.detail_panel.setVisible)
        self.detail_toggle.toggled.connect(self.update_detail_toggle)
        rl.addStretch()
        right_scroll.setWidget(right)
        right_layout.addWidget(right_scroll, 1)
        right_layout.addWidget(self.use_suggestion)
        self.submit = QtWidgets.QPushButton('填入答案并提交')
        self.submit.setObjectName('primary')
        self.submit.setMinimumHeight(34)
        self.edit = QtWidgets.QPushButton('修改答案')
        self.only = QtWidgets.QPushButton('仅勾选')
        self.only.setToolTip('暂停本题自动提交，在助手内选择答案；不会修改雨课堂页面。')
        self.recalculate = QtWidgets.QPushButton('重新计算')
        self.recalculate.setToolTip('使用顶部选择的当前模型重算，原倒计时继续运行。')
        self.retry_question = QtWidgets.QPushButton('重新获取题面')
        self.retry_question.clicked.connect(self.refetch_current)
        self.retry_question.hide()
        right_layout.addWidget(self.retry_question)
        right_layout.addWidget(self.submit)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(5)
        for button in (self.edit, self.only, self.recalculate):
            actions.addWidget(button, 1)
        right_layout.addLayout(actions)
        for button in (self.submit, self.edit, self.only, self.recalculate):
            button.setEnabled(False)
        self.splitter.addWidget(right_host)
        self.splitter.setSizes([400, 340])
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

    def update_detail_toggle(self, expanded):
        self.detail_toggle.setText('收起解析' if expanded else '解析与追问')
        self.detail_toggle.setArrowType(QtCore.Qt.DownArrow if expanded else QtCore.Qt.RightArrow)
        if expanded:
            QtCore.QTimer.singleShot(0, lambda: self.right_scroll.ensureWidgetVisible(self.detail_question))

    def set_timer_urgent(self, urgent):
        for widget in (self.countdown, self.progress):
            if widget.property('urgent') != urgent:
                widget.setProperty('urgent', urgent)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

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
        if data.get('review') is not None:
            self.add_review(data['review'])
            return
        # 兼容旧通知；已经在编辑其他题目时不抢走当前题目。
        if self.active is not None and not self.active.event.is_set():
            return
        if 'started' in data:
            self.active = None
            self.pending_question = data
            self.pending.blockSignals(True)
            self.pending.setCurrentIndex(-1)
            self.pending.blockSignals(False)
        problem = data['problem']
        self.render_question(problem)
        self.image_hint.setVisible(has_images(problem))
        self.image_hint.setText('含图片，请在雨课堂核对原图')
        self.question_type.setText({1:'单选题', 2:'多选题', 4:'填空题', 5:'简答题'}.get(data.get('type'), '待识别'))
        self._last_display = None
        self.confidence.setText('正在生成建议，请稍候')
        self.answer_caption.setText('答案状态')
        self.set_answer_mode('status')
        self.answer.setText(data.get('status', '正在请求模型…'))
        self.explanation.setPlainText('')

    def add_review(self, review):
        if any(item is review or (getattr(item, 'id', None) and getattr(item, 'id', None) == getattr(review, 'id', None)) for item in self.reviews):
            self.refresh()
            return
        keep_current = self.active is not None and not self.active.event.is_set()
        self.reviews.append(review)
        self.pending.blockSignals(True)
        if self.pending.count() == 1 and self.pending.itemData(0) is None:
            self.pending.clear()
        self.pending.addItem(self.review_label(review), review)
        if not keep_current:
            self.pending.setCurrentIndex(self.pending.count()-1)
        self.pending.setVisible(self.pending.count() > 1)
        self.pending.blockSignals(False)
        if not keep_current:
            self.select_review(self.pending.currentIndex())
        self.refresh()

    def review_label(self, review):
        return '%s · %s · %s' % (review.course, getattr(review, 'problem_id', '') or '题目',
                                 getattr(review, 'status', '') or review.result.get('question_type', '待作答'))

    def select_review(self, index):
        review = self.pending.itemData(index)
        self.active = review
        if review:
            self.question_type.setText({1:'单选题', 2:'多选题', 4:'填空题', 5:'简答题'}.get(review.problemtype, '待识别'))
            self.image_hint.setVisible(has_images(review.problem))
            self.image_hint.setText('含图片，请在雨课堂核对原图')
            self.pending_question = None
            self.show_question({'course': review.course, 'problem': review.problem, 'type': review.problemtype})
            self.show_result(review)
            self.build_answer_controls(review)
            if review is not self.detail_review:
                self.detail_output.clear()
        self.refresh()

    def render_question(self, problem, answers=()):
        text = strip_html(problem.get('body') or problem.get('content') or problem.get('title') or '')[0]
        self.question.setMaximumHeight(45 if len(text) < 40 and '\n' not in text else 130)
        paragraphs = '<p style="font-size:15px; margin-bottom:12px;">' + html.escape(text).replace('\n', '<br>') + '</p>'
        interactive = self.active is not None and self.active.problem is problem and self.active.problemtype in (1, 2)
        for i, option in enumerate([] if interactive else problem.get('options') or []):
            if isinstance(option, dict):
                key, value = option.get('key') or chr(65+i), option.get('value') or option.get('content') or ''
            else:
                key, value = chr(65+i), option
            selected = str(key).upper() in answers
            background = '#eef3ff' if selected else '#f5f7fa'
            color = '#315bcb' if selected else '#1e2b3d'
            paragraphs += '<table width="100%%" cellpadding="5" cellspacing="0" style="margin-bottom:6px; font-size:15px;"><tr><td width="22" bgcolor="%s" style="color:%s; font-weight:500;">%s</td><td bgcolor="%s" style="color:%s;">%s</td></tr></table>' % (background, color, html.escape(str(key)), background, color, html.escape(strip_html(value)[0]))
        self.question.setHtml(paragraphs)
        reading_text(self.question)

    def set_answer_mode(self, mode):
        if self.answer.property('answerMode') != mode:
            self.answer.setProperty('answerMode', mode)
            self.answer.setMaximumHeight(48 if mode == 'choice' else 16777215)
            repolish(self.answer)

    def show_result(self, review):
        signature = (id(review), tuple(review.answers), review.revision, getattr(review, 'llm_state', ''),
                     getattr(review, 'status', ''), repr(review.result))
        if self._last_display == signature:
            return
        self._last_display = signature
        text_answer = '、'.join(map(str, review.answers))
        self.set_answer_mode('status' if not text_answer else 'text' if review.problemtype in (4, 5) or len(text_answer) > 12 else 'choice')
        self.answer.setText(text_answer or '尚未选择答案')
        original = review.result.get('answers')
        self.answer_caption.setText('当前作答' if not original else '当前作答 · 已修改' if list(original) != list(review.answers) else '当前作答 · 模型建议')
        confidence = review.result.get('confidence')
        self.confidence.setText((('模型建议：%s · ' % '、'.join(map(str, original))) if original else '') + '置信度 %.0f%%（模型自评）' % (confidence*100) if isinstance(confidence, (int,float)) else
                                ('模型建议：' + '、'.join(map(str, original))) if original else '可立即手动作答')
        self.explanation.setPlainText(review.result.get('explanation') or '模型建议和解析会独立显示在这里。')
        reading_text(self.explanation, pixels=14, line_height=150)
        self.render_question(review.problem, review.answers if review.problemtype in (1, 2) else ())
        self.sync_answer_controls(review)

    def build_answer_controls(self, review):
        signature = (id(review), repr(review.problem), review.problemtype)
        if self._editor_signature == signature:
            self.sync_answer_controls(review)
            return
        self._editor_signature, self._editor_review = signature, review
        self._editing = True
        while self.manual_layout.count():
            item = self.manual_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.choice_controls, self.answer_editors = [], []
        supported = review.problemtype in (1, 2, 4, 5) and getattr(review, 'can_answer', True)
        self.manual_scroll.setVisible(supported)
        if review.problemtype in (1, 2):
            self.choice_group = QtWidgets.QButtonGroup(self.manual_host)
            self.choice_group.setExclusive(review.problemtype == 1)
            for i, option in enumerate(review.problem.get('options') or []):
                key = str(option.get('key') or chr(65+i)) if isinstance(option, dict) else chr(65+i)
                value = option.get('value') or option.get('content') or '' if isinstance(option, dict) else option
                control = QtWidgets.QRadioButton() if review.problemtype == 1 else QtWidgets.QCheckBox()
                control.setText(key + '.')
                control.setAccessibleName('选择答案 ' + key)
                control.setMinimumHeight(24)
                control.setChecked(key.upper() in review.answers)
                self.choice_group.addButton(control)
                self.choice_controls.append((key.upper(), control))
                option_row = QtWidgets.QWidget()
                option_layout = QtWidgets.QHBoxLayout(option_row)
                option_layout.setContentsMargins(0, 0, 0, 0)
                option_layout.setSpacing(4)
                option_layout.addWidget(control, 0, QtCore.Qt.AlignTop)
                option_label = QtWidgets.QLabel(strip_html(value)[0])
                option_label.setTextFormat(QtCore.Qt.PlainText)
                option_label.setWordWrap(True)
                option_label.setMinimumHeight(24)
                option_label.setAccessibleName('选项 ' + key + '：' + strip_html(value)[0])
                option_label.mousePressEvent = lambda event, button=control: button.click()
                option_layout.addWidget(option_label, 1)
                self.manual_layout.addWidget(option_row)
                control.clicked.connect(self.manual_answer_changed)
        elif review.problemtype in (4, 5):
            blanks = review.problem.get('blanks') or []
            count = max(1, len(blanks), len(review.answers)) if review.problemtype == 4 else 1
            for i in range(count):
                label = QtWidgets.QLabel('第 %d 空' % (i+1) if review.problemtype == 4 else '你的答案')
                self.manual_layout.addWidget(label)
                editor = QtWidgets.QPlainTextEdit(str(review.answers[i]) if i < len(review.answers) else '')
                editor.setAccessibleName(label.text())
                editor.setPlaceholderText('可直接填写，无需等待模型')
                editor.setFixedHeight(65 if review.problemtype == 4 else 95)
                reading_text(editor, pixels=15)
                self.answer_editors.append(editor)
                self.manual_layout.addWidget(editor)
                editor.textChanged.connect(self.manual_answer_changed)
        self._editing = False
        self.sync_answer_controls(review)

    def sync_answer_controls(self, review):
        if self._editor_review is not review or self._editing:
            return
        self._editing = True
        for key, control in self.choice_controls:
            control.blockSignals(True)
            control.setChecked(key in review.answers)
            control.blockSignals(False)
        for i, editor in enumerate(self.answer_editors):
            value = str(review.answers[i]) if i < len(review.answers) else ''
            if editor.toPlainText() != value and not editor.hasFocus():
                editor.blockSignals(True)
                editor.setPlainText(value)
                editor.blockSignals(False)
        self._editing = False

    def manual_answer_changed(self):
        review = self.active
        if self._editing or not review or review.event.is_set():
            return
        answers = [key for key, control in self.choice_controls if control.isChecked()] if self.choice_controls else [editor.toPlainText().strip() for editor in self.answer_editors]
        # 部分填写同样保留为草稿，避免填写下一空时清除前一空。
        review.update(answers)
        self.manual_hint.setText('本题已暂停自动提交，请确认后点击提交。')
        self.refresh()

    def adopt_suggestion(self):
        review = self.active
        if review and not review.event.is_set() and review.result.get('answers'):
            review.update(review.result['answers'])
            self._last_display = None
            self.refresh()

    def refetch_current(self):
        review = self.active
        if review and callable(getattr(review, 'retry_question', None)):
            review.retry_question()

    def update_state(self, data):
        if data.get('kind') == 'question':
            review = data.get('review')
            if review and review not in self.reviews:
                self.add_review(review)
            if review is self.active:
                self.build_answer_controls(review)
                self._last_display = None
        elif data.get('kind') == 'monitor':
            for key in ('last_message', 'last_question', 'last_check'):
                if isinstance(data.get(key), (int, float)):
                    self.monitor_details[key] = max(self.monitor_details.get(key, 0) or 0, data[key])
            label = data.get('status') or {'starting':'正在检测课程', 'connecting':'正在连接课堂', 'connected':'课堂已连接', 'listening':'监听中', 'reconnecting':'断线重连中', 'stopped':'已停止', 'error':'监听异常', 'idle':'等待课程'}.get(data.get('state'), data.get('state', '等待监听'))
            lesson_id = data.get('lesson_id')
            if lesson_id:
                existing = self.lesson_states.setdefault(str(lesson_id), {})
                existing.update(data)
                if data.get('state') == 'stopped':
                    self.lesson_states.pop(str(lesson_id), None)
                states = list(self.lesson_states.values())
                connected = sum(item.get('state') in ('connected', 'listening', 'ready') for item in states)
                reconnecting = sum(item.get('state') in ('reconnecting', 'connecting', 'error', 'disconnected') for item in states)
                warnings = sum(item.get('state') == 'warning' for item in states)
                label = '%d 门已连接%s%s' % (connected, ' · %d 门连接中或重连' % reconnecting if reconnecting else '', ' · %d 门需检查' % warnings if warnings else '')
                if not states:
                    label = '正在检测在课课程' if self.ui.is_active else '已停止'
                self.monitor_state.setToolTip('\n'.join('%s：%s' % (item.get('course') or key, item.get('status') or item.get('state')) for key, item in self.lesson_states.items()))
            elif data.get('state') in ('stopped', 'expired'):
                self.lesson_states.clear()
                self.monitor_state.setToolTip('')
            elif self.lesson_states:
                connected = sum(item.get('state') in ('connected', 'listening', 'ready') for item in self.lesson_states.values())
                reconnecting = sum(item.get('state') in ('reconnecting', 'connecting', 'error', 'disconnected') for item in self.lesson_states.values())
                warnings = sum(item.get('state') == 'warning' for item in self.lesson_states.values())
                aggregate = '%d 门已连接%s%s' % (connected, ' · %d 门连接中或重连' % reconnecting if reconnecting else '', ' · %d 门需检查' % warnings if warnings else '')
                label = str(label) + ' · ' + aggregate if data.get('state') in ('error', 'reconnecting', 'warning') else aggregate
            self.monitor_state.setText('· ' + str(label))
        self.refresh()

    @staticmethod
    def format_time(value):
        try:
            return time.strftime('%H:%M:%S', time.localtime(float(value))) if value else '--'
        except (ValueError, TypeError, OSError):
            return str(value or '--')

    def refresh(self):
        details = self.monitor_details
        received = len({getattr(review, 'id', id(review)) for review in self.reviews})
        self.activity_status.setText('已收 %s 题 · 最近课堂消息 %s · 最近题目 %s · 最近检测 %s' % (
            received, self.format_time(details.get('last_message')), self.format_time(details.get('last_question')),
            self.format_time(details.get('last_check'))) if received else
            '本次尚未收到题目 · 最近课堂消息 %s · 最近检测 %s' % (self.format_time(details.get('last_message')), self.format_time(details.get('last_check'))))
        for i in range(self.pending.count()):
            item = self.pending.itemData(i)
            if item:
                self.pending.setItemText(i, self.review_label(item))
        review = self.active
        now = time.monotonic()
        expired = bool(review and review.question_deadline is not None and now >= review.question_deadline)
        active = bool(review and not review.event.is_set() and not expired and getattr(review, 'can_answer', True))
        for button in (self.edit, self.only):
            button.setEnabled(active)
        self.submit.setEnabled(active and review.valid_input)
        self.manual_host.setEnabled(active)
        llm_state = getattr(review, 'llm_state', '') if review else ''
        processing = llm_state in ('pending', 'processing', 'retrying')
        self.recalculate.setEnabled(active and not self.recomputing and not processing)
        self.recalculate.setText('计算中…' if self.recomputing or processing else '重新计算')
        self.use_suggestion.setEnabled(bool(active and review.result.get('answers')))
        self.retry_question.setVisible(bool(review and not getattr(review, 'can_answer', True)))
        self.retry_question.setEnabled(bool(review and not review.event.is_set() and callable(getattr(review, 'retry_question', None))))
        if review:
            state_text = {'not_requested':'尚未请求模型', 'pending':'等待模型处理', 'processing':'模型处理中', 'retrying':'模型正在重试', 'success':'模型已生成建议',
                          'timeout':'模型已超时', 'failed':'模型请求失败', 'disabled':'模型答题未开启', 'cancelled':'本题模型处理已结束'}.get(llm_state, '模型状态待确认')
            started = getattr(review, 'llm_started', None)
            elapsed = max(0, int(now - started)) if isinstance(started, (int,float)) and started else 0
            deadline = getattr(review, 'llm_deadline', None)
            remaining = max(0, math.ceil(deadline-now)) if isinstance(deadline, (int,float)) else None
            timing = ' · 已等待 %d 秒%s' % (elapsed, (' · 最多再等 %d 秒' % remaining) if remaining is not None else '') if processing else ''
            error = getattr(review, 'llm_error', '')
            self.model_status.setText(state_text + timing + ('\n' + error[:150] if error else ''))
            self.model_status.setToolTip(state_text + ('\n' + error if error else ''))
            if not getattr(review, 'can_answer', True):
                self.manual_hint.setText(getattr(review, 'status', '') or '已收到题目，题面尚不完整。')
            elif not review.auto_submit:
                self.manual_hint.setText('可直接选择或填写答案，确认后手动提交。')
            else:
                self.manual_hint.setText('手动修改会暂停本题自动提交。')
        self.detail_send.setEnabled(bool(review) and not self.streaming)
        self.detail_send.setText('正在生成…' if self.streaming else '请求详细解析')
        if not review:
            data = self.pending_question
            if data:
                limit = data.get('limit', -1)
                if limit == -1:
                    self.countdown.setText('题目不限时')
                    self.progress.setValue(100)
                    self.set_timer_urgent(False)
                elif isinstance(limit, (int,float)) and limit > 0:
                    remaining = max(0, limit - (time.monotonic() - data['started']))
                    self.countdown.setText('题目剩余：%d 秒' % math.ceil(remaining))
                    self.progress.setValue(max(0,min(100,int(remaining/limit*100))))
                    self.set_timer_urgent(remaining <= 10)
            return
        if not self.recomputing:
            self.show_result(review)
        if not active:
            states = {'submitted':'提交成功', 'failed':'提交失败', 'unknown':'提交结果未知，请在雨课堂核对', 'cancelled':'本题已取消', 'expired':'题目已截止', 'submitting':'正在提交答案'}
            self.countdown.setText('题目已截止' if expired else states.get(getattr(review, 'submission_state', ''), '本题已取消' if review.cancelled else '正在提交答案'))
            self.progress.setValue(0)
            self.set_timer_urgent(False)
            return
        target = review.deadline if review.auto_submit and review.valid_input else review.question_deadline
        remain = max(0, target-now) if target is not None else None
        self.countdown.setText(('自动提交倒计时：%d 秒' if review.auto_submit else '手动提交 · 题目剩余：%d 秒') % math.ceil(remain) if remain is not None else '手动提交 · 不限时')
        total = getattr(review, 'display_duration', 1)
        self.progress.setValue(max(0,min(100,int(remain / max(total,1) * 100))) if remain is not None else 100)
        self.set_timer_urgent(remain is not None and remain <= 10)

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
        if callable(getattr(review, 'retry_model', None)):
            review.retry_model()
            self.refresh()
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
            self._last_display = None
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
                self.models.setToolTip('最近一次 API 响应：%d ms' % ms)
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
        self.answered_metric.setText('%d 题' % self.count)
        self.latency.setText(latency)
        self.token_metric.setText(tokens)
        self.metrics.setText(cost)
        self.metrics.setToolTip('仅统计本次运行中成功返回的模型请求；缺少用量或单价时费用不可估算。以服务商账单为准。')
