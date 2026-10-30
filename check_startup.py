"""离线启动自检：创建真实 Qt 窗口，不连接课堂。"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from UI.QtBootstrap import configure_qt
from PyQt5 import QtWidgets
from UI.MainWindow import MainWindow_Ui

app = QtWidgets.QApplication([])
window = QtWidgets.QMainWindow()
window.resize(800, 600)
window.show()
app.processEvents()
window.close()
print('Startup dependencies and Qt window OK')
