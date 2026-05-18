"""Preset Commands Panel - Simple and robust implementation.

Design: Neo-Terminal style with solid colors.
Loads preset commands from data/intent_mappings.json.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple


# Path to intent mappings config (project root / data / intent_mappings.json)
_current_file = os.path.abspath(__file__)
_popup_dir = os.path.dirname(_current_file)      # app/popup
_app_dir = os.path.dirname(_popup_dir)           # app
_project_root = os.path.dirname(_app_dir)        # project root
INTENT_MAPPINGS_PATH = os.path.join(_project_root, "data", "intent_mappings.json")


@dataclass
class PresetCommand:
    """Preset command data structure."""
    id: str
    name: str
    description: str
    instruction: str
    icon: str = ""


def load_presets_from_intent_mappings() -> List[PresetCommand]:
    """Load preset commands from intent_mappings.json."""
    if not os.path.exists(INTENT_MAPPINGS_PATH):
        print(f"[PresetCommands] Config file not found: {INTENT_MAPPINGS_PATH}")
        return []

    try:
        with open(INTENT_MAPPINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        presets = []
        for mapping in data.get("mappings", []):
            if not mapping.get("enabled", True):
                continue  # Skip disabled mappings

            # Use first alias as instruction (most natural voice command)
            aliases = mapping.get("aliases", [])
            instruction = aliases[0] if aliases else mapping.get("description", "")

            presets.append(PresetCommand(
                id=mapping.get("id", ""),
                name=mapping.get("description", ""),
                description=aliases[0] if aliases else mapping.get("description", ""),
                instruction=instruction,
                icon="command",
            ))

        return presets
    except Exception as e:
        print(f"[PresetCommands] Failed to load intent mappings: {e}")
        return []


# Default presets (fallback if config not found)
DEFAULT_PRESETS: List[PresetCommand] = [
    PresetCommand("open_edge", "Open Edge Browser", "Launch Edge browser", "Open Edge browser", "web"),
    PresetCommand("open_chrome", "Open Chrome Browser", "Launch Chrome browser", "Open Chrome browser", "web"),
]


# Load actual presets from config (used by default)
PRESETS = load_presets_from_intent_mappings() or DEFAULT_PRESETS


def build_preset_panel():
    """Build the preset command panel widget."""
    from PySide6 import QtCore, QtGui, QtWidgets

    # Stylesheet - Neo-Terminal style with solid colors
    STYLESHEET = """
        #PanelShell {
            background: #0A141E;
            border: 2px solid #00C896;
            border-radius: 12px;
        }
        #PanelTitle {
            color: #00FFC8;
            font-size: 12px;
            font-weight: bold;
        }
        #SearchInput {
            background: #001828;
            border: 1px solid #00C896;
            border-radius: 8px;
            color: #00FFC8;
            padding: 8px;
            font-size: 11px;
        }
        #CommandCard {
            background: #0A1A28;
            border: 1px solid #1A3A4A;
            border-radius: 8px;
        }
        #CommandCard:hover {
            background: #0A2838;
            border: 1px solid #00C896;
        }
        #CardIcon {
            color: #00C896;
            font-size: 16px;
        }
        #CardName {
            color: #E0F8F8;
            font-size: 12px;
            font-weight: bold;
        }
        #CardDesc {
            color: #6A8A9A;
            font-size: 10px;
        }
        #EmptyHint {
            color: #4A6A7A;
            font-size: 11px;
        }
    """

    class CommandCard(QtWidgets.QFrame):
        """Single command card widget."""

        clicked = QtCore.Signal(str)

        def __init__(self, preset: PresetCommand, parent=None):
            super().__init__(parent)
            self.preset = preset
            self.setObjectName("CommandCard")
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setFixedHeight(64)

            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(10)

            # Icon
            icon_map = {
                "chat": "💬", "camera": "📷", "send": "📤",
                "web": "🌐", "folder": "📁", "email": "📧",
                "music": "🎵", "calendar": "📅", "command": "⚡",
            }
            icon_label = QtWidgets.QLabel(icon_map.get(preset.icon, "⚡"))
            icon_label.setObjectName("CardIcon")
            icon_label.setFixedWidth(24)
            layout.addWidget(icon_label)

            # Name and description
            info_layout = QtWidgets.QVBoxLayout()
            info_layout.setSpacing(2)
            layout.addLayout(info_layout, 1)

            name_label = QtWidgets.QLabel(preset.name)
            name_label.setObjectName("CardName")
            info_layout.addWidget(name_label)

            desc_label = QtWidgets.QLabel(preset.description)
            desc_label.setObjectName("CardDesc")
            info_layout.addWidget(desc_label)

        def mousePressEvent(self, event):
            if event.button() == QtCore.Qt.LeftButton:
                self.clicked.emit(self.preset.instruction)
            super().mousePressEvent(event)

    class PresetPanel(QtWidgets.QFrame):
        """Preset command popup panel."""

        command_selected = QtCore.Signal(str)

        def __init__(self, presets: List[PresetCommand], parent=None):
            super().__init__(parent)
            self.presets = presets
            self._filtered = presets
            self._cards: List[CommandCard] = []

            self.setObjectName("PanelShell")
            self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
            self.setFixedSize(360, 380)
            self.setStyleSheet(STYLESHEET)

            self._build_ui()

        def _build_ui(self):
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(14, 12, 14, 12)
            layout.setSpacing(10)

            # Title row
            header = QtWidgets.QHBoxLayout()
            layout.addLayout(header)

            title = QtWidgets.QLabel("预存指令")
            title.setObjectName("PanelTitle")
            header.addWidget(title)

            close_btn = QtWidgets.QPushButton("X")
            close_btn.setStyleSheet("""
                QPushButton { background: transparent; color: #6A8A9A; border: none; font-weight: bold; }
                QPushButton:hover { color: #00FFC8; }
            """)
            close_btn.setFixedSize(20, 20)
            close_btn.clicked.connect(self.hide)
            header.addWidget(close_btn, 0, QtCore.Qt.AlignRight)

            # Search input
            self.search_input = QtWidgets.QLineEdit()
            self.search_input.setObjectName("SearchInput")
            self.search_input.setPlaceholderText("搜索指令...")
            self.search_input.textChanged.connect(self._filter)
            layout.addWidget(self.search_input)

            # Scroll area for cards
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("""
                QScrollArea { background: #0A141E; border: none; }
                QScrollBar:vertical { background: #001828; width: 6px; border-radius: 3px; }
                QScrollBar::handle:vertical { background: #00C896; border-radius: 3px; }
            """)
            layout.addWidget(scroll, 1)

            self.card_container = QtWidgets.QWidget()
            self.card_container.setStyleSheet("background: #0A141E;")
            self.card_layout = QtWidgets.QVBoxLayout(self.card_container)
            self.card_layout.setSpacing(6)
            self.card_layout.setContentsMargins(0, 0, 0, 0)
            scroll.setWidget(self.card_container)

            self._render_cards()

        def _render_cards(self):
            # Clear existing cards
            while self.card_layout.count():
                item = self.card_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            self._cards.clear()

            if not self._filtered:
                empty = QtWidgets.QLabel("未找到匹配指令")
                empty.setObjectName("EmptyHint")
                empty.setAlignment(QtCore.Qt.AlignCenter)
                self.card_layout.addWidget(empty)
                return

            for preset in self._filtered:
                card = CommandCard(preset, self.card_container)
                card.clicked.connect(self._on_click)
                self.card_layout.addWidget(card)
                self._cards.append(card)

        def _filter(self, text: str):
            text = text.lower().strip()
            if text:
                self._filtered = [p for p in self.presets
                    if text in p.name.lower() or text in p.description.lower()]
            else:
                self._filtered = self.presets
            self._render_cards()

        def _on_click(self, instruction: str):
            self.command_selected.emit(instruction)
            self.hide()

        def show_at(self, anchor: QtWidgets.QWidget):
            """Show panel below the anchor widget."""
            pos = anchor.mapToGlobal(QtCore.QPoint(0, anchor.height() + 5))

            # Adjust position to stay on screen
            screen = QtGui.QGuiApplication.primaryScreen()
            if screen:
                geo = screen.availableGeometry()
                if pos.x() + self.width() > geo.right():
                    pos.setX(geo.right() - self.width() - 10)
                if pos.y() + self.height() > geo.bottom():
                    pos.setY(anchor.mapToGlobal(QtCore.QPoint(0, 0)).y() - self.height() - 5)

            self.move(pos)
            self.show()

    return PresetPanel


def build_preset_button():
    """Build the preset button widget."""
    from PySide6 import QtCore, QtWidgets

    class PresetButton(QtWidgets.QPushButton):
        """Preset command trigger button."""

        def __init__(self, parent=None):
            super().__init__("预设指令", parent)
            self.setObjectName("PresetButton")
            self.setCursor(QtCore.Qt.PointingHandCursor)
            self.setFixedHeight(32)

            self.setStyleSheet("""
                #PresetButton {
                    background: #001828;
                    border: 1px solid #00C896;
                    border-radius: 10px;
                    color: #00FFC8;
                    font-size: 11px;
                    font-weight: bold;
                    padding: 0 14px;
                }
                #PresetButton:hover {
                    background: #0A2838;
                    border: 1px solid #00FFC8;
                }
            """)

    return PresetButton


def create_preset_system(
    parent: QtWidgets.QWidget,
    on_select: Callable[[str], None],
    presets: Optional[List[PresetCommand]] = None,
) -> Tuple[QtWidgets.QPushButton, QtWidgets.QFrame]:
    """
    Create preset button and panel.

    Returns: (button, panel)
    """
    PresetPanel = build_preset_panel()
    PresetButton = build_preset_button()

    presets = presets or PRESETS

    button = PresetButton(parent)
    panel = PresetPanel(presets, parent)
    panel.command_selected.connect(on_select)

    button.clicked.connect(lambda: panel.show_at(button))

    return button, panel