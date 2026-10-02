"""Keep a small badge pinned to the right edge of the button it sits on.

A badge placed with ``move()`` stays where it was put. When the button grows
or shrinks - the sidebar divider being dragged, the window resized - the badge
used to wait for something else to put it back, and jumped there in steps.
Pinning it re-places it on every resize of the button, so it glides with the
edge frame by frame.
"""

from PyQt6.QtCore import QEvent, QObject

_PLACE_ON = (QEvent.Type.Resize, QEvent.Type.Show)


class _RightEdgePin(QObject):
    def __init__(self, badge, right, top):
        super().__init__(badge)
        self._badge = badge
        self._host = badge.parentWidget()
        self._right = right
        self._top = top
        # The host moves the edge; the badge itself widens when its count
        # gains a digit.
        self._host.installEventFilter(self)
        badge.installEventFilter(self)
        self.place()

    def place(self):
        x = max(self._host.width() - self._badge.width() - self._right, 0)
        if self._badge.x() != x or self._badge.y() != self._top:
            self._badge.move(x, self._top)

    def eventFilter(self, obj, event):
        if event.type() in _PLACE_ON:
            self.place()
        return False


def pin_to_right_edge(badge, right, top):
    """Keep ``badge`` ``right`` px from its parent's right edge, ``top`` px down."""
    return _RightEdgePin(badge, right, top)
