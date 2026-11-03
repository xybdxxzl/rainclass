"""按当前账号浏览本地持久化题目，历史详情只读。"""
import json
import time
from PyQt5 import QtCore, QtWidgets
from Scripts.LLM import strip_html
from UI.Theme import STYLE
from UI.Typography import reading_text


MODEL_STATES = {'not_requested':'尚未请求模型', 'pending':'等待模型处理', 'processing':'模型处理中', 'retrying':'模型正在重试',
                'success':'模型已生成建议', 'timeout':'模型超时', 'failed':'模型失败', 'disabled':'模型未开启', 'cancelled':'模型处理已结束'}
SUBMISSION_STATES = {'waiting':'未提交', 'submitting':'正在提交', 'submitted':'提交成功', 'failed':'提交失败', 'unknown':'提交结果未知，请在雨课堂核对',
                     'cancelled':'已取消', 'expired':'题目已截止'}


class HistoryDialog(QtWidgets.QDialog):
    def __init__(self, store, account_id, user_name='', parent=None):
        super().__init__(parent)
        self.store, self.account_id = store, str(account_id or '')
        self.records = []
        self.setWindowTitle('历史题目 · ' + (user_name or '当前账号'))
        self.resize(940, 680)
        self.setMinimumSize(650, 460)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('历史题目 · ' + (user_name or '当前账号'))
        title.setTextFormat(QtCore.Qt.PlainText)
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        hint = QtWidgets.QLabel('题目从接收时开始保存；模型失败、超时和未作答题目也会保留。详情为只读。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QtWidgets.QHBoxLayout()
        self.keyword = QtWidgets.QLineEdit()
        self.keyword.setPlaceholderText('搜索题干、答案或课程')
        self.keyword.setAccessibleName('搜索历史题目')
        self.course = QtWidgets.QLineEdit()
        self.course.setPlaceholderText('课程名称')
        self.course.setAccessibleName('按课程筛选')
        self.date = QtWidgets.QLineEdit()
        self.date.setPlaceholderText('日期：2026-11-03')
        self.date.setAccessibleName('按日期筛选，年-月-日')
        self.date.setMaximumWidth(160)
        self.status = QtWidgets.QComboBox()
        self.status.setAccessibleName('按题目状态筛选')
        for label, value in [('全部状态',''), ('模型超时','timeout'), ('模型失败','failed'), ('提交成功','submitted'), ('提交结果未知','unknown'), ('已取消','cancelled'), ('已过期','expired')]:
            self.status.addItem(label, value)
        for widget in (self.keyword, self.course, self.date, self.status):
            row.addWidget(widget)
        search = QtWidgets.QPushButton('查询')
        search.clicked.connect(self.reload)
        row.addWidget(search)
        layout.addLayout(row)
        self.summary = QtWidgets.QLabel()
        layout.addWidget(self.summary)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['接收时间', '课程', '题型', '状态', '题目'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        self.table.itemSelectionChanged.connect(self.show_detail)
        self.detail = QtWidgets.QTextBrowser()
        self.detail.setAccessibleName('历史题目详情，只读')
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setSizes([240, 310])
        layout.addWidget(splitter, 1)
        footer = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton('复制题目详情')
        copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.detail.toPlainText()))
        footer.addWidget(copy)
        footer.addStretch()
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        for field in (self.keyword, self.course, self.date):
            field.returnPressed.connect(self.reload)
        self.status.currentIndexChanged.connect(self.reload)
        self.reload()

    def reload(self):
        try:
            self.records = self.store.list(self.account_id, keyword=self.keyword.text().strip(),
                                           course=self.course.text().strip(), status=self.status.currentData() or '',
                                           date=self.date.text().strip(), limit=200)
        except Exception:
            self.records = []
            self.summary.setText('历史记录读取失败，请稍后重试。')
            return
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            problem = record.get('problem') or {}
            text = strip_html(problem.get('body') or problem.get('content') or problem.get('title') or '')[0]
            timestamp = record.get('received_at') or 0
            values = [time.strftime('%m-%d %H:%M:%S', time.localtime(timestamp)), record.get('course', ''),
                      {1:'单选',2:'多选',4:'填空',5:'简答'}.get(record.get('problemtype'), '待识别'),
                      record.get('status') or SUBMISSION_STATES.get(record.get('submission_state'), '') or MODEL_STATES.get(record.get('llm_state'), ''), text[:100]]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))
        self.summary.setText('显示最近 %d 条匹配记录 · 仅当前账号、本机历史' % len(self.records))
        if self.records:
            self.table.selectRow(0)
        else:
            self.detail.setPlainText('暂无匹配记录。接收到的新题目会自动保存在这里。')

    def show_detail(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.records):
            return
        record = self.records[row]
        problem, result = record.get('problem') or {}, record.get('result') or {}
        lines = [record.get('course', ''), '题目编号：' + str(record.get('problem_id', '')),
                 '接收时间：' + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.get('received_at') or 0)), '',
                 strip_html(problem.get('body') or problem.get('content') or problem.get('title') or '题面未获取到')[0]]
        for i, option in enumerate(problem.get('options') or []):
            key = option.get('key') or chr(65+i) if isinstance(option, dict) else chr(65+i)
            value = option.get('value') or option.get('content') or '' if isinstance(option, dict) else option
            lines.append('%s. %s' % (key, strip_html(value)[0]))
        lines += ['', '当前答案：' + '、'.join(map(str, record.get('answers') or [])),
                  '模型建议：' + '、'.join(map(str, result.get('answers') or [])),
                  '解析：' + str(result.get('explanation') or '未提供'),
                  '模型状态：' + MODEL_STATES.get(record.get('llm_state'), '待确认'),
                  '模型耗时：' + ('%s 毫秒' % result['latency_ms'] if result.get('latency_ms') is not None else '未提供'),
                  '处理状态：' + str(record.get('status') or ''),
                  '提交结果：' + SUBMISSION_STATES.get(record.get('submission_state'), '未提交')]
        if record.get('llm_error'):
            lines.append('错误原因：' + str(record['llm_error']))
        self.detail.setPlainText('\n'.join(lines))
        reading_text(self.detail)
