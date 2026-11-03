import sys
from PyQt5 import QtWidgets, QtCore, QtGui
from UI.MainWindow import MainWindow_Ui
from UI.Theme import STYLE, prepare_fonts
from Scripts.Utils import resource_path

if __name__ == "__main__":
    # 初始化
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RainClassroomAssistant.Desktop')
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
    QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
    app = QtWidgets.QApplication(sys.argv)
    prepare_fonts(app)
    app.setWindowIcon(QtGui.QIcon(resource_path('UI/Image/app-icon.ico')))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    main = QtWidgets.QMainWindow()
    ui = MainWindow_Ui()
    preview = '--preview' in sys.argv
    ui.setupUi(main, preview=preview)
    main.show()
    # 使用实际窗口边框居中，避免系统缩放后底部按钮落到屏幕外。
    available = main.screen().availableGeometry()
    frame = main.frameGeometry()
    main.resize(min(main.width(), available.width() - frame.width() + main.width() - 16),
                min(main.height(), available.height() - frame.height() + main.height() - 16))
    frame = main.frameGeometry()
    frame.moveCenter(available.center())
    main.move(frame.topLeft())
    # 启动监听
    if not preview:
        ui.active()
    # 主窗体循环
    app.exec_()

