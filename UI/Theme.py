from UI.QtBootstrap import configure_qt

STYLE = '''
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 10pt; color: #243047; }
QMainWindow, QDialog { background: #f3f6fb; }
QGroupBox { background: white; border: 1px solid #e1e7f0; border-radius: 10px; margin-top: 18px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 16px; color: #4b5e7a; }
QPushButton { background: #eaf0f9; border: 1px solid #d7e1ef; border-radius: 7px; padding: 8px 16px; color: #304d7e; }
QPushButton:hover { background: #dce7f8; }
QPushButton:disabled { color: #98a3b6; background: #edf0f5; }
QPushButton#primary, QPushButton#active_btn { background: #3367d6; color: white; border: none; }
QPushButton#primary:hover, QPushButton#active_btn:hover { background: #2856b8; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTextBrowser { background: white; border: 1px solid #d9e2ef; border-radius: 6px; padding: 7px; selection-background-color: #dce8ff; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #477bdd; }
QTableWidget { background: white; alternate-background-color: #f6f9fe; border: none; gridline-color: #edf1f7; }
QHeaderView::section { background: #edf3fc; color: #506581; border: none; padding: 10px; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: #f3f6fb; }
QCheckBox, QRadioButton { spacing: 8px; padding: 3px; }
QLabel#muted { color: #6c7b90; }
QLabel#label_2 { font-size: 17pt; font-weight: 600; }
QLabel#countdown { color: #2856b8; font-size: 13pt; font-weight: 600; padding: 8px; background: #eaf1ff; border-radius: 8px; }
'''


def apply_theme(widget):
    # 去掉旧界面的行内样式，统一由主题负责。
    from PyQt5.QtWidgets import QWidget
    for child in widget.findChildren(QWidget):
        child.setStyleSheet('')
    widget.setStyleSheet(STYLE)
