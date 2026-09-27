"""partition bar - horizontal disk-layout viz with drag-resize"""

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QToolTip, QWidget

from emu68hatcher.config.constants import (
    CYLINDER_SIZE,
    MIN_AMIGA_PARTITION_SIZE,
    PFS3_MAX_PARTITION_SIZE,
)
from emu68hatcher.config.partition_helpers import round_to_cylinder
from emu68hatcher.config.partition_models import Filesystem

BOOT_COLOR = QColor("#546E7A")  # blue-gray
AMIGA_COLORS = [
    QColor("#009688"),  # teal
    QColor("#FF9800"),  # orange
    QColor("#4CAF50"),  # green
    QColor("#9C27B0"),  # purple
    QColor("#F44336"),  # red
    QColor("#3F51B5"),  # indigo
]
FREE_COLOR = QColor("#424242")  # dark gray
SELECTED_BORDER = QColor("#FFEB3B")  # yellow highlight
FRAME_COLOR = QColor("#90A4AE")  # container chrome - frames, captions, strip text; never a fill
BAND_BG = QColor("#37474F")
RDB_BADGE = QColor("#263238")
RDB_BADGE_BORDER = QColor("#607D8B")
FREE_HATCH = QColor("#616161")  # diagonal hatch marks absence, solid fills mean data


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024**3:
        return f"{size_bytes / (1024**3):.1f} GiB"
    return f"{size_bytes // (1024**2)} MiB"


def _tooltip(label: str, size: int, sublabel: str) -> str:
    return f"{label}\n{_format_size(size)}\n{sublabel}"


class PartitionBar(QWidget):
    """horizontal bar - proportional partition sizes with drag-resize"""

    GRAB_ZONE = 7  # pixels from border edge that activate resize
    STRIP_H = 22  # callout labels for outer segments too thin to label inline
    BAND_H = 24  # container caption band inside the frame
    partition_clicked = Signal(int)  # amiga partition index

    def __init__(self, parent=None, *, interactive=True):
        super().__init__(parent)
        self._interactive = interactive
        self.setMinimumHeight(128)
        self.setMaximumHeight(144)
        self.setMouseTracking(True)
        self._segments: list[tuple[str, int, str, QColor, bool]] = []
        self._rects: list[tuple[QRect, str]] = []
        self._on_resize_callback = None  # callable(left_idx, left_size, right_idx, right_size)
        # drag state
        self._borders: list[tuple[int, int, int]] = []
        self._dragging = False
        self._drag_border_idx = -1
        self._drag_start_x = 0
        self._bytes_per_pixel = 1.0
        self._amiga_partitions = []
        self._free_space = 0
        self._shortfall = 0
        self._children_rect: QRect | None = None
        self._name_font = QFont()
        self._name_font.setPointSize(11)
        self._name_font.setBold(True)
        self._sub_font = QFont()
        self._sub_font.setPointSize(10)

    def set_data(self, boot_size: int, amiga_partitions, free_space: int, selected: int = -1):
        self._amiga_partitions = list(amiga_partitions)
        self._free_space = max(0, free_space)
        self._shortfall = max(0, -free_space)
        self._segments = []
        self._segments.append(("EMU68BOOT", boot_size, "FAT32", BOOT_COLOR, False))
        for i, p in enumerate(amiga_partitions):
            color = AMIGA_COLORS[i % len(AMIGA_COLORS)]
            star = " *" if p.bootable else ""
            sublabel = f"{p.filesystem.value}{star}"
            if p.ags_reservation:
                sublabel += "; from AGS image · fixed size"
            self._segments.append((p.volume, p.size, sublabel, color, i == selected))
        if free_space > 0:
            self._segments.append(("free", free_space, "", FREE_COLOR, False))
        self.update()

    def _resizable_border(self, seg_idx: int) -> bool:
        """true for amiga|amiga and amiga|free; boot|first-amiga (0|1) is locked"""
        # seg 0 = boot. amiga partitions start at seg 1
        left = seg_idx
        right = seg_idx + 1
        if right >= len(self._segments):
            return False
        if left == 0:
            return False  # boot border not resizable
        return True

    def _draw_segment_label(self, painter, rect, seg_w, label, size, sublabel) -> bool:
        """two-line label when it fits, centred name when only that fits; False if neither"""
        sub_text = f"{_format_size(size)}  {sublabel}".rstrip()
        painter.setFont(self._name_font)
        name_w = painter.fontMetrics().horizontalAdvance(label)
        painter.setFont(self._sub_font)
        sub_w = painter.fontMetrics().horizontalAdvance(sub_text)
        painter.setPen(QColor("#FFFFFF"))
        text_rect = rect.adjusted(6, 4, -6, -4)
        if seg_w > max(name_w, sub_w) + 14:
            painter.setFont(self._name_font)
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, label
            )
            painter.setFont(self._sub_font)
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, sub_text
            )
            return True
        if seg_w > name_w + 10:
            painter.setFont(self._name_font)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
            return True
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        total = sum(s[1] for s in self._segments)
        if total <= 0:
            painter.end()
            return
        w = self.width() - 2
        bar_top = self.STRIP_H + 1
        bar_h = self.height() - bar_top - 1
        self._rects = []
        self._borders = []
        self._children_rect = None
        boot_w = self._draw_boot(painter, w, bar_top, bar_h, total)
        if len(self._segments) == 1:
            painter.end()
            return
        container_bytes, badge, band, children = self._draw_rdb_frame(
            painter,
            w,
            bar_top,
            bar_h,
            boot_w,
            total,
        )
        self._draw_rdb_children(painter, children, container_bytes)
        self._rects.append((badge, "RDB header\n~1 MB\nnot to scale"))
        self._rects.append((band, self._capacity_caption(container_bytes)))
        self._draw_handles(painter, children)
        painter.end()

    def _draw_boot(self, painter, width: int, bar_top: int, bar_height: int, total: int) -> int:
        boot_label, boot_size, boot_sub, boot_color, _ = self._segments[0]
        boot_width = width if len(self._segments) == 1 else max(2, int((boot_size / total) * width))
        boot_rect = QRect(1, bar_top, boot_width, bar_height)
        self._rects.append((boot_rect, _tooltip(boot_label, boot_size, boot_sub)))
        painter.fillRect(boot_rect, QBrush(boot_color))
        painter.setPen(QPen(QColor("#222222"), 1))
        painter.drawRect(boot_rect)
        if not self._draw_segment_label(
            painter, boot_rect, boot_width, boot_label, boot_size, boot_sub
        ):
            painter.setFont(self._sub_font)
            painter.setPen(FRAME_COLOR)
            painter.drawText(
                QRect(1, 0, width, self.STRIP_H - 4),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{boot_label} · {boot_sub} · {_format_size(boot_size)}",
            )
            painter.setPen(QPen(FRAME_COLOR, 2))
            tick_x = 1 + boot_width // 2
            painter.drawLine(tick_x, self.STRIP_H - 4, tick_x, bar_top)
        return boot_width

    def _draw_rdb_frame(
        self,
        painter,
        width: int,
        bar_top: int,
        bar_height: int,
        boot_width: int,
        total: int,
    ) -> tuple[int, QRect, QRect, QRect]:
        boot_size = self._segments[0][1]
        container_rect = QRect(1 + boot_width, bar_top, width - boot_width, bar_height)
        container_bytes = total - boot_size
        painter.setPen(QPen(FRAME_COLOR, 2))
        painter.drawRect(container_rect.adjusted(1, 1, -1, -1))
        band = QRect(
            container_rect.left() + 2,
            container_rect.top() + 2,
            container_rect.width() - 4,
            self.BAND_H,
        )
        painter.fillRect(band, QBrush(BAND_BG))
        badge = QRect(band.left() + 6, band.top() + 7, 10, 10)
        painter.fillRect(badge, QBrush(RDB_BADGE))
        painter.setPen(QPen(RDB_BADGE_BORDER, 1))
        painter.drawRect(badge)
        painter.setFont(self._sub_font)
        painter.setPen(QColor("#ECEFF1"))
        caption = painter.fontMetrics().elidedText(
            self._capacity_caption(container_bytes),
            Qt.TextElideMode.ElideRight,
            band.width() - 28,
        )
        painter.drawText(
            band.adjusted(22, 0, -6, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            caption,
        )

        children = QRect(
            container_rect.left() + 2,
            band.bottom() + 1,
            container_rect.width() - 4,
            container_rect.bottom() - band.bottom() - 2,
        )
        self._children_rect = children
        self._bytes_per_pixel = container_bytes / children.width() if children.width() > 0 else 1.0
        return container_bytes, badge, band, children

    def _capacity_caption(self, planned: int) -> str:
        if self._shortfall:
            return (
                f"Amiga partitions · {_format_size(planned)} planned / "
                f"{_format_size(max(0, planned - self._shortfall))} available"
            )
        return f"Amiga partitions · {_format_size(planned)}"

    def _draw_rdb_children(self, painter, children: QRect, container_bytes: int) -> None:
        x = children.left()
        for seg_idx in range(1, len(self._segments)):
            label, size, sublabel, color, selected = self._segments[seg_idx]
            is_free = label == "free" and sublabel == ""
            seg_w = (
                max(2, int((size / container_bytes) * children.width()))
                if container_bytes > 0
                else 2
            )
            if seg_idx == len(self._segments) - 1 or x + seg_w > children.right():
                seg_w = children.right() - x + 1
            rect = QRect(x, children.top(), seg_w, children.height())
            self._rects.append((rect, _tooltip(label, size, sublabel)))

            painter.fillRect(rect, QBrush(color))
            if is_free:
                painter.fillRect(rect, QBrush(FREE_HATCH, Qt.BrushStyle.BDiagPattern))

            if selected:
                painter.setPen(QPen(SELECTED_BORDER, 3))
                painter.drawRect(rect.adjusted(1, 1, -2, -2))

            painter.setPen(QPen(QColor("#222222"), 1))
            painter.drawRect(rect)
            drew = self._draw_segment_label(painter, rect, seg_w, label, size, sublabel)
            if not drew and not is_free and seg_w > 18:
                painter.setFont(self._name_font)
                painter.setPen(QColor("#FFFFFF"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(seg_idx))
            x += seg_w
            if seg_idx < len(self._segments) - 1 and self._resizable_border(seg_idx):
                self._borders.append((x, seg_idx, seg_idx + 1))
        last = self._segments[-1]
        if not (last[0] == "free" and last[2] == ""):
            self._borders.append(
                (children.right() + 1, len(self._segments) - 1, len(self._segments))
            )

    def _draw_handles(self, painter, children: QRect) -> None:
        if not self._interactive:
            return
        arrow = 6
        gap = 3
        mid_y = children.center().y()
        for bx, _, right_seg in self._borders:
            painter.setPen(QPen(QColor(255, 255, 255, 200), 2))
            painter.drawLine(bx, children.top() + 3, bx, children.bottom() - 3)

            painter.setBrush(QBrush(QColor("#FFFFFF")))
            painter.setPen(QPen(QColor("#222222"), 1))
            painter.drawPolygon(
                QPolygon(
                    [
                        QPoint(bx - gap, mid_y),
                        QPoint(bx - gap - arrow, mid_y - arrow),
                        QPoint(bx - gap - arrow, mid_y + arrow),
                    ]
                )
            )
            # the outer-edge handle has no room to its right - left arrow only
            if right_seg < len(self._segments):
                painter.drawPolygon(
                    QPolygon(
                        [
                            QPoint(bx + gap, mid_y),
                            QPoint(bx + gap + arrow, mid_y - arrow),
                            QPoint(bx + gap + arrow, mid_y + arrow),
                        ]
                    )
                )

    def _border_hit(self, pos) -> int:
        """border index near pos, children row only - strip and band never grab"""
        if not self._interactive:
            return -1
        rc = self._children_rect
        if not rc or not (rc.top() <= pos.y() <= rc.bottom()):
            return -1
        for i, (bx, _, _) in enumerate(self._borders):
            if abs(pos.x() - bx) <= self.GRAB_ZONE:
                return i
        return -1

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if self._dragging:
            self._drag_to(pos)
            return
        self._update_hover(event, pos)

    def _drag_to(self, pos) -> None:
        delta_bytes = round_to_cylinder(int((pos.x() - self._drag_start_x) * self._bytes_per_pixel))
        if delta_bytes == 0:
            return
        _, left_seg, right_seg = self._borders[self._drag_border_idx]
        left_amiga = left_seg - 1
        right_amiga = right_seg - 1
        left_is_amiga = 0 <= left_amiga < len(self._amiga_partitions)
        right_is_free = right_seg >= len(self._segments) or (
            right_seg == len(self._segments) - 1 and self._segments[right_seg][0] == "free"
        )
        right_is_amiga = right_seg < len(self._segments) and (
            0 <= right_amiga < len(self._amiga_partitions)
        )
        if not left_is_amiga or not (right_is_amiga or right_is_free):
            return
        left_size = self._amiga_partitions[left_amiga].size
        right_size = (
            self._amiga_partitions[right_amiga].size if right_is_amiga else self._free_space
        )
        new_left = left_size + delta_bytes
        new_right = right_size - delta_bytes
        left_part = self._amiga_partitions[left_amiga]
        if left_part.ags_reservation:
            return
        left_minimum = (
            left_part.ags_reservation.minimum_size
            if left_part.ags_reservation
            else MIN_AMIGA_PARTITION_SIZE
        )
        left_minimum = ((left_minimum + CYLINDER_SIZE - 1) // CYLINDER_SIZE) * CYLINDER_SIZE
        right_minimum = 0
        if right_is_amiga:
            right_part = self._amiga_partitions[right_amiga]
            if right_part.ags_reservation:
                return
            right_minimum = (
                right_part.ags_reservation.minimum_size
                if right_part.ags_reservation
                else MIN_AMIGA_PARTITION_SIZE
            )
            right_minimum = ((right_minimum + CYLINDER_SIZE - 1) // CYLINDER_SIZE) * CYLINDER_SIZE
        if new_left < left_minimum or new_right < right_minimum:
            return
        if left_part.filesystem == Filesystem.PFS3 and new_left > PFS3_MAX_PARTITION_SIZE:
            return
        if (
            right_is_amiga
            and right_part.filesystem == Filesystem.PFS3
            and new_right > PFS3_MAX_PARTITION_SIZE
        ):
            return
        self._drag_start_x = pos.x()
        if self._on_resize_callback:
            self._on_resize_callback(
                left_amiga,
                new_left,
                right_amiga if right_is_amiga else -1,
                new_right,
            )

    def _update_hover(self, event, pos) -> None:
        bi = self._border_hit(pos)
        if bi >= 0:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            QToolTip.hideText()
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            for rect, tip in self._rects:
                if rect.contains(pos):
                    gpos = (
                        event.globalPosition().toPoint()
                        if hasattr(event, "globalPosition")
                        else self.mapToGlobal(pos)
                    )
                    QToolTip.showText(gpos, tip, self, rect)
                    return
            QToolTip.hideText()

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        bi = self._border_hit(pos)
        if bi >= 0:
            self._dragging = True
            self._drag_border_idx = bi
            self._drag_start_x = pos.x()
            return
        for seg_idx, (rect, _) in enumerate(self._rects):
            if rect.contains(pos):
                amiga_idx = seg_idx - 1
                if 0 <= amiga_idx < len(self._amiga_partitions):
                    self.partition_clicked.emit(amiga_idx)
                return

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._drag_border_idx = -1
