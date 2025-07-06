from PyQt6.QtWidgets import (QMainWindow, QTabWidget, QWidget, QVBoxLayout,
                             QMenuBar, QToolBar, QLabel, QStatusBar, QProgressBar,
                             QTableView, QTextEdit, QSizePolicy, QHeaderView) # Added QTableView, QTextEdit, QSizePolicy, QHeaderView
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtCore import Qt, pyqtSlot

from .main_window_vm import MainWindowVM
from .models import DuplicatesTableModel # Import the table model
import logging
logger = logging.getLogger(__name__) # Make sure logger is defined


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.vm = MainWindowVM(self) # Pass self as parent for QObject hierarchy

        self.setWindowTitle("Video Duplicate Finder (Python)")
        self.setGeometry(100, 100, 1020, 950) # x, y, width, height (similar to C# XAML)

        self._create_actions()
        self._create_menu_bar()
        # self._create_tool_bars() # Toolbar can be added later if needed

        self.tab_widget = QTabWidget()
        self._create_scanner_tab()
        self._create_settings_tab()
        self._create_log_tab()

        self.setCentralWidget(self.tab_widget)

        self._create_status_bar()
        self._connect_vm_signals()

        logger.info("MainWindow initialized and VM signals connected.")


    def _create_actions(self):
        self.quit_action = QAction("&Quit", self)
        self.quit_action.triggered.connect(self.close) # QMainWindow.close()

        self.start_full_scan_action = QAction("Start &Full Scan", self)
        self.start_full_scan_action.triggered.connect(self.vm.start_full_scan)

        self.start_compare_only_action = QAction("Start &Compare Only (Rescan)", self)
        self.start_compare_only_action.triggered.connect(self.vm.start_compare_only)
        # self.start_compare_only_action.setEnabled(False) # VM will control this

        self.pause_scan_action = QAction("&Pause Scan", self)
        self.pause_scan_action.triggered.connect(self.vm.pause_scan)
        self.pause_scan_action.setEnabled(False) # Initially disabled

        self.resume_scan_action = QAction("&Resume Scan", self)
        self.resume_scan_action.triggered.connect(self.vm.resume_scan)
        self.resume_scan_action.setEnabled(False) # Initially disabled

        self.stop_scan_action = QAction("&Stop Scan", self)
        self.stop_scan_action.triggered.connect(self.vm.stop_scan)
        self.stop_scan_action.setEnabled(False) # Initially disabled

        # TODO: Other actions for Database, Selection, Delete, Settings etc.

    def _create_menu_bar(self):
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        # TODO: Add actions like Export/Import DB, Export/Import Results, Settings link?
        file_menu.addAction(self.quit_action)

        scan_menu = menu_bar.addMenu("&Scan")
        scan_menu.addAction(self.start_full_scan_action)
        scan_menu.addAction(self.start_compare_only_action)
        scan_menu.addSeparator()
        scan_menu.addAction(self.pause_scan_action)
        scan_menu.addAction(self.resume_scan_action)
        scan_menu.addAction(self.stop_scan_action)

        selection_menu = menu_bar.addMenu("&Selection")
        self.clear_selection_action = QAction("&Clear Selection", self)
        self.clear_selection_action.triggered.connect(self.vm.clear_all_selection)
        selection_menu.addAction(self.clear_selection_action)
        # TODO: Add other selection actions

        # TODO: Add other menus (Database, Tools, Help)


    def _create_tool_bars(self):
        # Example Toolbar (can be populated from actions)
        # main_toolbar = QToolBar("Main Toolbar")
        # self.addToolBar(Qt.ToolBarArea.TopToolBarArea, main_toolbar)
        # main_toolbar.addAction(self.start_scan_action) # If defined
        pass

    def _create_scanner_tab(self):
        scanner_tab = QWidget()
        layout = QVBoxLayout() # Main layout for scanner tab

        # Placeholder: Menu/Toolbar area within the tab (if not using main window's)
        # For now, main window menus will be used.

        # Placeholder: Filter/Sort area
        filter_sort_label = QLabel("Filter/Sort Area (Placeholder)")
        layout.addWidget(filter_sort_label)

        # Placeholder: Duplicates Display (QTableView or QTreeView)
        # duplicates_view_label = QLabel("Duplicates View (QTableView/QTreeView Placeholder)")
        # layout.addWidget(duplicates_view_label)
        self.duplicates_table_view = QTableView()
        # Pass self.vm.core_settings to the model's constructor
        self.duplicates_model = DuplicatesTableModel(self.vm.core_settings, self)
        self.duplicates_table_view.setModel(self.duplicates_model)

        # Configure table view appearance and behavior
        self.duplicates_table_view.setAlternatingRowColors(True)
        self.duplicates_table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.duplicates_table_view.setSelectionMode(QTableView.SelectionMode.ExtendedSelection) # Allow multi-select
        self.duplicates_table_view.setSortingEnabled(True) # Model needs to support sorting or use QSortFilterProxyModel

        # Adjust column widths (example, can be more dynamic)
        header = self.duplicates_table_view.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents) # Checkbox
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents) # Thumbnail
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch) # Path
        for i in range(3, self.duplicates_model.columnCount()): # Other info columns
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)
            self.duplicates_table_view.setColumnWidth(i, 150) # Default width for info columns

        # TODO: Set custom delegates for thumbnail column if needed

        layout.addWidget(self.duplicates_table_view)

        scanner_tab.setLayout(layout)
        self.tab_widget.addTab(scanner_tab, "Scanner")

    def _create_settings_tab(self):
        settings_tab = QWidget()
        layout = QVBoxLayout()

        # Placeholder: Sub-tabs for Misc, Files, Appearance
        settings_sub_tabs = QTabWidget()
        misc_tab = QWidget()
        misc_layout = QVBoxLayout()
        misc_layout.addWidget(QLabel("Misc Settings (Checkboxes, NumericUpDowns Placeholder)"))
        misc_tab.setLayout(misc_layout)
        settings_sub_tabs.addTab(misc_tab, "Misc")

        files_tab = QWidget()
        files_layout = QVBoxLayout()
        files_layout.addWidget(QLabel("Files Settings (Include/Exclude Lists, Path Filters Placeholder)"))
        files_tab.setLayout(files_layout)
        settings_sub_tabs.addTab(files_tab, "Files")

        appearance_tab = QWidget()
        appearance_layout = QVBoxLayout()
        appearance_layout.addWidget(QLabel("Appearance Settings (Theme, Column Visibility Placeholder)"))
        appearance_tab.setLayout(appearance_layout)
        settings_sub_tabs.addTab(appearance_tab, "Appearance")

        layout.addWidget(settings_sub_tabs)
        settings_tab.setLayout(layout)
        self.tab_widget.addTab(settings_tab, "Settings")

    def _create_log_tab(self):
        log_tab = QWidget()
        layout = QVBoxLayout()

        # Add Menu for Log Tab (Clear, Save)
        log_menu_bar = QMenuBar()
        log_file_menu = log_menu_bar.addMenu("&Log Actions")

        self.clear_log_action = QAction("&Clear Log", self)
        self.clear_log_action.triggered.connect(self.vm.clear_log)
        log_file_menu.addAction(self.clear_log_action)

        self.save_log_action = QAction("&Save Log to File...", self)
        self.save_log_action.triggered.connect(self.vm.save_log_to_file)
        log_file_menu.addAction(self.save_log_action)

        layout.setMenuBar(log_menu_bar) # Add menu to the tab's layout

        self.log_text_edit = QTextEdit()
        self.log_text_edit.setReadOnly(True)
        layout.addWidget(self.log_text_edit)

        log_tab.setLayout(layout)
        self.tab_widget.addTab(log_tab, "Log")

    def _create_status_bar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.scan_progress_bar = QProgressBar()
        self.scan_progress_bar.setRange(0,100)
        self.scan_progress_bar.setValue(0)
        self.status_bar.addPermanentWidget(self.scan_progress_bar, 1)

        self.scan_status_label = QLabel("Ready") # For current file/phase text
        self.status_bar.addWidget(self.scan_status_label, 2) # Stretch factor for more space

        self.elapsed_time_label = QLabel("Elapsed: 00:00:00")
        self.status_bar.addWidget(self.elapsed_time_label)

        self.remaining_time_label = QLabel("Remaining: ~00:00:00")
        self.status_bar.addWidget(self.remaining_time_label)


    def _connect_vm_signals(self):
        self.vm.is_scanning_changed.connect(self._on_is_scanning_changed)
        self.vm.is_paused_changed.connect(self._on_is_paused_changed)
        # self.vm.is_busy_changed.connect(self._on_is_busy_changed) # For general busy overlay
        # self.vm.busy_text_changed.connect(self._on_busy_text_changed)

        self.vm.scan_progress_text_changed.connect(self.scan_status_label.setText)
        self.vm.scan_progress_value_changed.connect(self.scan_progress_bar.setValue)
        self.vm.scan_progress_max_value_changed.connect(self.scan_progress_bar.setMaximum)
        self.vm.elapsed_time_changed.connect(self.elapsed_time_label.setText)
        self.vm.remaining_time_changed.connect(self.remaining_time_label.setText)

        self.vm.duplicates_updated.connect(self._on_duplicates_updated)
        self.vm.log_message_added.connect(self._on_log_message_added)
        self.vm.request_clear_selection_signal.connect(self._on_request_clear_selection)

    @pyqtSlot()
    def _on_request_clear_selection(self):
        if hasattr(self, 'duplicates_model') and self.duplicates_model is not None:
            self.duplicates_model.clear_all_checked_states()
            logger.debug("MainWindow: Cleared all item check states in model.")


    @pyqtSlot(dict)
    def _on_duplicates_updated(self, duplicates_data: dict):
        logger.debug(f"MainWindow received duplicates_updated signal with {len(duplicates_data)} groups.")
        if hasattr(self, 'duplicates_model') and self.duplicates_model is not None:
            self.duplicates_model.load_data(duplicates_data)
            # Adjust column widths after data is loaded if needed, e.g. ResizeToContents for some
            # self.duplicates_table_view.resizeColumnsToContents() # Can be slow
            # Or specific columns:
            # self.duplicates_table_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            # self.duplicates_table_view.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)


    @pyqtSlot(bool)
    def _on_is_scanning_changed(self, is_scanning):
        self.start_full_scan_action.setEnabled(not is_scanning)
        self.start_compare_only_action.setEnabled(not is_scanning) # and self.vm.is_ready_to_compare

        self.stop_scan_action.setEnabled(is_scanning)
        # Pause/Resume enabled state depends on both is_scanning and is_paused
        self._update_pause_resume_actions_state(is_scanning, self.vm.is_paused)

    @pyqtSlot(bool)
    def _on_is_paused_changed(self, is_paused):
        self._update_pause_resume_actions_state(self.vm.is_scanning, is_paused)

    def _update_pause_resume_actions_state(self, is_scanning, is_paused):
        self.pause_scan_action.setEnabled(is_scanning and not is_paused)
        self.resume_scan_action.setEnabled(is_scanning and is_paused)

    @pyqtSlot(str)
    def _on_log_message_added(self, message):
        # Assuming self.log_text_edit exists and is a QTextEdit
        if hasattr(self, 'log_text_edit') and self.log_text_edit is not None:
            if message.strip() == "---- Log Cleared ----":
                self.log_text_edit.clear()
            self.log_text_edit.append(message) # Append the clear message itself too, or just the newline
        else: # Fallback if log tab not fully init yet or error
            print(f"LOG: {message}")

    # Actual save log functionality will require QFileDialog and file I/O
    @pyqtSlot() # Assuming vm.save_log_to_file will eventually trigger this via a signal if View interaction is needed
    def _trigger_save_log_dialog(self):
        if hasattr(self, 'log_text_edit') and self.log_text_edit is not None:
            content = self.log_text_edit.toPlainText()
            # TODO: Use QFileDialog to get save path from user
            # For now, just log that it would save.
            logger.info(f"Save Log to File dialog requested. Content length: {len(content)}")
            # Example:
            # from PyQt6.QtWidgets import QFileDialog
            # filePath, _ = QFileDialog.getSaveFileName(self, "Save Log", "", "Text Files (*.txt);;All Files (*)")
            # if filePath:
            #     try:
            #         with open(filePath, 'w', encoding='utf-8') as f:
            #             f.write(content)
            #         logger.info(f"Log saved to {filePath}")
            #     except Exception as e:
            #         logger.error(f"Failed to save log to {filePath}: {e}")
            #         # Show error message to user via QMessageBox
            pass # Placeholder for QFileDialog logic


    # def closeEvent(self, event):
    #     # Handle unsaved changes or confirm close, from VM
    #     # if not self.vm.can_close():
    #     #     event.ignore()
    #     # else:
    #     #     event.accept()
    #     pass


if __name__ == '__main__':
    # This is for testing the MainWindow directly if needed
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    main_win = MainWindow()
    main_win.show()
    sys.exit(app.exec())
