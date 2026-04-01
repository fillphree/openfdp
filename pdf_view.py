#!/usr/bin/python3
"""PDF rendering view and thumbnail worker."""

import fitz  # PyMuPDF
from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QLineEdit, QGraphicsProxyWidget,
)
from PyQt5.QtCore import Qt, QObject, QThread, pyqtSignal, QRectF
from PyQt5.QtGui import QImage, QPixmap, QPainter, QFont


BASE_DPI_MATRIX = fitz.Matrix(2.0, 2.0)   # 2x render for crisp display
THUMB_MATRIX = fitz.Matrix(0.2, 0.2)      # ~20% for sidebar thumbnails
ZOOM_STEP = 0.15
ZOOM_MIN = 0.1
ZOOM_MAX = 8.0
FONT_SIZE_STEP = 1.0
FONT_SIZE_MIN = 4.0
FONT_SIZE_MAX = 72.0
DEFAULT_FONT_SIZE = 12.0
ANNOT_DEFAULT_WIDTH = 200.0   # PDF points — width of a newly placed annotation


def _fitz_page_to_pixmap(page, matrix: fitz.Matrix) -> QPixmap:
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888)
    return QPixmap.fromImage(img)


# ---------------------------------------------------------------------------
# QLineEdit subclass so Escape key can cancel an in-progress edit
# ---------------------------------------------------------------------------

class _AnnotLineEdit(QLineEdit):
    escape_pressed = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.escape_pressed.emit()
        else:
            super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# Thumbnail worker
# ---------------------------------------------------------------------------

class ThumbnailWorker(QObject):
    """Generates page thumbnails in a background thread."""

    thumbnail_ready = pyqtSignal(int, QPixmap)
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


# ---------------------------------------------------------------------------
# Main PDF view
# ---------------------------------------------------------------------------

class PDFGraphicsView(QGraphicsView):
    """Zoomable, pannable PDF page viewer with free-text annotation editing.

    Usage:
      - Single-click an existing annotation  → open editor on it
      - Double-click empty page area          → place a new annotation
      - Enter                                 → commit / save
      - Escape                                → cancel (no change)
      - Click outside editor                  → commit / save
    """

    zoom_changed = pyqtSignal(float)
    page_modified = pyqtSignal()
    status_message = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._zoom_factor = 1.0

        self._doc: fitz.Document | None = None
        self._page_num: int = 0

        # Editor state
        self._active_proxy: QGraphicsProxyWidget | None = None
        self._active_edit: _AnnotLineEdit | None = None
        self._active_annot_xref: int | None = None  # xref of annot being edited
        self._active_annot_rect = None  # fitz.Rect of annot being edited
        self._new_annot_rect = None     # fitz.Rect for a brand-new annotation
        self._active_font_size: float = DEFAULT_FONT_SIZE

        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setBackgroundBrush(Qt.darkGray)
        self.setAlignment(Qt.AlignCenter)

    # ------------------------------------------------------------------
    # Page rendering
    # ------------------------------------------------------------------

    def render_page(self, doc: fitz.Document, page_num: int):
        # Commit any pending edit before wiping the scene
        if self._active_proxy is not None:
            self._commit_editor()
        self._clear_editor_state()

        self._doc = doc
        self._page_num = page_num
        page = doc[page_num]
        pixmap = _fitz_page_to_pixmap(page, BASE_DPI_MATRIX)
        self._scene.clear()
        item = self._scene.addPixmap(pixmap)
        rect = QRectF(pixmap.rect())
        self._scene.setSceneRect(rect.adjusted(-20, -20, 20, 20))
        self.resetTransform()
        self._zoom_factor = 1.0
        self.centerOn(item)
        self.zoom_changed.emit(self._zoom_factor)

    # ------------------------------------------------------------------
    # Mouse handling
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._doc is not None:
            scene_pos = self.mapToScene(event.pos())

            # If editor is open and click is inside it, pass event to the QLineEdit
            if self._active_proxy is not None:
                proxy_rect = self._active_proxy.mapRectToScene(
                    self._active_proxy.boundingRect()
                )
                if proxy_rect.contains(scene_pos):
                    super().mousePressEvent(event)
                    return

            pdf_pt = fitz.Point(scene_pos.x() / 2.0, scene_pos.y() / 2.0)

            # Single-click on an existing free-text annotation → edit it
            page = self._doc[self._page_num]
            for annot in page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]):
                if annot.rect.contains(pdf_pt):
                    self._open_annotation_editor(annot)
                    event.accept()
                    return

            # Click elsewhere → commit and close any open editor (no new annotation)
            if self._active_proxy is not None:
                self._dismiss_editor(commit=True)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._doc is not None:
            scene_pos = self.mapToScene(event.pos())
            pdf_pt = fitz.Point(scene_pos.x() / 2.0, scene_pos.y() / 2.0)

            # Double-click on an existing annotation → edit it
            page = self._doc[self._page_num]
            for annot in page.annots(types=[fitz.PDF_ANNOT_FREE_TEXT]):
                if annot.rect.contains(pdf_pt):
                    self._open_annotation_editor(annot)
                    event.accept()
                    return

            # Double-click on empty space → create new annotation here
            self._dismiss_editor(commit=True)
            h = self._active_font_size * 1.5 + 4
            new_rect = fitz.Rect(
                pdf_pt.x, pdf_pt.y,
                pdf_pt.x + ANNOT_DEFAULT_WIDTH, pdf_pt.y + h,
            )
            self._open_new_annotation_editor(new_rect)
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Annotation editor
    # ------------------------------------------------------------------

    def _open_annotation_editor(self, annot):
        """Open the inline editor over an existing annotation."""
        self._dismiss_editor(commit=True)
        # Store xref + rect only — the fitz.Annot object itself becomes invalid
        # once its parent page is garbage-collected, so we never hold onto it.
        self._active_annot_xref = annot.xref
        self._active_annot_rect = fitz.Rect(annot.rect)  # copy, not a live reference
        self._new_annot_rect = None
        text = annot.info.get("content", "")
        self._show_editor_overlay(self._active_annot_rect, text)
        self.status_message.emit(
            "Editing annotation — Enter to save, Esc to cancel"
        )

    def _open_new_annotation_editor(self, pdf_rect):
        """Open the inline editor for a brand-new annotation."""
        self._active_annot = None
        self._new_annot_rect = pdf_rect
        self._show_editor_overlay(pdf_rect, "")
        self.status_message.emit(
            "New annotation — type text, Enter to place, Esc to cancel"
        )

    def _show_editor_overlay(self, pdf_rect, initial_text: str):
        x = pdf_rect.x0 * 2.0
        y = pdf_rect.y0 * 2.0
        w = (pdf_rect.x1 - pdf_rect.x0) * 2.0
        h = (pdf_rect.y1 - pdf_rect.y0) * 2.0

        edit = _AnnotLineEdit()
        edit.setText(initial_text)
        edit.setFrame(False)
        edit.setStyleSheet(
            "background: rgba(255, 255, 200, 230); color: black; padding: 1px;"
        )
        font = QFont()
        font.setPixelSize(max(6, int(self._active_font_size * 2.0)))
        edit.setFont(font)

        proxy = QGraphicsProxyWidget()
        proxy.setWidget(edit)
        proxy.setGeometry(QRectF(x, y, w, h))
        self._scene.addItem(proxy)

        self._active_proxy = proxy
        self._active_edit = edit

        # Disable pan-drag so clicks inside the editor reach the QLineEdit
        self.setDragMode(QGraphicsView.NoDrag)

        edit.returnPressed.connect(lambda: self._dismiss_editor(commit=True))
        edit.escape_pressed.connect(lambda: self._dismiss_editor(commit=False))
        proxy.setFocus()
        edit.setFocus(Qt.OtherFocusReason)
        edit.selectAll()

    def _commit_editor(self) -> bool:
        """Write editor content to the fitz document. Returns True if changed."""
        if self._active_edit is None or self._doc is None:
            return False
        text = self._active_edit.text().strip()
        page = self._doc[self._page_num]

        if self._active_annot_xref is not None:
            # Look up the annotation fresh from the page by xref — the stored
            # fitz.Annot object would be invalid (page was garbage-collected).
            target = None
            for annot in page.annots():
                if annot.xref == self._active_annot_xref:
                    target = annot
                    break
            if target is None:
                return False  # annotation was already removed
            if text:
                target.set_info(content=text)
                target.update(fontsize=self._active_font_size)
            else:
                page.delete_annot(target)
            return True

        if self._new_annot_rect is not None and text:
            # Create new annotation only if the user typed something
            page.add_freetext_annot(
                self._new_annot_rect,
                text,
                fontsize=self._active_font_size,
                fontname="helv",
                text_color=(0, 0, 0),
                fill_color=(1, 1, 0.8),
                align=fitz.TEXT_ALIGN_LEFT,
            )
            return True

        return False

    def _dismiss_editor(self, commit: bool = True):
        if self._active_proxy is None:
            return
        changed = self._commit_editor() if commit else False
        try:
            self._scene.removeItem(self._active_proxy)
        except Exception:
            pass
        self._clear_editor_state()
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        if changed:
            self.page_modified.emit()

    def _clear_editor_state(self):
        self._active_proxy = None
        self._active_edit = None
        self._active_annot_xref = None
        self._active_annot_rect = None
        self._new_annot_rect = None

    def adjust_field_font_size(self, delta: float):
        """Adjust the annotation font size (affects current editor and future annotations)."""
        self._active_font_size = max(
            FONT_SIZE_MIN, min(FONT_SIZE_MAX, self._active_font_size + delta)
        )
        if self._active_edit is not None:
            font = QFont()
            font.setPixelSize(max(6, int(self._active_font_size * 2.0)))
            self._active_edit.setFont(font)
        self.status_message.emit(f"Annotation font size: {self._active_font_size:.0f}pt")

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

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
