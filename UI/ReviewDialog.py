"""桌面期末复习工作台，后台只读采集，通过信号更新界面。"""
import copy
from pathlib import Path
import threading

from PyQt5 import QtCore, QtGui, QtWidgets
from Scripts.Review import (ReviewClient, ReviewCollector, ReviewStore, ReviewError, Cancelled,
                            parse_course_url, supplement_ai, missing_official)
from Scripts.ReviewExport import export_book
from Scripts.Utils import get_config_dir
from UI.Theme import STYLE


class ReviewSignals(QtCore.QObject):
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(str, object)
    failed = QtCore.pyqtSignal(str)


class ReviewDialog(QtWidgets.QDialog):
    def __init__(self, config, account_id, user_name='', parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.account_id = str(account_id)
        self.store = ReviewStore(Path(get_config_dir()) / 'review', self.account_id)
        self.cancel = threading.Event()
        self.running = False
        self.records = []
        self.signals = ReviewSignals(self)
        self.signals.progress.connect(self.log)
        self.signals.finished.connect(self.finished_work)
        self.signals.failed.connect(self.failed_work)
        self.setWindowTitle('期末复习 · ' + (user_name or '当前账号'))
        self.resize(1020, 730)
        self.setMinimumSize(780, 580)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        title = QtWidgets.QLabel('期末复习 · 课件与习题归档')
        title.setObjectName('sectionTitle')
        layout.addWidget(title)
        hint = QtWidgets.QLabel('按学期日期范围收集所选课程的可访问历史课堂。只读采集；官方答案缺失会明确标记，不会提交课堂答案。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        date_row = QtWidgets.QHBoxLayout()
        self.term = QtWidgets.QComboBox()
        self.term.setAccessibleName('选择学期日期范围')
        now = QtCore.QDate.currentDate()
        for year in range(now.year() - 2, now.year() + 2):
            self.term.addItem('%d 春季（2月-7月）' % year, (QtCore.QDate(year, 2, 1), QtCore.QDate(year, 7, 31)))
            self.term.addItem('%d 秋季（8月-次年1月）' % year, (QtCore.QDate(year, 8, 1), QtCore.QDate(year + 1, 1, 31)))
        self.start, self.end = QtWidgets.QDateEdit(), QtWidgets.QDateEdit()
        for field in (self.start, self.end):
            field.setCalendarPopup(True)
            field.setDisplayFormat('yyyy-MM-dd')
        self.start.setAccessibleName('学期开始日期，可按学校校历修改')
        self.end.setAccessibleName('学期结束日期，可按学校校历修改')
        for widget in (QtWidgets.QLabel('学期'), self.term, QtWidgets.QLabel('从'), self.start, QtWidgets.QLabel('至'), self.end):
            date_row.addWidget(widget)
        layout.addLayout(date_row)
        self.term.currentIndexChanged.connect(self.apply_term)
        selected = next((i for i in range(self.term.count()) if self.term.itemData(i)[0] <= now <= self.term.itemData(i)[1]), 0)
        self.term.setCurrentIndex(selected)
        self.apply_term()
        for field in (self.start, self.end):
            field.dateChanged.connect(self.reload)
        course_row = QtWidgets.QHBoxLayout()
        self.load = QtWidgets.QPushButton('加载我的课程')
        self.load.clicked.connect(self.load_courses)
        self.manual = QtWidgets.QPushButton('从课程地址添加')
        self.manual.clicked.connect(self.add_course_url)
        self.select_all = QtWidgets.QPushButton('全选课程')
        self.select_all.clicked.connect(self.check_all)
        for button in (self.load, self.manual, self.select_all):
            course_row.addWidget(button)
        course_row.addStretch()
        layout.addLayout(course_row)
        splitter = QtWidgets.QSplitter()
        self.courses = QtWidgets.QListWidget()
        self.courses.setAccessibleName('勾选需要收集的课程')
        self.courses.itemChanged.connect(self.reload)
        self.courses.setMinimumWidth(230)
        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['课程 / 课次', '日期', '课件页', '习题', '缺官方答案', '采集状态'])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        splitter.addWidget(self.courses)
        splitter.addWidget(self.table)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        actions = QtWidgets.QHBoxLayout()
        self.scan = QtWidgets.QPushButton('一键收集所选课程')
        self.scan.clicked.connect(self.collect)
        self.ai = QtWidgets.QPushButton('AI 补充缺失答案')
        self.ai.clicked.connect(self.fill_ai)
        self.stop = QtWidgets.QPushButton('取消当前任务')
        self.stop.clicked.connect(self.cancel.set)
        self.stop.setEnabled(False)
        for button in (self.scan, self.ai, self.stop):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.logs = QtWidgets.QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(1000)
        self.logs.setMaximumHeight(110)
        self.logs.setAccessibleName('采集进度和缺失项')
        layout.addWidget(self.logs)
        options = QtWidgets.QHBoxLayout()
        self.content = QtWidgets.QComboBox()
        self.content.addItem('仅习题（课件另行导出）', 'questions')
        self.content.addItem('仅课件', 'courseware')
        self.content.addItem('课件＋习题', 'combined')
        self.content.setAccessibleName('导出内容')
        self.answers = QtWidgets.QCheckBox('包含答案和解析')
        self.answers.setChecked(True)
        self.answer_position = QtWidgets.QComboBox()
        self.answer_position.addItem('答案紧跟每道题', 'inline')
        self.answer_position.addItem('答案集中在末尾', 'appendix')
        self.answer_position.setAccessibleName('答案位置')
        self.personal = QtWidgets.QCheckBox('包含个人作答')
        for option in (self.content, self.answers, self.answer_position, self.personal):
            options.addWidget(option)
        self.content.currentIndexChanged.connect(self.update_export_options)
        self.answers.toggled.connect(self.update_export_options)
        layout.addLayout(options)
        footer = QtWidgets.QHBoxLayout()
        self.html = QtWidgets.QPushButton('导出离线网页')
        self.pdf = QtWidgets.QPushButton('导出 PDF 复习册')
        self.html.clicked.connect(lambda: self.export('html'))
        self.pdf.clicked.connect(lambda: self.export('pdf'))
        footer.addWidget(self.html)
        footer.addWidget(self.pdf)
        footer.addStretch()
        self.open_folder = QtWidgets.QPushButton('打开本机归档')
        self.open_folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.store.root))))
        footer.addWidget(self.open_folder)
        close = QtWidgets.QPushButton('关闭')
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        layout.addLayout(footer)
        self.controls = [self.load, self.manual, self.select_all, self.scan, self.ai, self.html, self.pdf,
                         self.term, self.start, self.end, self.courses, self.content, self.answers, self.answer_position, self.personal]
        self.reload()
        self.log('先加载课程并勾选；已归档课程可从官网学习日志地址添加。学期日期可按校历调整。')

    def apply_term(self):
        start, end = self.term.currentData()
        self.start.setDate(start)
        self.end.setDate(end)

    def update_export_options(self):
        questions = self.content.currentData() != 'courseware'
        self.answers.setEnabled(not self.running and questions)
        show_answers = not self.running and questions and self.answers.isChecked()
        self.answer_position.setEnabled(show_answers)
        self.personal.setEnabled(show_answers)

    def dates(self):
        return self.start.date().toString('yyyy-MM-dd'), self.end.date().toString('yyyy-MM-dd')

    def selected_courses(self):
        return [self.courses.item(i).data(QtCore.Qt.UserRole) for i in range(self.courses.count())
                if self.courses.item(i).checkState() == QtCore.Qt.Checked]

    def add_course(self, course):
        if any(self.courses.item(i).data(QtCore.Qt.UserRole)['classroom_id'] == course['classroom_id'] for i in range(self.courses.count())):
            return
        item = QtWidgets.QListWidgetItem(course['name'] + (' · ' + course['classroom'] if course.get('classroom') else ''))
        item.setData(QtCore.Qt.UserRole, course)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked)
        self.courses.addItem(item)

    def check_all(self):
        for i in range(self.courses.count()):
            self.courses.item(i).setCheckState(QtCore.Qt.Checked)

    def add_course_url(self):
        value, ok = QtWidgets.QInputDialog.getText(self, '添加历史或已归档课程', '填写官网课程学习日志地址（不是 /web?index 外层地址）：')
        if not ok:
            return
        try:
            course = parse_course_url(value)
            name, ok = QtWidgets.QInputDialog.getText(self, '课程名称', '课程名称：')
            if ok and name.strip():
                course['name'] = name.strip()
                self.add_course(course)
                self.reload()
        except ReviewError as error:
            self.log(str(error))

    def log(self, message):
        self.logs.appendPlainText(str(message))

    def begin(self, kind, callback):
        if self.running:
            return
        self.running = True
        self.cancel.clear()
        for widget in self.controls:
            widget.setEnabled(False)
        self.stop.setEnabled(True)

        def work():
            try:
                result = callback()
                self.signals.finished.emit(kind, result)
            except (ReviewError, PermissionError) as error:
                self.signals.failed.emit(str(error))
            except Exception:
                self.signals.failed.emit('处理失败，已保留本机资料。请检查网络、磁盘空间或重新采集。')
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def with_client(self, callback):
        client = ReviewClient(self.config.get('sessionid', ''), self.account_id, self.cancel)
        try:
            return callback(client)
        finally:
            client.close()

    def load_courses(self):
        self.begin('courses', lambda: self.with_client(lambda client: client.courses()))

    def collect(self):
        courses = copy.deepcopy(self.selected_courses())
        if not courses:
            self.log('请先加载并勾选课程，或从课程学习日志地址添加。')
            return
        start, end = self.dates()
        if start > end:
            self.log('开始日期不能晚于结束日期。')
            return
        self.begin('collect', lambda: self.with_client(lambda client: ReviewCollector(client, self.store, self.signals.progress.emit).collect(courses, start, end)))

    def fill_ai(self):
        self.reload()
        records = copy.deepcopy(self.records)
        cfg = copy.deepcopy(self.config.get('llm_config') or {})
        if not records:
            self.log('当前范围没有资料，请先采集。')
            return
        if not cfg.get('enabled') or not cfg.get('api_key'):
            self.log('请先在主窗口设置中启用并配置大模型，再重新打开复习窗口。')
            return
        answer = QtWidgets.QMessageBox.question(self, '生成 AI 参考答案',
            '将把当前范围内缺少官方答案的题面发送到你配置的模型服务，可能产生费用。只处理未生成过的题目，不会提交答案。继续吗？',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if answer == QtWidgets.QMessageBox.Yes:
            self.begin('ai', lambda: supplement_ai(self.store, records, cfg, self.cancel, self.signals.progress.emit))

    def restore(self):
        self.running = False
        for widget in self.controls:
            widget.setEnabled(True)
        self.stop.setEnabled(False)
        self.update_export_options()
        self.reload()

    def finished_work(self, kind, result):
        self.restore()
        if kind == 'courses':
            for course in result:
                self.add_course(course)
            self.reload()
            self.log('已加载 %d 门可访问课程；归档课程可使用学习日志地址添加。' % len(result))
        elif kind == 'collect':
            self.log('本次：%d 个课次，完整 %d，部分 %d，失败 %d；习题 %d，缺官方答案 %d；课件图片 %d。' %
                tuple(result[k] for k in ('lessons', 'complete', 'partial', 'failed', 'questions', 'missing_answers', 'images')))
            for error in result['errors']:
                self.log('未完成项：' + error)
            if result.get('polls'):
                self.log('其中 %d 道为投票题，不适用标准答案，不计入缺官方答案数量。' % result['polls'])
            if not result['lessons'] and not result['failed']:
                self.log('此日期范围没有发现可访问课次，请检查学期日期和课程选择。')
        elif kind == 'ai':
            self.log('AI 补充完成：生成 %d，失败 %d。参考答案仍需核对。' % (result['generated'], result['failed']))
        elif kind == 'export':
            self.log('已导出：' + result['path'] + ('；移动网页时请一起移动同名 _assets 文件夹。' if result['kind'] == 'html' else ''))

    def failed_work(self, message):
        self.restore()
        self.log(message)

    def reload(self):
        if not hasattr(self, 'table'):
            return
        start, end = self.dates()
        selected = {course['classroom_id'] for course in self.selected_courses()}
        self.records = self.store.list(start=start, end=end)
        if self.courses.count():
            self.records = [r for r in self.records if r['classroom_id'] in selected]
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            pages = sum(bool(s.get('image')) for p in record['presentations'] for s in p['slides'])
            missing = sum(missing_official(q) for q in record['questions'])
            values = [record['course'] + ' / ' + record['title'], record['date'], pages, len(record['questions']), missing,
                      {'complete':'采集完整', 'partial':'有缺失项', 'failed':'采集失败'}.get(record['status'], record['status'])]
            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(str(value))
                item.setToolTip('\n'.join(record.get('issues', [])))
                self.table.setItem(row, col, item)
        self.summary.setText('当前导出范围：%s 至 %s · %d 个课次 · %d 道习题 · %d 个课次有未完成项。' %
            (start, end, len(self.records), sum(len(r['questions']) for r in self.records), sum(r['status'] != 'complete' for r in self.records)))

    def export(self, kind):
        self.reload()
        if not self.records:
            self.log('当前范围没有资料，请先采集。')
            return
        content = self.content.currentData()
        name = {'questions': '课堂习题与答案' if self.answers.isChecked() else '课堂习题',
                'courseware': '课件归档', 'combined': '期末复习册'}[content]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, '导出期末复习资料', name + '.' + kind,
                            'PDF 文件 (*.pdf)' if kind == 'pdf' else '离线网页 (*.html)')
        if not path:
            return
        if Path(path).suffix.lower() != '.' + kind:
            path += '.' + kind
        records = copy.deepcopy(self.records)
        questions, slides = content != 'courseware', content != 'questions'
        answers, personal = questions and self.answers.isChecked(), self.personal.isChecked()
        position = self.answer_position.currentData()
        def work():
            export_book(path, records, self.store.root, answers, slides, personal, self.cancel,
                        questions=questions, answer_position=position)
            return {'path': path, 'kind': kind}
        self.log('正在导出复习册，页面较多时可能需要一些时间。')
        self.begin('export', work)

    def reject(self):
        if self.running:
            self.cancel.set()
            self.log('正在取消，请等待当前网络请求结束，已采集资料会保留。')
            return
        super().reject()

    def closeEvent(self, event):
        if self.running:
            self.reject()
            event.ignore()
        else:
            event.accept()
