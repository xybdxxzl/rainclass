import math
import time
from PyQt5 import QtCore, QtWidgets
from Scripts.LLM import strip_html
from UI.Theme import STYLE


class AnswerDialog(QtWidgets.QDialog):
    def __init__(self, review, parent=None):
        super().__init__(parent)
        self.review = review
        self.setWindowTitle('核对答案 · ' + review.course)
        self.resize(620, 560)
        self.setStyleSheet(STYLE)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        title = QtWidgets.QLabel(review.course + ' · 答案待提交')
        title.setTextFormat(QtCore.Qt.PlainText)
        layout.addWidget(title)
        self.countdown = QtWidgets.QLabel()
        self.countdown.setObjectName('countdown')
        layout.addWidget(self.countdown)
        question = QtWidgets.QTextBrowser()
        text, _ = strip_html(review.problem.get('body') or review.problem.get('content') or review.problem.get('title') or '')
        lines = [text]
        for i, option in enumerate(review.problem.get('options') or []):
            if isinstance(option, dict):
                key = option.get('key') or chr(65 + i)
                value = option.get('value') or option.get('content') or ''
            else:
                key, value = chr(65 + i), option
            lines.append('%s. %s' % (key, strip_html(value)[0]))
        question.setPlainText('\n'.join(lines))
        layout.addWidget(question, 2)
        self.editors, self.choices = [], []
        group = QtWidgets.QGroupBox('核对并修改答案')
        form = QtWidgets.QVBoxLayout(group)
        if review.problemtype in (1, 2):
            options = review.problem.get('options') or []
            keys = [(opt.get('key') or chr(65 + i)) if isinstance(opt, dict) else chr(65 + i)
                    for i, opt in enumerate(options)] or list('ABCD')
            self.button_group = QtWidgets.QButtonGroup(self)
            self.button_group.setExclusive(review.problemtype == 1)
            row = QtWidgets.QHBoxLayout()
            for key in keys:
                button = QtWidgets.QRadioButton(str(key)) if review.problemtype == 1 else QtWidgets.QCheckBox(str(key))
                self.button_group.addButton(button)
                button.setChecked(str(key).upper() in review.answers)
                self.choices.append((str(key).upper(), button))
                button.toggled.connect(self.update_answer)
                row.addWidget(button)
            form.addLayout(row)
        else:
            for i, answer in enumerate(review.answers):
                form.addWidget(QtWidgets.QLabel('第 %d 空' % (i + 1) if review.problemtype == 4 else '答案'))
                editor = QtWidgets.QPlainTextEdit(str(answer))
                editor.setMaximumHeight(80 if review.problemtype == 4 else 160)
                editor.textChanged.connect(self.update_answer)
                self.editors.append(editor)
                form.addWidget(editor)
        layout.addWidget(group, 2)
        self.note = QtWidgets.QLabel('倒计时结束会提交当前有效答案；关闭窗口或点击取消将放弃本题。')
        self.note.setWordWrap(True)
        self.note.setObjectName('muted')
        layout.addWidget(self.note)
        row = QtWidgets.QHBoxLayout()
        cancel = QtWidgets.QPushButton('取消本题')
        cancel.clicked.connect(self.reject)
        self.submit = QtWidgets.QPushButton('立即提交')
        self.submit.setObjectName('primary')
        self.submit.clicked.connect(self.confirm)
        row.addWidget(cancel)
        row.addStretch()
        row.addWidget(self.submit)
        layout.addLayout(row)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.tick()

    def update_answer(self):
        answers = [key for key, button in self.choices if button.isChecked()] if self.choices else [e.toPlainText().strip() for e in self.editors]
        valid = bool(answers) and all(answers)
        self.submit.setEnabled(valid)
        if valid:
            self.review.update(answers)
        else:
            self.review.mark_invalid()
        if not self.review.auto_submit:
            self.note.setText('自动提交已暂停；请修改后点击立即提交，或关闭窗口取消本题。' if valid else '答案不能为空；请补全或取消本题。')
        else:
            self.note.setText('倒计时结束提交当前答案；关闭窗口将取消本题。' if valid else
                              '答案不能为空。到时将提交最近一次有效答案，请及时补全或取消本题。')

    def set_answers(self, answers):
        if self.choices:
            self.button_group.setExclusive(False)
            for key, button in self.choices:
                button.blockSignals(True)
                button.setChecked(key in answers)
                button.blockSignals(False)
            self.button_group.setExclusive(self.review.problemtype == 1)
        else:
            for editor, text in zip(self.editors, answers):
                editor.blockSignals(True)
                editor.setPlainText(str(text))
                editor.blockSignals(False)

    def confirm(self):
        self.review.finish()
        self.timer.stop()
        self.accept()

    def tick(self):
        if self.review.event.is_set():
            self.timer.stop()
            self.accept()
            return
        target = self.review.deadline if self.review.auto_submit else self.review.question_deadline
        remaining = max(0, target - time.monotonic()) if target is not None else None
        self.countdown.setText(('%d 秒后自动提交' if self.review.auto_submit else '手动提交 · 剩余 %d 秒') % math.ceil(remaining) if remaining is not None else '手动提交 · 不限时')
        if not self.review.auto_submit and '自动提交已暂停' not in self.note.text():
            self.note.setText('自动提交已暂停；请修改后点击立即提交，或关闭窗口取消本题。')
        if remaining is not None and remaining <= 0 and self.review.auto_submit:
            self.confirm()
        elif remaining is not None and remaining <= 0:
            self.reject()

    def reject(self):
        self.review.finish(cancel=True)
        self.timer.stop()
        super().reject()

    def closeEvent(self, event):
        self.reject()
        event.accept()
