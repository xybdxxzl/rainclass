import sys
from PyQt5 import QtWidgets, QtCore, QtGui
from UI.MainWindow import MainWindow_Ui
from UI.Theme import STYLE
from Scripts.Utils import resource_path

if __name__ == "__main__":
    # 初始化
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RainClassroomAssistant.Desktop')
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app = QtWidgets.QApplication(sys.argv)
    app.setWindowIcon(QtGui.QIcon(resource_path('UI/Image/app-icon.ico')))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    main = QtWidgets.QMainWindow()
    ui = MainWindow_Ui()
    ui.setupUi(main)
    main.show()
    # 启动监听
    ui.active()
    # 主窗体循环
    app.exec_()

