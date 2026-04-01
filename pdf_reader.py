#!/usr/bin/python3
"""PDF Reader — main application entry point."""

import sys
import fitz  # PyMuPDF
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QListWidget, QListWidgetItem, QToolBar, QAction,
    QFileDialog, QLabel, QSizePolicy, QShortcut,
    QMessageBox, QStatusBar,
)
from PyQt5.QtCore import Qt, QThread, QSize
from PyQt5.QtGui import QIcon, QKeySequence, QFont

from pdf_view import PDFGraphicsView, ThumbnailWorker, FONT_SIZE_STEP

THUMB_WIDTH = 140
THUMB_HEIGHT = 190
SIDEBAR_WIDTH = 165


class PDFReader(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Reader")
        self.resize(1100, 780)

        self._doc: fitz.Document | None = None
        self._doc_path: str = ""
        self._current_page: int = 0
        self._thumb_thread: QThread | None = None
        self._thumb_worker: ThumbnailWorker | None = None

        self._build_ui()
        self._build_shortcuts()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Toolbar
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        open_action = QAction("Open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.setToolTip("Open PDF file (Ctrl+O)")
        open_action.triggered.connect(self.open_file)
        toolbar.addAction(open_action)

        self._save_action = QAction("Save", self)
        self._save_action.setShortcut("Ctrl+S")
        self._save_action.setToolTip("Save changes to file (Ctrl+S)")
        self._save_action.setEnabled(False)
        self._save_action.triggered.connect(self.save_file)
        toolbar.addAction(self._save_action)

        self._save_as_action = QAction("Save As", self)
        self._save_as_action.setShortcut("Ctrl+Shift+S")
        self._save_as_action.setToolTip("Save to a new file (Ctrl+Shift+S)")
        self._save_as_action.setEnabled(False)
        self._save_as_action.triggered.connect(self.save_file_as)
        toolbar.addAction(self._save_as_action)

        toolbar.addSeparator()

        self._prev_action = QAction("◀  Prev", self)
        self._prev_action.setToolTip("Previous page (Left / PgUp)")
        self._prev_action.setEnabled(False)
        self._prev_action.triggered.connect(self.prev_page)
        toolbar.addAction(self._prev_action)

        self._page_label = QLabel("  —  ")
        self._page_label.setAlignment(Qt.AlignCenter)
        self._page_label.setMinimumWidth(90)
        toolbar.addWidget(self._page_label)

        self._next_action = QAction("Next  ▶", self)
        self._next_action.setToolTip("Next page (Right / PgDn)")
        self._next_action.setEnabled(False)
        self._next_action.triggered.connect(self.next_page)
        toolbar.addAction(self._next_action)

        toolbar.addSeparator()

        zoom_out_action = QAction("−  Zoom Out", self)
        zoom_out_action.setToolTip("Zoom out (Ctrl+-)")
        zoom_out_action.setShortcut("Ctrl+-")
        zoom_out_action.triggered.connect(lambda: self._pdf_view.zoom_out())
        toolbar.addAction(zoom_out_action)

        self._zoom_label = QLabel("  100%  ")
        self._zoom_label.setAlignment(Qt.AlignCenter)
        self._zoom_label.setMinimumWidth(60)
        toolbar.addWidget(self._zoom_label)

        zoom_in_action = QAction("+  Zoom In", self)
        zoom_in_action.setToolTip("Zoom in (Ctrl++)")
        zoom_in_action.setShortcut("Ctrl+=")
        zoom_in_action.triggered.connect(lambda: self._pdf_view.zoom_in())
        toolbar.addAction(zoom_in_action)

        zoom_reset_action = QAction("100%", self)
        zoom_reset_action.setToolTip("Reset zoom (Ctrl+0)")
        zoom_reset_action.setShortcut("Ctrl+0")
        zoom_reset_action.triggered.connect(lambda: self._pdf_view.zoom_reset())
        toolbar.addAction(zoom_reset_action)

        fit_width_action = QAction("Fit Width", self)
        fit_width_action.setToolTip("Fit page to window width (Ctrl+W)")
        fit_width_action.setShortcut("Ctrl+Shift+W")
        fit_width_action.triggered.connect(lambda: self._pdf_view.fit_to_width())
        toolbar.addAction(fit_width_action)

        fit_page_action = QAction("Fit Page", self)
        fit_page_action.setToolTip("Fit full page in view (Ctrl+Shift+F)")
        fit_page_action.setShortcut("Ctrl+Shift+F")
        fit_page_action.triggered.connect(lambda: self._pdf_view.fit_to_page())
        toolbar.addAction(fit_page_action)

        toolbar.addSeparator()

        field_font_dec_action = QAction("A−", self)
        field_font_dec_action.setToolTip("Decrease annotation font size (Ctrl+[)")
        field_font_dec_action.setShortcut("Ctrl+[")
        field_font_dec_action.triggered.connect(
            lambda: self._pdf_view.adjust_field_font_size(-FONT_SIZE_STEP)
        )
        toolbar.addAction(field_font_dec_action)

        field_font_inc_action = QAction("A+", self)
        field_font_inc_action.setToolTip("Increase annotation font size (Ctrl+])")
        field_font_inc_action.setShortcut("Ctrl+]")
        field_font_inc_action.triggered.connect(
            lambda: self._pdf_view.adjust_field_font_size(FONT_SIZE_STEP)
        )
        toolbar.addAction(field_font_inc_action)

        # Central widget: splitter with sidebar + main view
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setHandleWidth(4)

        # Left: thumbnail sidebar
        self._thumb_list = QListWidget()
        self._thumb_list.setViewMode(QListWidget.IconMode)
        self._thumb_list.setIconSize(QSize(THUMB_WIDTH, THUMB_HEIGHT))
        self._thumb_list.setGridSize(QSize(THUMB_WIDTH + 16, THUMB_HEIGHT + 28))
        self._thumb_list.setResizeMode(QListWidget.Adjust)
        self._thumb_list.setMovement(QListWidget.Static)
        self._thumb_list.setSpacing(4)
        self._thumb_list.setFixedWidth(SIDEBAR_WIDTH)
        self._thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._thumb_list.setStyleSheet("""
            QListWidget {
                background: #2b2b2b;
                border: none;
                outline: none;
            }
            QListWidget::item {
                color: #cccccc;
                font-size: 10px;
                padding-bottom: 2px;
            }
            QListWidget::item:selected {
                background: #3a5f8a;
                border-radius: 3px;
            }
        """)
        self._thumb_list.itemClicked.connect(self._on_thumbnail_clicked)
        splitter.addWidget(self._thumb_list)

        # Right: PDF view
        self._pdf_view = PDFGraphicsView()
        self._pdf_view.zoom_changed.connect(self._on_zoom_changed)
        self._pdf_view.page_modified.connect(self._render_current_page)
        self._pdf_view.status_message.connect(lambda msg: self._status.showMessage(msg, 4000))
        splitter.addWidget(self._pdf_view)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.setCentralWidget(splitter)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_label = QLabel("No file open")
        self._status.addPermanentWidget(self._status_label)

    def _build_shortcuts(self):
        for key in (Qt.Key_Left, Qt.Key_PageUp):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(self.prev_page)
        for key in (Qt.Key_Right, Qt.Key_PageDown):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(self.next_page)
        # Ctrl++ (some keyboards send Ctrl+Shift+=)
        sc_zi = QShortcut(QKeySequence("Ctrl++"), self)
        sc_zi.activated.connect(self._pdf_view.zoom_in)

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PDF", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if path:
            self._load_pdf(path)

    def save_file(self):
        if not self._doc:
            return
        try:
            self._doc.save(self._doc_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            self._status_label.setText(f"Saved — {self._doc_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save file:\n{exc}")

    def save_file_as(self):
        if not self._doc:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF As", self._doc_path, "PDF Files (*.pdf);;All Files (*)"
        )
        if not path:
            return
        try:
            self._doc.save(path)
            self._doc_path = path
            self.setWindowTitle(f"PDF Reader — {path.split('/')[-1]}")
            self._status_label.setText(f"Saved — {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save file:\n{exc}")

    def _load_pdf(self, path: str):
        # Cancel any in-progress thumbnail generation
        self._cancel_thumbnail_worker()

        try:
            doc = fitz.open(path)
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Could not open file:\n{exc}")
            return

        if self._doc:
            self._doc.close()

        self._doc = doc
        self._doc_path = path
        self._current_page = 0

        self._thumb_list.clear()
        self._prev_action.setEnabled(True)
        self._next_action.setEnabled(True)
        self._save_action.setEnabled(True)
        self._save_as_action.setEnabled(True)

        self.setWindowTitle(f"PDF Reader — {path.split('/')[-1]}")
        self._render_current_page()
        self._start_thumbnail_worker()
        self._update_status()

    # ------------------------------------------------------------------
    # Page navigation
    # ------------------------------------------------------------------

    def prev_page(self):
        if self._doc and self._current_page > 0:
            self._current_page -= 1
            self._render_current_page()
            self._sync_thumbnail_selection()
            self._update_status()

    def next_page(self):
        if self._doc and self._current_page < len(self._doc) - 1:
            self._current_page += 1
            self._render_current_page()
            self._sync_thumbnail_selection()
            self._update_status()

    def _render_current_page(self):
        if self._doc:
            self._pdf_view.render_page(self._doc, self._current_page)

    def _on_thumbnail_clicked(self, item: QListWidgetItem):
        page_num = item.data(Qt.UserRole)
        if page_num is not None and page_num != self._current_page:
            self._current_page = page_num
            self._render_current_page()
            self._update_status()

    def _sync_thumbnail_selection(self):
        item = self._thumb_list.item(self._current_page)
        if item:
            self._thumb_list.setCurrentItem(item)
            self._thumb_list.scrollToItem(item)

    # ------------------------------------------------------------------
    # Thumbnail generation
    # ------------------------------------------------------------------

    def _start_thumbnail_worker(self):
        if not self._doc:
            return
        page_count = len(self._doc)

        # Pre-populate sidebar with placeholder items so page numbers show up
        for i in range(page_count):
            item = QListWidgetItem(f"Page {i + 1}")
            item.setData(Qt.UserRole, i)
            item.setSizeHint(QSize(THUMB_WIDTH + 16, THUMB_HEIGHT + 28))
            self._thumb_list.addItem(item)

        self._thumb_list.setCurrentRow(0)

        thread = QThread(self)
        worker = ThumbnailWorker(self._doc_path, page_count)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        # Clear refs when the thread finishes naturally so closeEvent doesn't
        # call isRunning() on an already-deleted C++ QThread object.
        thread.finished.connect(self._on_thumb_thread_finished)

        self._thumb_thread = thread
        self._thumb_worker = worker
        thread.start()

    def _on_thumb_thread_finished(self):
        self._thumb_thread = None
        self._thumb_worker = None

    def _on_thumbnail_ready(self, page_num: int, pixmap):
        item = self._thumb_list.item(page_num)
        if item:
            item.setIcon(QIcon(pixmap))

    def _cancel_thumbnail_worker(self):
        if self._thumb_worker:
            self._thumb_worker.cancel()
        if self._thumb_thread and self._thumb_thread.isRunning():
            self._thumb_thread.quit()
            self._thumb_thread.wait(2000)
        self._thumb_thread = None
        self._thumb_worker = None

    # ------------------------------------------------------------------
    # Status / zoom updates
    # ------------------------------------------------------------------

    def _on_zoom_changed(self, factor: float):
        self._zoom_label.setText(f"  {int(factor * 100)}%  ")

    def _update_status(self):
        if self._doc:
            total = len(self._doc)
            self._page_label.setText(f"  {self._current_page + 1} / {total}  ")
            self._status_label.setText(
                f"{self._doc_path}   |   Page {self._current_page + 1} of {total}"
            )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        self._cancel_thumbnail_worker()
        if self._doc:
            self._doc.close()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Reader")
    app.setStyle("Fusion")

    # Dark palette for Fusion style
    from PyQt5.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(35, 35, 35))
    palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ToolTipBase, QColor(25, 25, 25))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(55, 55, 55))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, Qt.red)
    palette.setColor(QPalette.Link, QColor(42, 130, 218))
    palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))
    app.setPalette(palette)

    window = PDFReader()
    window.show()

    # Allow passing a PDF path as a command-line argument
    if len(sys.argv) > 1:
        window._load_pdf(sys.argv[1])

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
