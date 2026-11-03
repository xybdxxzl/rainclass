"""电脑端的轻量组件，沿用 Qt 的键盘操作和可访问性。"""
from PyQt5 import QtCore, QtGui, QtWidgets


class ElidingLabel(QtWidgets.QLabel):
    """长课程名只占用可用宽度，完整内容保留在提示和可访问文本中。"""
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setTextFormat(QtCore.Qt.PlainText)
        self.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        text = self.fontMetrics().elidedText(self.text(), QtCore.Qt.ElideRight, self.contentsRect().width())
        painter.drawItemText(self.contentsRect(), self.alignment(), self.palette(), self.isEnabled(), text, QtGui.QPalette.WindowText)


class ToggleSwitch(QtWidgets.QCheckBox):
    def sizeHint(self):
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self.text()) + 54, 32)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        track = QtCore.QRectF(2, (self.height() - 18) / 2, 32, 18)
        painter.setOpacity(1 if self.isEnabled() else .45)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor('#3563e9' if self.isChecked() else '#64748b'))
        painter.drawRoundedRect(track, 9, 9)
        painter.setBrush(QtGui.QColor('#ffffff'))
        x = track.left() + (17 if self.isChecked() else 3)
        painter.drawEllipse(QtCore.QRectF(x, track.top() + 3, 12, 12))
        painter.setPen(self.palette().color(QtGui.QPalette.WindowText))
        painter.drawText(QtCore.QRect(43, 0, self.width() - 43, self.height()), QtCore.Qt.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor('#3563e9'), 1.5, QtCore.Qt.DashLine))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawRoundedRect(self.rect().adjusted(0, 1, -1, -2), 5, 5)


def repolish(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
