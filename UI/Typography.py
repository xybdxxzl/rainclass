"""项目内字体和阅读排版；不安装系统字体，不依赖工作目录。"""
import os
import sys
import math
from pathlib import Path
from PyQt5 import QtGui, QtWidgets
from UI.QtBootstrap import configure_qt

FONT_FAMILY = 'Noto Sans SC'
FONT_FILES = ('NotoSansSC-Regular.otf', 'NotoSansSC-Medium.otf', 'NotoSansSC-Bold.otf')
RESOURCE_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
FONT_DIRECTORY = RESOURCE_ROOT / 'UI' / 'Fonts'


def ui_font(pixels=14, weight=QtGui.QFont.Normal):
    app = QtWidgets.QApplication.instance()
    family = getattr(app, '_rain_font_family', FONT_FAMILY) if app else FONT_FAMILY
    font = QtGui.QFont(family)
    font.setPixelSize(pixels)
    font.setWeight(weight)
    font.setStyleStrategy(QtGui.QFont.PreferAntialias)
    return font


def prepare_fonts(app):
    if app is None or getattr(app, '_rain_fonts_ready', False):
        return
    ids = []
    for name in FONT_FILES:
        path = FONT_DIRECTORY / name
        if path.is_file():
            ids.append(QtGui.QFontDatabase.addApplicationFont(str(path)))
    styles = set(QtGui.QFontDatabase().styles(FONT_FAMILY))
    if {'Regular', 'Medium', 'Bold'}.issubset(styles):
        app._rain_font_family = FONT_FAMILY
    else:
        # 字体资源被移动时仍可打开；回退也加载真实粗体。
        windows_fonts = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        for name in ('msyh.ttc', 'msyhbd.ttc', 'segoeui.ttf', 'segoeuib.ttf'):
            path = windows_fonts / name
            if path.is_file():
                ids.append(QtGui.QFontDatabase.addApplicationFont(str(path)))
        app._rain_font_family = 'Microsoft YaHei'
    app._rain_font_ids = ids
    app._rain_fonts_ready = True
    app.setFont(ui_font())


def reading_text(widget, pixels=15, line_height=155):
    """为题目与解析设置实际文档行距，而不只依赖 Qt 的 CSS 支持。"""
    document = widget.document()
    document.setDefaultFont(ui_font(pixels))
    cursor = QtGui.QTextCursor(document)
    cursor.beginEditBlock()
    block = document.begin()
    while block.isValid():
        cursor.setPosition(block.position())
        formatting = block.blockFormat()
        # Noto 的字体行框包含较大的上下留白，以字号计算行距，避免重复放大。
        height = max(QtGui.QFontMetrics(ui_font(pixels)).height(), math.ceil(pixels * line_height / 100))
        formatting.setLineHeight(height, QtGui.QTextBlockFormat.FixedHeight)
        cursor.setBlockFormat(formatting)
        block = block.next()
    cursor.endEditBlock()
