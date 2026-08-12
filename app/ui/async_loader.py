from PyQt6.QtCore import QObject, QDateTime, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox, QProgressBar


class DataLoadWorker(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fetch_fn):
        super().__init__()
        self.fetch_fn = fetch_fn

    def run(self):
        try:
            self.finished.emit(self.fetch_fn())
        except Exception as exc:
            self.failed.emit(str(exc))


class AsyncDataLoader(QObject):
    apply_requested = pyqtSignal(object)

    def __init__(self, owner, progress_bar=None):
        super().__init__(owner)
        self.owner = owner
        self.progress_bar = progress_bar
        self.thread = None
        self.worker = None
        self.pending = None
        self.apply_fn = None
        self.started_at_ms = 0
        self.minimum_visible_ms = 350
        self.apply_requested.connect(self._apply_result)

    def start(self, fetch_fn, apply_fn):
        if self.thread and self.thread.isRunning():
            self.pending = (fetch_fn, apply_fn)
            return
        self.apply_fn = apply_fn
        if self.progress_bar:
            self.started_at_ms = QDateTime.currentMSecsSinceEpoch()
            self.progress_bar.setVisible(True)
        self.thread = QThread(self.owner)
        self.worker = DataLoadWorker(fetch_fn)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.apply_requested)
        self.worker.failed.connect(self._failed)
        self.thread.start()

    @pyqtSlot(object)
    def _apply_result(self, result):
        if self.apply_fn:
            self.apply_fn(result)
        self._complete_thread()

    @pyqtSlot(str)
    def _failed(self, message):
        QMessageBox.warning(self.owner, "Xatolik", message)
        self._complete_thread()

    def _complete_thread(self):
        if self.thread:
            self.thread.quit()
            self.thread.wait(3000)
        self.thread = None
        self.worker = None
        self.apply_fn = None
        if self.pending:
            fetch_fn, apply_fn = self.pending
            self.pending = None
            QTimer.singleShot(0, lambda: self.start(fetch_fn, apply_fn))
            return
        if self.progress_bar:
            elapsed = QDateTime.currentMSecsSinceEpoch() - self.started_at_ms
            delay = max(0, self.minimum_visible_ms - elapsed)
            QTimer.singleShot(delay, lambda: self.progress_bar.setVisible(False))


def make_progress_bar():
    bar = QProgressBar()
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedHeight(4)
    bar.setStyleSheet("""
        QProgressBar {
            background:#e2e8f0;
            border:none;
            border-radius:2px;
        }
        QProgressBar::chunk {
            background:#3b82f6;
            border-radius:2px;
        }
    """)
    bar.hide()
    return bar
