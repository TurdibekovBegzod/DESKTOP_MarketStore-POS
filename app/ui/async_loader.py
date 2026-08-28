from PyQt6.QtCore import QObject, QDateTime, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox, QProgressBar


def set_progress_bar_loading(bar, loading):
    """Toggle animation without adding/removing the bar from its layout."""
    if bar is None:
        return
    bar.setProperty("loading", bool(loading))
    if loading:
        bar.setRange(0, 0)
    else:
        bar.setRange(0, 1)
        bar.setValue(0)
    # The transparent idle rail keeps four pixels reserved, so every toolbar
    # and table stays exactly where it was while data refreshes.
    bar.setVisible(True)
    style = bar.style()
    style.unpolish(bar)
    style.polish(bar)
    bar.update()


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
        self._progress_epoch = 0
        self.apply_requested.connect(self._apply_result)

    def start(self, fetch_fn, apply_fn):
        if self.thread and self.thread.isRunning():
            self.pending = (fetch_fn, apply_fn)
            return
        self.apply_fn = apply_fn
        if self.progress_bar:
            self._progress_epoch += 1
            self.started_at_ms = QDateTime.currentMSecsSinceEpoch()
            set_progress_bar_loading(self.progress_bar, True)
        self.thread = QThread(self.owner)
        self.worker = DataLoadWorker(fetch_fn)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.apply_requested)
        self.worker.failed.connect(self._failed)
        self.thread.start()

    @pyqtSlot(object)
    def _apply_result(self, result):
        # A sync may request a fresh read while an older query is still in
        # flight. In that case the pending query is the only snapshot worth
        # painting; briefly applying the old one can leave an open product
        # table looking stale until the next interaction.
        if self.apply_fn and self.pending is None:
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
            epoch = self._progress_epoch
            QTimer.singleShot(delay, lambda: self._finish_progress(epoch))

    def _finish_progress(self, epoch):
        if epoch != self._progress_epoch or self.thread or self.pending:
            return
        set_progress_bar_loading(self.progress_bar, False)


def make_progress_bar():
    bar = QProgressBar()
    bar.setTextVisible(False)
    bar.setFixedHeight(4)
    bar.setStyleSheet("""
        QProgressBar[loading="true"] {
            background:#e2e8f0;
            border:none;
            border-radius:2px;
        }
        QProgressBar[loading="true"]::chunk {
            background:#3b82f6;
            border-radius:2px;
        }
        QProgressBar[loading="false"] {
            background:transparent;
            border:none;
        }
        QProgressBar[loading="false"]::chunk {
            background:transparent;
        }
    """)
    set_progress_bar_loading(bar, False)
    return bar
