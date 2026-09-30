"""Thin outline icons drawn with QPainter, so the app needs no icon files.

Every icon is drawn on a 20x20 grid in one stroke colour, the way the sidebar
and the AI page show them.
"""
from PyQt6.QtCore import Qt, QPointF, QRectF
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _p(x, y):
    return QPointF(x, y)


def _draw(p, kind):
    if kind == "sidebar":
        p.drawRoundedRect(QRectF(3, 4, 14, 12), 3, 3)
        p.drawLine(_p(8, 4), _p(8, 16))
    elif kind == "chat":
        path = QPainterPath()
        path.addRoundedRect(QRectF(3, 3.5, 14, 10.5), 3.5, 3.5)
        p.drawPath(path)
        p.drawPolyline(QPolygonF([_p(6.5, 14), _p(6.5, 17), _p(10, 14)]))
    elif kind == "plug":
        p.drawLine(_p(8, 2.5), _p(8, 6))
        p.drawLine(_p(12, 2.5), _p(12, 6))
        p.drawRoundedRect(QRectF(5.5, 6, 9, 6.5), 2.5, 2.5)
        p.drawLine(_p(10, 12.5), _p(10, 17.5))
    elif kind == "rules":
        for y in (5.5, 10, 14.5):
            p.drawPoint(_p(4.5, y))
            p.drawLine(_p(8, y), _p(16, y))
    elif kind == "plus":
        p.drawLine(_p(10, 4), _p(10, 16))
        p.drawLine(_p(4, 10), _p(16, 10))
    elif kind == "send":
        p.drawLine(_p(10, 15.5), _p(10, 4.5))
        p.drawPolyline(QPolygonF([_p(5, 9.5), _p(10, 4.5), _p(15, 9.5)]))
    elif kind == "cart":
        p.drawPolyline(QPolygonF([_p(2.5, 3.5), _p(4.5, 3.5), _p(6.5, 12.5), _p(15, 12.5), _p(17, 6.5), _p(5.3, 6.5)]))
        p.drawEllipse(_p(7.5, 16), 1.1, 1.1)
        p.drawEllipse(_p(14, 16), 1.1, 1.1)
    elif kind == "box":
        p.drawPolygon(QPolygonF([_p(10, 2.5), _p(17, 6.3), _p(10, 10), _p(3, 6.3)]))
        p.drawPolyline(QPolygonF([_p(3, 6.3), _p(3, 13.7), _p(10, 17.5), _p(17, 13.7), _p(17, 6.3)]))
        p.drawLine(_p(10, 10), _p(10, 17.5))
    elif kind == "check":
        p.drawEllipse(_p(10, 10), 7, 7)
        p.drawPolyline(QPolygonF([_p(7, 10.3), _p(9.2, 12.5), _p(13, 8)]))
    elif kind == "chart":
        p.drawLine(_p(3, 16.5), _p(17, 16.5))
        p.drawLine(_p(6, 13.5), _p(6, 9.5))
        p.drawLine(_p(10, 13.5), _p(10, 5.5))
        p.drawLine(_p(14, 13.5), _p(14, 7.5))
    elif kind == "receipt":
        p.drawPolyline(QPolygonF([
            _p(5, 2.5), _p(15, 2.5), _p(15, 17.5), _p(12.5, 16), _p(10, 17.5),
            _p(7.5, 16), _p(5, 17.5), _p(5, 2.5),
        ]))
        p.drawLine(_p(7.5, 7), _p(12.5, 7))
        p.drawLine(_p(7.5, 10.5), _p(12.5, 10.5))
    elif kind == "barcode":
        for a, b in (((3.5, 6), (3.5, 3.5)), ((3.5, 3.5), (6, 3.5)), ((14, 3.5), (16.5, 3.5)),
                     ((16.5, 3.5), (16.5, 6)), ((16.5, 14), (16.5, 16.5)), ((16.5, 16.5), (14, 16.5)),
                     ((6, 16.5), (3.5, 16.5)), ((3.5, 16.5), (3.5, 14))):
            p.drawLine(_p(*a), _p(*b))
        for x in (7, 9.5, 12, 13.8):
            p.drawLine(_p(x, 7), _p(x, 13))
    elif kind == "sparkle":
        p.drawPolygon(QPolygonF([
            _p(10, 2.5), _p(11.5, 7), _p(16, 8.5), _p(11.5, 10), _p(10, 14.5),
            _p(8.5, 10), _p(4, 8.5), _p(8.5, 7),
        ]))
        p.drawLine(_p(15.5, 13.5), _p(15.5, 17.5))
        p.drawLine(_p(13.5, 15.5), _p(17.5, 15.5))
    elif kind == "wallet":
        p.drawRoundedRect(QRectF(3, 5, 14, 11), 2.5, 2.5)
        p.drawLine(_p(5, 5), _p(14, 3))
        p.drawRoundedRect(QRectF(12, 8.5, 5, 4), 1.5, 1.5)
    elif kind == "debt":
        p.drawLine(_p(4, 7), _p(16, 7))
        p.drawPolyline(QPolygonF([_p(13, 4), _p(16, 7), _p(13, 10)]))
        p.drawLine(_p(16, 13), _p(4, 13))
        p.drawPolyline(QPolygonF([_p(7, 10), _p(4, 13), _p(7, 16)]))
    elif kind == "tag":
        p.drawPolygon(QPolygonF([_p(3, 3), _p(10.5, 3), _p(17, 9.5), _p(9.5, 17), _p(3, 10.5)]))
        p.drawPoint(_p(7, 7))
    elif kind == "users":
        p.drawEllipse(_p(8, 7), 3, 3)
        p.drawArc(QRectF(2.5, 12, 11, 8), 0, 180 * 16)
        p.drawArc(QRectF(11, 3.5, 6, 7), -90 * 16, 180 * 16)
        p.drawArc(QRectF(13, 12, 6, 8), 0, 90 * 16)
    elif kind == "clock":
        p.drawEllipse(_p(10, 10), 7, 7)
        p.drawPolyline(QPolygonF([_p(10, 6), _p(10, 10), _p(12.8, 11.8)]))
    elif kind == "bell":
        path = QPainterPath(_p(5, 13.5))
        path.lineTo(_p(5, 9))
        path.cubicTo(_p(5, 2.5), _p(15, 2.5), _p(15, 9))
        path.lineTo(_p(15, 13.5))
        path.lineTo(_p(16.5, 15))
        path.lineTo(_p(3.5, 15))
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(_p(8.5, 17.5), _p(11.5, 17.5))
    elif kind == "copy":
        p.drawRoundedRect(QRectF(6.5, 6.5, 9.5, 10.5), 2, 2)
        p.drawPolyline(QPolygonF([_p(4, 13.5), _p(4, 4), _p(13.5, 4)]))
    elif kind == "eye":
        path = QPainterPath()
        path.moveTo(3, 10)
        path.cubicTo(6.5, 5.5, 13.5, 5.5, 17, 10)
        path.cubicTo(13.5, 14.5, 6.5, 14.5, 3, 10)
        p.drawPath(path)
        p.drawEllipse(_p(10, 10), 2.2, 2.2)
    elif kind == "eye-off":
        path = QPainterPath()
        path.moveTo(3, 10)
        path.cubicTo(6.5, 5.5, 13.5, 5.5, 17, 10)
        path.cubicTo(13.5, 14.5, 6.5, 14.5, 3, 10)
        p.drawPath(path)
        p.drawEllipse(_p(10, 10), 2.2, 2.2)
        p.drawLine(_p(4, 4), _p(16, 16))
    elif kind == "instagram":
        p.drawRoundedRect(QRectF(3.5, 3.5, 13, 13), 3.5, 3.5)
        p.drawEllipse(_p(10, 10), 3.2, 3.2)
        p.drawPoint(_p(13.5, 6.5))


def _pixmap(kind, color, size, width):
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size * scale / 20, size * scale / 20)
    pen = QPen(QColor(color), 2.0 if kind == "send" else width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    _draw(p, kind)
    p.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def line_icon(kind, color, size=20, width=1.6, checked_color=None):
    """A QIcon of the named outline drawing in `color`.

    `checked_color`, when given, is used while a checkable button is on, so an
    icon stays readable on a filled selection.
    """
    icon = QIcon()
    states = [(QIcon.State.Off, _pixmap(kind, color, size, width))]
    states.append((QIcon.State.On, _pixmap(kind, checked_color, size, width) if checked_color else states[0][1]))
    # The same drawing for every mode, so Qt does not grey it out itself.
    for state, pixmap in states:
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Disabled, QIcon.Mode.Selected):
            icon.addPixmap(pixmap, mode, state)
    return icon
