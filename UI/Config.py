"""分页配置面板：固定保存栏，验证通过后原子保存。"""
import copy
import json
import threading
import datetime
from pathlib import Path
from urllib.parse import urlsplit
from PyQt5 import QtCore, QtGui, QtWidgets
from Scripts.Utils import get_config_path, get_initial_data, resource_path
from Scripts.Providers import PRESETS
from UI.Theme import STYLE, prepare_fonts
from UI.Components import ToggleSwitch


class ConnectionTestSignals(QtCore.QObject):
    completed = QtCore.pyqtSignal(int, object)

SETTINGS_STYLE = '''
QDialog#settings { background: #f5f7fa; }
QLabel#settingsTitle { color: #1e2b3d; font-size: 22px; font-weight: 500; }
QFrame#card { background: #ffffff; border: 1px solid #dce3ec; border-radius: 8px; }
QLabel#fieldLabel { color: #344b6a; font-size: 13px; }
QLabel#error { color: #a7352f; font-size: 12px; }
QFrame#footer { background: #ffffff; border-top: 1px solid #dfe6f0; }
QTabWidget::pane { border: none; }
QListWidget#settingsNav { background: #edf1f7; border: none; padding: 12px 8px; outline: none; font-size: 14px; }
QListWidget#settingsNav::item { padding: 12px 8px; margin-bottom: 6px; border: 1px solid transparent; border-radius: 6px; color: #435c7c; }
QListWidget#settingsNav::item:selected { background: #ffffff; color: #315bcb; border-color: #d5e1fa; font-weight: 500; }
QListWidget#settingsNav::item:focus { border-color: #3563e9; }
QLineEdit[invalid="true"], QComboBox[invalid="true"] { border: 1px solid #bc5749; background: #fff8f7; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { background: #f1f4f8; color: #7b899c; }
QSpinBox::up-button, QSpinBox::down-button { width: 22px; }
'''



class SettingsCombo(QtWidgets.QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(3)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()  # 滚动页面时不会意外切换服务商或模型。


class SettingsSpin(QtWidgets.QSpinBox):
    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class Config_Ui:
    def setupUi(self, Dialog, preview=False):
        self.preview_mode = preview
        prepare_fonts(QtWidgets.QApplication.instance())
        self.dialog, self.loading = Dialog, True
        self.provider_profiles, self.current_provider, self.errors = {}, 0, {}
        self._test_generation = 0
        self._test_running = False
        self._test_signals = ConnectionTestSignals(Dialog)
        self._test_signals.completed.connect(self.finish_connection_test)
        Dialog.setObjectName('settings')
        Dialog.setWindowTitle('设置 · 雨课堂助手')
        Dialog.setWindowIcon(QtGui.QIcon(resource_path('UI/Image/app-icon.ico')))
        Dialog.setStyleSheet(STYLE + SETTINGS_STYLE)
        Dialog.setMinimumSize(500, 380)
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QtCore.QRect(0, 0, 1000, 800)
        Dialog.resize(min(680, available.width() - 40), min(560, available.height() - 60))
        root = QtWidgets.QVBoxLayout(Dialog)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        header = QtWidgets.QWidget()
        head = QtWidgets.QVBoxLayout(header)
        head.setContentsMargins(22, 18, 22, 14)
        title = QtWidgets.QLabel('工作台设置')
        title.setObjectName('settingsTitle')
        head.addWidget(title)
        head.addWidget(self.hint('连接模型、安排提交时间，并管理课堂提醒。'))
        root.addWidget(header)
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.tabBar().hide()
        settings_body = QtWidgets.QWidget()
        settings_row = QtWidgets.QHBoxLayout(settings_body)
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(0)
        self.navigation = QtWidgets.QListWidget()
        self.navigation.setObjectName('settingsNav')
        self.navigation.setAccessibleName('设置分类')
        self.navigation.setFixedWidth(120)
        self.navigation.addItems(['模型连接', '答案提交', '课堂提醒'])
        settings_row.addWidget(self.navigation)
        settings_row.addWidget(self.tabs, 1)
        root.addWidget(settings_body, 1)
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.navigation.setCurrentRow)
        self.navigation.setCurrentRow(0)
        models, self.scrollArea = self.page('大模型连接')
        answers, _ = self.page('答案与提交')
        notices, _ = self.page('提醒与弹幕')

        layout = self.card(models, '模型连接', '选择服务商后填写密钥，即可开始使用。')
        self.llm_on = ToggleSwitch('启用大模型答题')
        layout.addWidget(self.llm_on)
        self.when_llm_on, fields = self.container(layout)
        self.provider_select = SettingsCombo()
        self.provider_select.addItems([p[0] for p in PRESETS])
        self.model_select = SettingsCombo()
        self.model_select.setEditable(True)
        self.model_select.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.llm_model_input = self.model_select.lineEdit()
        self.llm_model_input.setPlaceholderText('可直接输入其他模型名称')
        self.model_select.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        provider_row = QtWidgets.QHBoxLayout()
        for name, widget, key in (('服务商', self.provider_select, None), ('模型 · 可直接编辑', self.model_select, 'model')):
            column = QtWidgets.QVBoxLayout()
            self.field(column, name, widget, key)
            provider_row.addLayout(column, 1)
        fields.addLayout(provider_row)
        self.provider_hint = self.hint('')
        fields.addWidget(self.provider_hint)
        key_row = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(key_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.llm_api_key_input = QtWidgets.QLineEdit()
        self.llm_api_key_input.setEchoMode(QtWidgets.QLineEdit.Password)
        self.llm_api_key_input.setPlaceholderText('粘贴当前服务商的 API Key')
        self.llm_api_key_input.setClearButtonEnabled(True)
        self.show_key = QtWidgets.QToolButton()
        self.show_key.setText('显示')
        self.show_key.setCheckable(True)
        self.show_key.setAccessibleName('显示或隐藏 API Key')
        self.show_key.toggled.connect(self.toggle_key)
        row.addWidget(self.llm_api_key_input, 1)
        row.addWidget(self.show_key)
        self.field(fields, 'API Key', key_row, 'key')
        fields.addWidget(self.hint('切换服务商会保留各自的密钥、模型和接口设置。'))
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText('高级设置 · 接口、路由与费用')
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(QtCore.Qt.RightArrow)
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setProperty('role', 'link')
        fields.addWidget(self.advanced_toggle)
        self.advanced, advanced = self.container(fields)
        self.structured_mode = QtWidgets.QComboBox()
        for text, value in (('自动（按服务商适配）','auto'), ('严格 JSON Schema（需模型支持）','schema'),
                            ('JSON Object（需接口支持）','json'), ('仅使用提示词','prompt')):
            self.structured_mode.addItem(text, value)
        self.field(advanced, '响应格式', self.structured_mode)
        self.llm_base_url_input = QtWidgets.QLineEdit()
        self.llm_base_url_input.setPlaceholderText('https://api.example.com/v1')
        self.llm_base_url_input.setClearButtonEnabled(True)
        self.field(advanced, '接口地址', self.llm_base_url_input, 'url')
        advanced.addWidget(self.hint('支持粘贴完整 /chat/completions 地址，保存时自动整理。'))
        self.llm_timeout_input = self.spin(5, 600, ' 秒')
        self.field(advanced, '模型总等待上限', self.llm_timeout_input)
        advanced.addWidget(self.hint('推荐 30 秒。图片获取与重试计入上限；限时题另外预留 5 秒提交。'))
        self.vision_model_input = QtWidgets.QLineEdit()
        self.vision_model_input.setPlaceholderText('留空使用当前模型（必须支持图片输入）')
        advanced.addWidget(self.hint('模型策略'))
        self.field(advanced, '图片题模型 · 同一服务商', self.vision_model_input)
        self.alternate_models_input = QtWidgets.QLineEdit()
        self.alternate_models_input.setPlaceholderText('可选：用英文逗号分隔，如 model-a, model-b')
        self.field(advanced, '顶部可切换的模型', self.alternate_models_input)
        self.custom_prompt_input = QtWidgets.QPlainTextEdit()
        self.custom_prompt_input.setPlaceholderText('可选：补充解题要求，系统会保留 JSON 输出约束')
        self.custom_prompt_input.setMaximumHeight(90)
        self.field(advanced, '自定义提示词', self.custom_prompt_input)
        self.pricing_on = QtWidgets.QCheckBox('设置费用估算单价（元 / 百万 Token）')
        advanced.addWidget(self.pricing_on)
        price_row = QtWidgets.QHBoxLayout()
        self.price_inputs = []
        for label in ('输入', '缓存输入', '输出'):
            column = QtWidgets.QVBoxLayout()
            price = QtWidgets.QDoubleSpinBox()
            price.setRange(0, 100000)
            price.setDecimals(4)
            column.addWidget(QtWidgets.QLabel(label))
            column.addWidget(price)
            price_row.addLayout(column)
            self.price_inputs.append(price)
        advanced.addLayout(price_row)
        self.pricing_on.toggled.connect(lambda on: [price.setEnabled(on) for price in self.price_inputs])
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.toggle_advanced)
        self.llm_config = self.when_llm_on
        test_row = QtWidgets.QHBoxLayout()
        self.test_button = QtWidgets.QPushButton('测试大模型连接')
        self.test_button.setEnabled(not preview)
        self.test_button.setToolTip('使用当前填写的配置发送一次最小模型请求，会产生少量调用费用。')
        self.test_button.clicked.connect(self.start_connection_test)
        test_row.addWidget(self.test_button)
        test_row.addStretch()
        layout.addLayout(test_row)
        self.test_result = self.hint('尚未测试 · 使用当前填写的地址、密钥和模型，无需先保存。')
        self.test_result.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(self.test_result)
        for field in (self.llm_base_url_input, self.llm_api_key_input, self.llm_model_input, self.vision_model_input):
            field.textChanged.connect(self.invalidate_connection_test)
        self.llm_timeout_input.valueChanged.connect(self.invalidate_connection_test)
        self.structured_mode.currentIndexChanged.connect(self.invalidate_connection_test)
        models.addWidget(self.hint('题目和图片会发送到所选接口。密钥仅保存在当前项目的本地配置中。'))
        models.addStretch()

        layout = self.card(answers, '答案核对', '先看答案，再决定是否修改或立即提交。')
        self.answer_on = ToggleSwitch('生成答案并弹窗核对')
        layout.addWidget(self.answer_on)
        self.when_answer_on, fields = self.container(layout)
        self.review_seconds_input = self.spin(1, 300, ' 秒')
        self.review_max_input = self.spin(1, 300, ' 秒')
        delay_row = QtWidgets.QHBoxLayout()
        for name, control in (('延迟下限', self.review_seconds_input), ('延迟上限', self.review_max_input)):
            column = QtWidgets.QVBoxLayout()
            self.field(column, name, control)
            delay_row.addLayout(column, 1)
        fields.addLayout(delay_row)
        self.delay_error = QtWidgets.QLabel()
        self.delay_error.setObjectName('error')
        self.delay_error.setWordWrap(True)
        self.delay_error.hide()
        fields.addWidget(self.delay_error)
        self.review_seconds_input.valueChanged.connect(self.delay_error.hide)
        self.review_max_input.valueChanged.connect(self.delay_error.hide)
        fields.addWidget(self.hint('开启自动提交时，从答案生成后在区间内随机计时；临近截止会缩短等待并预留 5 秒提交。'))
        self.answer_hint = self.hint('')
        layout.addWidget(self.answer_hint)
        layout = self.card(answers, '提交方式')
        for text in ('立即提交：提交你当前选择或编辑的答案。', '自动提交：在设定的延迟区间结束后提交；可在顶部关闭。',
                     '取消本题：关闭弹窗或按 Esc，停止本题自动提交。'):
            layout.addWidget(self.hint(text))
        answers.addStretch()

        layout = self.card(notices, '课堂提醒', '仅播报你关心的事件。')
        self.audio_on = ToggleSwitch('开启语音提醒')
        layout.addWidget(self.audio_on)
        self.when_audio_on = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(self.when_audio_on)
        grid.setContentsMargins(0, 10, 0, 0)
        self.audio_fields = {}
        for i, (attr, key, text) in enumerate([
            ('receive_problem', 'receive_problem', '收到题目'), ('answer_result', 'answer_result', '答案提交结果'),
            ('self_called', 'im_called', '自己被点名'), ('others_called', 'others_called', '他人被点名'),
            ('course', 'course_info', '课程开始与结束'), ('network', 'network_info', '网络断开与恢复'),
            ('self_danmu', 'send_danmu', '弹幕发送结果'), ('others_danmu', 'others_danmu', '他人弹幕')]):
            box = QtWidgets.QCheckBox(text)
            setattr(self, attr, box)
            self.audio_fields[key] = box
            grid.addWidget(box, i // 2, i % 2)
        layout.addWidget(self.when_audio_on)
        layout = self.card(notices, '自动弹幕')
        self.danmu_on = QtWidgets.QCheckBox('启用自动发送弹幕')
        layout.addWidget(self.danmu_on)
        self.when_danmu_on, fields = self.container(layout)
        self.danmu_spinBox = self.spin(1, 32767, ' 条 / 分钟')
        self.field(fields, '触发阈值', self.danmu_spinBox)
        fields.addWidget(self.hint('一分钟内收到相同弹幕达到此数量时，自动发送相同内容。'))
        notices.addStretch()

        self.footer = QtWidgets.QFrame()
        self.footer.setObjectName('footer')
        self.footer.setMinimumHeight(72)
        self.footer.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        footer = QtWidgets.QHBoxLayout(self.footer)
        footer.setContentsMargins(24, 14, 24, 14)
        self.status = self.hint('修改后点击保存')
        footer.addWidget(self.status, 1)
        self.cancel = QtWidgets.QPushButton('取消')
        self.save = QtWidgets.QPushButton('保存设置')
        self.save.setObjectName('primary')
        for button in (self.cancel, self.save):
            button.setMinimumSize(96, 38)
            button.setAutoDefault(False)
            footer.addWidget(button)
        root.addWidget(self.footer)
        self.cancel.clicked.connect(Dialog.reject)
        self.save.clicked.connect(lambda: self.save_config(Dialog))
        self.save_shortcut = QtWidgets.QShortcut(QtGui.QKeySequence('Ctrl+S'), Dialog)
        self.save_shortcut.activated.connect(lambda: self.save_config(Dialog))
        self.provider_select.currentIndexChanged.connect(self.select_provider)
        for box, callback in ((self.llm_on, self.enable_llm_config), (self.answer_on, self.enable_answer_config),
                              (self.audio_on, self.enable_audio_config), (self.danmu_on, self.enable_danmu_config)):
            box.toggled.connect(callback)
        for widget in Dialog.findChildren(QtWidgets.QLineEdit):
            widget.textEdited.connect(self.mark_dirty)
        for widget in Dialog.findChildren(QtWidgets.QCheckBox):
            widget.toggled.connect(self.mark_dirty)
        for widget in Dialog.findChildren(QtWidgets.QSpinBox):
            widget.valueChanged.connect(self.mark_dirty)
            widget.setAccelerated(True)
        self.model_select.currentTextChanged.connect(self.mark_dirty)
        self.structured_mode.currentIndexChanged.connect(self.mark_dirty)
        self.custom_prompt_input.textChanged.connect(self.mark_dirty)
        for price in self.price_inputs:
            price.valueChanged.connect(self.mark_dirty)
        self.llm_api_key_input.textEdited.connect(lambda: self.set_error('key', ''))
        self.llm_model_input.textEdited.connect(lambda: self.set_error('model', ''))
        self.llm_base_url_input.textEdited.connect(lambda: self.set_error('url', ''))
        self.llm_base_url_input.editingFinished.connect(self.normalize_url_field)
        self.llm_api_key_input.editingFinished.connect(lambda: self.llm_api_key_input.setText(self.llm_api_key_input.text().strip()))
        self.load_config(get_initial_data())
        if preview:
            self.save.setEnabled(False)
            self.save_shortcut.setEnabled(False)
            self.status.setText('界面预览 · 不会保存设置')

    def hint(self, text):
        label = QtWidgets.QLabel(text)
        label.setObjectName('hint')
        label.setWordWrap(True)
        return label

    def page(self, title):
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setMinimumHeight(0)
        area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Ignored)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(14)
        area.setWidget(content)
        self.tabs.addTab(area, title)
        return layout, area

    def card(self, parent, title, description=''):
        frame = QtWidgets.QFrame()
        frame.setObjectName('card')
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(8)
        label = QtWidgets.QLabel(title)
        label.setObjectName('sectionTitle')
        layout.addWidget(label)
        if description:
            layout.addWidget(self.hint(description))
        parent.addWidget(frame)
        return layout

    def container(self, parent):
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        parent.addWidget(widget)
        return widget, layout

    def field(self, layout, label, widget, key=None):
        field_label = QtWidgets.QLabel(label)
        field_label.setObjectName('fieldLabel')
        field_label.setBuddy(self.llm_api_key_input if key == 'key' else widget)
        layout.addWidget(field_label)
        layout.addWidget(widget)
        if key:
            error = QtWidgets.QLabel()
            error.setObjectName('error')
            error.setWordWrap(True)
            error.hide()
            layout.addWidget(error)
            self.errors[key] = error

    def spin(self, minimum, maximum, suffix):
        spin = SettingsSpin()
        spin.setRange(minimum, maximum)
        spin.setSuffix(suffix)
        spin.setMinimumHeight(36)
        spin.setFocusPolicy(QtCore.Qt.StrongFocus)
        spin.setKeyboardTracking(False)
        return spin

    def mark_dirty(self, *_):
        if not self.loading:
            self.status.setText('有未保存的更改 · Ctrl+S 保存')

    def toggle_key(self, visible):
        self.llm_api_key_input.setEchoMode(QtWidgets.QLineEdit.Normal if visible else QtWidgets.QLineEdit.Password)
        self.show_key.setText('隐藏' if visible else '显示')

    def toggle_advanced(self, checked):
        self.advanced.setVisible(checked)
        self.advanced_toggle.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)

    def profile(self):
        return {'base_url': self.normalized_url(self.llm_base_url_input.text()),
                'model': self.llm_model_input.text().strip(), 'api_key': self.llm_api_key_input.text().strip(),
                'timeout': self.llm_timeout_input.value(), 'vision_model': self.vision_model_input.text().strip(),
                'model_choices': [s.strip() for s in self.alternate_models_input.text().split(',') if s.strip()],
                'structured_mode': self.structured_mode.currentData() or 'auto',
                'system_prompt': self.custom_prompt_input.toPlainText().strip(),
                'pricing': dict(enabled=self.pricing_on.isChecked(), **{key: p.value() for key, p in zip(('input','cached','output'), self.price_inputs)})}

    def select_provider(self, index):
        self.provider_profiles[str(self.current_provider)] = self.profile()
        self.current_provider = index
        preset = PRESETS[index]
        profile = self.provider_profiles.get(str(index), {})
        self.show_key.setChecked(False)
        self.llm_base_url_input.setText(profile.get('base_url', preset[1]))
        self.model_select.clear()
        if preset[2]:
            self.model_select.addItem(preset[2])
        self.model_select.setEditText(profile.get('model', preset[2]))
        self.llm_api_key_input.setText(profile.get('api_key', ''))
        self.llm_timeout_input.setValue(profile.get('timeout', 30))
        self.vision_model_input.setText(profile.get('vision_model', ''))
        self.structured_mode.setCurrentIndex(max(0, self.structured_mode.findData(profile.get('structured_mode', 'auto'))))
        self.alternate_models_input.setText(', '.join(profile.get('model_choices') or []))
        self.custom_prompt_input.setPlainText(profile.get('system_prompt', ''))
        rates = profile.get('pricing') or {}
        self.pricing_on.setChecked(bool(rates.get('enabled')))
        for price, key in zip(self.price_inputs, ('input', 'cached', 'output')):
            price.setValue(float(rates.get(key, 0) or 0))
            price.setEnabled(self.pricing_on.isChecked())
        self.provider_hint.setText(preset[3])
        self.advanced_toggle.setChecked(index == 0)
        for key in self.errors:
            self.set_error(key, '')
        self.mark_dirty()

    @staticmethod
    def normalized_url(value):
        value = value.strip().rstrip('/')
        suffix = '/chat/completions'
        return value[:-len(suffix)] if value.endswith(suffix) else value

    def normalize_url_field(self):
        self.llm_base_url_input.setText(self.normalized_url(self.llm_base_url_input.text()))

    def enable_llm_config(self):
        self.when_llm_on.setEnabled(self.llm_on.isChecked())
        self.update_answer_hint()

    def enable_answer_config(self):
        self.when_answer_on.setEnabled(self.answer_on.isChecked())
        self.update_answer_hint()

    def update_answer_hint(self):
        self.answer_hint.setText('尚未启用大模型连接。当前只提醒收到题目，不会自动生成或提交答案。'
                                 if self.answer_on.isChecked() and not self.llm_on.isChecked() else '')

    def enable_audio_config(self):
        self.when_audio_on.setEnabled(self.audio_on.isChecked())

    def enable_danmu_config(self):
        self.when_danmu_on.setEnabled(self.danmu_on.isChecked())

    def enable_delay_custom(self):
        pass  # 兼容旧主界面调用，随机等待已由核对倒计时替代。

    def load_config(self, config):
        self.loading = True
        self.dialog_config = config
        data = get_initial_data()
        def merge(target, values):
            for key, value in values.items():
                if isinstance(value, dict) and isinstance(target.get(key), dict):
                    merge(target[key], value)
                else:
                    target[key] = copy.deepcopy(value)
        merge(data, config)
        self.base_config = data
        self.danmu_on.setChecked(data['auto_danmu'])
        self.danmu_spinBox.setValue(data['danmu_config']['danmu_limit'])
        self.audio_on.setChecked(data['audio_on'])
        for key, box in self.audio_fields.items():
            box.setChecked(data['audio_config']['audio_type'][key])
        self.answer_on.setChecked(data['auto_answer'])
        self.review_seconds_input.setValue(data['answer_review_seconds'])
        self.review_seconds_input.setValue(data.get('answer_delay_min', data['answer_review_seconds']))
        self.review_max_input.setValue(data.get('answer_delay_max', data['answer_review_seconds']))
        llm = data['llm_config']
        self.llm_on.setChecked(llm['enabled'])
        self.provider_profiles = copy.deepcopy(llm.get('profiles', {}))
        index = next((i for i, p in enumerate(PRESETS) if p[1] and
                      p[1].rstrip('/') == self.normalized_url(llm['base_url'])), 0)
        self.current_provider = index
        self.provider_select.blockSignals(True)
        self.provider_select.setCurrentIndex(index)
        self.provider_select.blockSignals(False)
        self.llm_base_url_input.setText(llm['base_url'])
        self.model_select.clear()
        if PRESETS[index][2]:
            self.model_select.addItem(PRESETS[index][2])
        self.model_select.setEditText(llm['model'])
        self.llm_api_key_input.setText(llm['api_key'])
        self.show_key.setChecked(False)
        self.llm_timeout_input.setValue(llm['timeout'])
        self.vision_model_input.setText(llm.get('vision_model', ''))
        self.structured_mode.setCurrentIndex(max(0, self.structured_mode.findData(llm.get('structured_mode', 'auto'))))
        self.alternate_models_input.setText(', '.join(llm.get('model_choices') or []))
        self.custom_prompt_input.setPlainText(llm.get('system_prompt', ''))
        rates = llm.get('pricing') or {}
        self.pricing_on.setChecked(bool(rates.get('enabled', False)))
        for price, key in zip(self.price_inputs, ('input', 'cached', 'output')):
            price.setValue(float(rates.get(key, 0) or 0))
            price.setEnabled(self.pricing_on.isChecked())
        self.provider_hint.setText(PRESETS[index][3])
        self.advanced_toggle.setChecked(index == 0)
        self.enable_llm_config()
        self.enable_answer_config()
        self.enable_audio_config()
        self.enable_danmu_config()
        self.loading = False
        self.status.setText('修改后点击保存 · Ctrl+S')

    def set_error(self, key, text):
        label = self.errors[key]
        label.setText(text)
        label.setVisible(bool(text))
        widget = {'url': self.llm_base_url_input, 'key': self.llm_api_key_input, 'model': self.model_select}[key]
        widget.setProperty('invalid', bool(text))
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def invalidate_connection_test(self, *args):
        self._test_generation += 1
        self._test_running = False
        if hasattr(self, 'test_result'):
            self.test_result.setText('配置已变更，请重新测试。' if not self.loading else '尚未测试 · 使用当前填写的配置，无需先保存。')
            self.test_button.setText('测试大模型连接')
            self.test_button.setEnabled(not self.preview_mode)

    def start_connection_test(self):
        if self.preview_mode or self._test_running:
            return
        profile = self.profile()
        try:
            url = urlsplit(profile['base_url'])
            valid_url = url.scheme in ('http', 'https') and bool(url.hostname) and not url.username and not url.password and not url.query and not url.fragment
        except ValueError:
            valid_url = False
        if not valid_url or not profile['api_key'] or not profile['model'] or any(char.isspace() for char in profile['api_key'] + profile['model']):
            self.test_result.setText('无法测试：请填写有效的接口地址、API Key 和模型名称。')
            return
        self._test_generation += 1
        generation = self._test_generation
        self._test_running = True
        self.test_button.setEnabled(False)
        self.test_button.setText('正在测试…')
        self.test_result.setText('正在请求模型 %s，测试不会提交任何课堂答案。' % profile['model'])
        signal = self._test_signals
        def run():
            try:
                from Scripts.LLM import test_connection
                result = test_connection(profile)
            except Exception:
                result = {'ok': False, 'message': '连接测试失败，请检查配置后重试。', 'model': profile['model']}
            try:
                signal.completed.emit(generation, result)
            except RuntimeError:
                pass  # 对话框已关闭，后台结果不再访问窗口。
        threading.Thread(target=run, daemon=True).start()

    def finish_connection_test(self, generation, result):
        if generation != self._test_generation:
            return
        self._test_running = False
        self.test_button.setEnabled(not self.preview_mode)
        self.test_button.setText('重新测试连接')
        stamp = datetime.datetime.now().strftime('%H:%M:%S')
        latency = result.get('latency_ms')
        elapsed = ' · %.2f 秒' % (latency / 1000) if isinstance(latency, (int,float)) else ''
        self.test_result.setText('%s · %s%s · %s\n%s' % ('测试通过' if result.get('ok') else '测试失败',
            result.get('model') or self.llm_model_input.text(), elapsed, stamp, result.get('message') or result.get('status') or ''))

    def validate(self):
        for key in self.errors:
            self.set_error(key, '')
        if self.review_seconds_input.value() > self.review_max_input.value():
            self.tabs.setCurrentIndex(1)
            self.delay_error.setText('延迟下限不能大于上限，请调整后保存。')
            self.delay_error.show()
            self.review_seconds_input.setFocus()
            self.status.setText('尚未保存，请检查延迟时间')
            return False
        if not self.llm_on.isChecked():
            return True
        profile = self.profile()
        failures = []
        try:
            url = urlsplit(profile['base_url'])
            valid = (url.scheme in ('http', 'https') and bool(url.hostname) and not url.username
                     and not url.password and not url.query and not url.fragment
                     and not any(c.isspace() for c in profile['base_url']))
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            failures.append(('url', '请输入有效的 http:// 或 https:// 接口地址，不要带查询参数。'))
        if not profile['api_key'] or any(c.isspace() for c in profile['api_key']):
            failures.append(('key', '请填写当前服务商的 API Key，密钥中不能含空格或换行。'))
        if not profile['model'] or any(c.isspace() for c in profile['model']):
            failures.append(('model', '请输入准确的模型标识，模型名中不能含空格。'))
        if failures:
            self.tabs.setCurrentIndex(0)
            for key, message in failures:
                self.set_error(key, message)
            first = failures[0][0]
            if first == 'url':
                self.advanced_toggle.setChecked(True)
            widget = {'url': self.llm_base_url_input, 'key': self.llm_api_key_input, 'model': self.model_select}[first]
            self.scrollArea.ensureWidgetVisible(widget)
            widget.setFocus()
            self.status.setText('尚未保存，请检查标红的输入项')
            return False
        return True

    def save_config(self, dialog):
        if self.preview_mode:
            return False
        if not self.validate():
            return False
        config = copy.deepcopy(self.base_config)
        config['auto_danmu'] = self.danmu_on.isChecked()
        config['danmu_config']['danmu_limit'] = self.danmu_spinBox.value()
        config['audio_on'] = self.audio_on.isChecked()
        config['audio_config']['audio_type'].update({key: box.isChecked() for key, box in self.audio_fields.items()})
        config['auto_answer'] = self.answer_on.isChecked()
        config['answer_review_seconds'] = self.review_seconds_input.value()
        config['answer_delay_min'] = self.review_seconds_input.value()
        config['answer_delay_max'] = self.review_max_input.value()
        llm = config['llm_config']
        llm.update(self.profile())
        llm['vision_model'] = self.vision_model_input.text().strip()
        llm['system_prompt'] = self.custom_prompt_input.toPlainText().strip()
        llm['pricing'] = dict(enabled=self.pricing_on.isChecked(), **{key: p.value() for key, p in zip(('input','cached','output'),self.price_inputs)})
        llm['enabled'] = self.llm_on.isChecked()
        profiles = copy.deepcopy(self.provider_profiles)
        profiles[str(self.current_provider)] = self.profile()
        llm['profiles'] = profiles
        try:
            path = Path(get_config_path())
            path.parent.mkdir(parents=True, exist_ok=True)
            output = QtCore.QSaveFile(str(path))
            if not output.open(QtCore.QIODevice.WriteOnly):
                raise OSError('无法打开配置文件')
            payload = json.dumps(config, ensure_ascii=True, indent=2).encode('utf-8')
            if output.write(payload) != len(payload) or not output.commit():
                output.cancelWriting()
                raise OSError('无法写入配置文件')
        except OSError:
            self.status.setText('保存失败，请检查项目目录是否可写后重试')
            return False
        self.dialog_config.clear()
        self.dialog_config.update(config)
        dialog.accept()
        return True
