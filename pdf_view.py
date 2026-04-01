#!/usr/bin/python3
"""PDF rendering view and thumbnail worker."""

import fitz  # PyMuPDF
from PyQt5.QtWidgets import QGraphicsView, QGraphicsScene
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, QRectF
from PyQt5.QtGui import QImage, QPixmap, QTransform, QCursor


BASE_DPI_MATRIX = fitz.Matrix(2.0, 2.0)   # 2x render for crisp zoom
THUMB_MATRIX = fitz.Matrix(0.2, 0.2)      # ~20% for sidebar thumbnails
ZOOM_STEP = 0.15
ZOOM_MIN = 0.1
ZOOM_MAX = 8.0


def _fitz_page_to_pixmap(page, matrix: fitz.Matrix) -> QPixmap:
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return QPixmap.fromImage(img)


class ThumbnailWorker(QObject):
    """Generates page thumbnails in a background thread."""

    thumbnail_ready = pyqtSignal(int, QPixmap)   # page_index, pixmap
    finished = pyqtSignal()

    def __init__(self, doc_path: str, page_count: int):
        super().__init__()
        self.doc_path = doc_path
        self.page_count = page_count
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        doc = fitz.open(self.doc_path)
        try:
            for i in range(self.page_count):
                if self._cancelled:
                    break
                page = doc[i]
                pixmap = _fitz_page_to_pixmap(page, THUMB_MATRIX)
                self.thumbnail_ready.emit(i, pixmap)
        finally:
            doc.close()
            self.finished.emit()


class PDFGraphicsView(QGraphicsView):
    """Zoomable, pannable PDF page viewer."""

    zoom_changed = pyqtSignal(float)   # emits current zoom factor

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._zoom_factor = 1.0

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(self.renderHints())
        self.setBackgroundBrush(Qt.darkGray)
        self.setAlignment(Qt.AlignCenter)

    def render_page(self, doc: fitz.Document, page_num: int):
        page = doc[page_num]
        pixmap = _fitz_page_to_pixmap(page, BASE_DPI_MATRIX)
        self._scene.clear()
        item = self._scene.addPixmap(pixmap)
        # Add a thin border shadow effect by offsetting a rect behind the page
        rect = QRectF(pixmap.rect())
        self._scene.setSceneRect(rect.adjusted(-20, -20, 20, 20))
        self.resetTransform()
        self._zoom_factor = 1.0
        self.centerOn(item)
        self.zoom_changed.emit(self._zoom_factor)

    def zoom_in(self):
        self._apply_zoom(1 + ZOOM_STEP)

    def zoom_out(self):
        self._apply_zoom(1.0 / (1 + ZOOM_STEP))

    def zoom_reset(self):
        self.resetTransform()
        self._zoom_factor = 1.0
        self.zoom_changed.emit(self._zoom_factor)

    def fit_to_width(self):
        scene_width = self._scene.sceneRect().width()
        view_width = self.viewport().width()
        if scene_width > 0 and view_width > 0:
            factor = view_width / scene_width
            self.resetTransform()
            self.scale(factor, factor)
            self._zoom_factor = factor
            self.zoom_changed.emit(self._zoom_factor)

    def fit_to_page(self):
        self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)
        # Calculate resulting zoom factor from the current transform
        self._zoom_factor = self.transform().m11()
        self.zoom_changed.emit(self._zoom_factor)

    def _apply_zoom(self, factor: float):
        new_zoom = self._zoom_factor * factor
        if ZOOM_MIN <= new_zoom <= ZOOM_MAX:
            self.scale(factor, factor)
            self._zoom_factor = new_zoom
            self.zoom_changed.emit(self._zoom_factor)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
        else:
            super().wheelEvent(event)
