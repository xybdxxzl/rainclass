from UI.QtBootstrap import configure_qt
from UI.Typography import prepare_fonts

UI_EDITION = '工作台 2.1'

STYLE = '''
QWidget { font-family: "Noto Sans SC", "Microsoft YaHei"; font-size: 14px; font-weight: 400; color: #1e2b3d; }
QMainWindow, QDialog, QWidget#Window { background: #f5f7fa; }
QFrame#appHeader { background: #1e2b3d; border: none; }
QLabel#appTitle { color: #ffffff; font-size: 20px; font-weight: 500; }
QLabel#appSubtitle { color: #c4cedd; font-size: 12px; }
QLabel#edition { color: #d8e1ee; background: #33435b; border-radius: 4px; padding: 3px 8px; font-size: 12px; }
QFrame#surface { background: #ffffff; border: 1px solid #dce3ec; border-radius: 8px; }
QFrame#metricsStrip { background: transparent; border: none; border-bottom: 1px solid #dce3ec; }
QFrame#answerHero { background: #ffffff; border: none; border-left: 3px solid #315bcb; border-radius: 0; }
QLabel#sectionTitle { color: #1e2b3d; font-size: 16px; font-weight: 500; }
QLabel#loginTitle { color: #1e2b3d; font-size: 20px; font-weight: 500; }
QLabel#eyebrow { color: #5d6b7e; font-size: 12px; }
QLabel#answerValue { color: #315bcb; font-size: 36px; font-weight: 700; }
QLabel#answerValue[answerMode="status"] { font-size: 18px; font-weight: 500; }
QLabel#answerValue[answerMode="text"] { font-size: 16px; font-weight: 500; }
QLabel#confidence { color: #5d6b7e; font-size: 12px; }
QLabel#questionType { color: #315bcb; background: #eef3ff; padding: 3px 7px; border-radius: 4px; font-size: 12px; }
QLabel#timer { color: #344b6a; font-size: 13px; font-weight: 500; }
QLabel#timer[urgent="true"] { color: #a6411a; }
QLabel#countdown { color: #315bcb; background: #eef3ff; border-radius: 6px; padding: 10px; font-size: 17px; font-weight: 500; }
QLabel#metricValue { color: #1e2b3d; font-size: 18px; font-weight: 500; }
QLabel#metricCaption, QLabel#muted, QLabel#hint, QLabel#status, QLabel#feedHint { color: #5d6b7e; font-size: 12px; }
QLabel#connection, QLabel#monitorState { color: #52657c; font-size: 12px; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #cbd6e5; border-radius: 5px; padding: 6px 10px; color: #344c6c; font-size: 13px; font-weight: 500; }
QPushButton:hover, QToolButton:hover { background: #eef3ff; border-color: #8fa9e5; }
QPushButton:pressed, QToolButton:pressed { background: #e2eaff; }
QPushButton:focus, QToolButton:focus { border: 1px solid #315bcb; background: #eef3ff; }
QPushButton[role="primary"], QPushButton#primary { background: #315bcb; color: #ffffff; border: 1px solid #315bcb; font-weight: 500; }
QPushButton[role="primary"]:hover, QPushButton#primary:hover { background: #264bad; border-color: #264bad; }
QPushButton[role="primary"]:pressed, QPushButton#primary:pressed { background: #1c3c91; }
QPushButton[role="primary"]:focus, QPushButton#primary:focus { background: #264bad; color: #ffffff; border: 1px solid #16398e; }
QPushButton#headerAction { background: #315bcb; color: #ffffff; border: 1px solid #557bdc; }
QPushButton#headerAction:hover { background: #416bdc; }
QPushButton#headerAction:focus { border: 1px solid #c8d7ff; }
QPushButton#headerSecondary { background: #2e3d53; color: #e5edfc; border: 1px solid #4a5a73; }
QPushButton#headerSecondary:hover, QPushButton#headerSecondary:focus { background: #3a4e6b; border-color: #95b0e9; }
QToolButton[role="link"] { background: transparent; border: 1px solid transparent; color: #315bcb; padding: 4px 2px; font-weight: 400; }
QToolButton[role="link"]:hover, QToolButton[role="link"]:focus { background: #eef3ff; border-color: #a9bdf0; }
QPushButton:disabled, QToolButton:disabled { color: #77869a; background: #eef1f6; border-color: #e0e5ed; }
QPushButton#primary:disabled { color: #70819c; background: #e5ebf6; border-color: #dae3f2; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextBrowser { background: #ffffff; border: 1px solid #cbd6e5; border-radius: 5px; padding: 6px 8px; selection-background-color: #dce7ff; selection-color: #203f83; font-size: 14px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextBrowser:focus { border: 1px solid #315bcb; }
QTextBrowser#questionContent, QTextBrowser#briefExplanation { border: none; padding: 0; background: transparent; }
QTextBrowser#questionContent:focus, QTextBrowser#briefExplanation:focus { border: 1px solid #a6baf0; }
QCheckBox, QRadioButton { spacing: 8px; padding: 3px; font-size: 13px; }
QGroupBox { background: #ffffff; border: 1px solid #dce3ec; border-radius: 6px; margin-top: 14px; padding: 14px 10px 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; color: #344b6a; font-size: 14px; font-weight: 500; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QProgressBar { background: #e8edf5; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #315bcb; border-radius: 3px; }
QProgressBar[urgent="true"]::chunk { background: #cc6735; }
QTableWidget { background: #ffffff; alternate-background-color: #f5f7fa; border: none; gridline-color: #e5eaf3; font-size: 13px; }
QHeaderView::section { background: #edf2fa; color: #3e5879; border: none; padding: 7px; font-size: 13px; font-weight: 500; }
QSplitter::handle { background: transparent; }
QStatusBar { background: #f5f7fa; color: #5d6b7e; font-size: 12px; }
QScrollBar:vertical { width: 8px; background: transparent; margin: 2px; }
QScrollBar::handle:vertical { background: #bdcbe0; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
'''


def apply_theme(widget):
    from PyQt5 import QtWidgets
    prepare_fonts(QtWidgets.QApplication.instance())
    widget.setStyleSheet(STYLE)

