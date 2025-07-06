from PyQt6.QtCore import QObject, pyqtSignal, QThread, pyqtSlot
import logging

# Assuming vdf_core is in PYTHONPATH or path adjusted
from vdf_core.settings import CoreSettings
from vdf_core.database import DatabaseManager
from vdf_core.scan_engine import ScanEngine, ScanProgressEventArgs, DuplicateItemGroup, OperationCanceledError
from vdf_core.file_entry import FileEntry # For type hinting if needed

logger = logging.getLogger(__name__)

# Worker QThread for ScanEngine
class ScanEngineWorker(QThread):
    # Signals to communicate with the main thread (VM)
    scan_phase_changed = pyqtSignal(str)
    progress_updated = pyqtSignal(ScanProgressEventArgs)
    scan_completed = pyqtSignal(dict) # dict of DuplicateItemGroup
    scan_aborted = pyqtSignal(str) # Reason for abort (cancelled, error)
    files_enumerated = pyqtSignal(int)
    info_gathering_complete = pyqtSignal(int)

    def __init__(self, scan_engine: ScanEngine, parent=None):
        super().__init__(parent)
        self.scan_engine = scan_engine
        self.action = None # e.g., "full_scan", "compare_only"

    def run(self):
        try:
            # Connect ScanEngine's callbacks to emit signals
            self.scan_engine.on_scan_phase_changed = lambda phase: self.scan_phase_changed.emit(phase)
            self.scan_engine.on_progress = lambda args: self.progress_updated.emit(args)
            self.scan_engine.on_scan_complete = lambda duplicates: self.scan_completed.emit(duplicates)
            self.scan_engine.on_scan_aborted = lambda: self.scan_aborted.emit("aborted") # Generic abort
            self.scan_engine.on_files_enumerated = lambda count: self.files_enumerated.emit(count)
            self.scan_engine.on_info_gathering_complete = lambda count: self.info_gathering_complete.emit(count)

            if self.action == "full_scan":
                self.scan_engine.start_scan()
            elif self.action == "compare_only":
                # self.scan_engine.start_compare_only() # Assuming this method exists or adapt start_scan
                logger.warning("Compare only not fully implemented in ScanEngine yet, running full scan logic.")
                self.scan_engine.start_scan(quick_scan_if_possible=True) # Adapt based on ScanEngine
            elif self.action == "cleanup_db":
                self.scan_engine.cleanup_database_interactive()
                self.scan_completed.emit({}) # Emit empty results to signify completion
            else:
                logger.warning(f"Unknown action for ScanEngineWorker: {self.action}")
                self.scan_aborted.emit(f"Unknown action: {self.action}")

        except OperationCanceledError:
            logger.info("ScanEngineWorker: Scan cancelled.")
            self.scan_aborted.emit("cancelled")
        except Exception as e:
            logger.error(f"ScanEngineWorker: Error during scan: {e}", exc_info=True)
            self.scan_aborted.emit(f"error: {str(e)}")

    def start_full_scan(self):
        self.action = "full_scan"
        self.start()

    def start_compare_only(self):
        self.action = "compare_only"
        self.start()

    def start_cleanup_db(self):
        self.action = "cleanup_db"
        self.start()

    def stop_scan(self):
        if self.scan_engine:
            self.scan_engine.stop_scan()
        # QThread.quit() or terminate() might be needed if the run loop doesn't exit quickly after stop_scan()


class MainWindowVM(QObject):
    # Signals for UI updates
    is_scanning_changed = pyqtSignal(bool)
    is_paused_changed = pyqtSignal(bool)
    is_busy_changed = pyqtSignal(bool) # For general busy states, not just scanning
    busy_text_changed = pyqtSignal(str)

    scan_progress_text_changed = pyqtSignal(str)
    scan_progress_value_changed = pyqtSignal(int)
    scan_progress_max_value_changed = pyqtSignal(int)
    elapsed_time_changed = pyqtSignal(str) # Formatted string
    remaining_time_changed = pyqtSignal(str) # Formatted string

    # Signal to update duplicates display (e.g., with List[DuplicateItemGroup] or a model)
    duplicates_updated = pyqtSignal(dict) # dict of DuplicateItemGroup
    log_message_added = pyqtSignal(str) # For individual log messages

    # Summary stats
    total_duplicates_changed = pyqtSignal(int)
    total_groups_changed = pyqtSignal(int)
    total_size_duplicates_changed = pyqtSignal(str) # Formatted string

    # Signal to be connected by MainWindow to its model's clear method or a view method
    request_clear_selection_signal = pyqtSignal()


    def __init__(self, parent=None):
        super().__init__(parent)

        self._is_scanning = False
        self._is_paused = False
        self._is_busy = False
        self._busy_text = ""

        self._scan_progress_text = ""
        self._scan_progress_value = 0
        self._scan_progress_max_value = 100
        self._elapsed_time_str = "00:00:00"
        self._remaining_time_str = "~00:00:00"

        self._duplicates: Dict[str, DuplicateItemGroup] = {}

        # Initialize core components
        # TODO: Load/Save CoreSettings from a file (e.g., config.json)
        self.core_settings = CoreSettings()
        self.db_manager = DatabaseManager(self.core_settings)
        self.scan_engine = ScanEngine(self.core_settings, self.db_manager)

        self.scan_worker_thread: Optional[ScanEngineWorker] = None

        self._setup_logging_handler()
        logger.info("MainWindowVM initialized.")

    def _setup_logging_handler(self):
        # Add a handler to the root logger to capture logs for the UI
        # More specific logger can be targeted if preferred.
        ui_log_handler = UILogHandler(self)
        # Let's attach to the VDF root logger, assuming one is set up in main app or CLI.
        # For now, let's use the root logger for simplicity of capturing all.
        # logging.getLogger("vdf_core").addHandler(ui_log_handler) # if vdf_core has its own named logger
        logging.getLogger().addHandler(ui_log_handler) # Add to root logger
        # Ensure level is appropriate if root logger's level is too high
        if logging.getLogger().level > logging.INFO:
             logging.getLogger().setLevel(logging.INFO)


    # --- Properties with Qt Signals ---
    @property
    def is_scanning(self): return self._is_scanning
    @is_scanning.setter
    def is_scanning(self, value):
        self._is_scanning = value
        self.is_scanning_changed.emit(value)
        if not value: # If scanning stopped, also not paused
            self.is_paused = False

    @property
    def is_paused(self): return self._is_paused
    @is_paused.setter
    def is_paused(self, value):
        self._is_paused = value
        self.is_paused_changed.emit(value)

    @property
    def is_busy(self): return self._is_busy
    @is_busy.setter
    def is_busy(self, value):
        self._is_busy = value
        self.is_busy_changed.emit(value)

    @property
    def busy_text(self): return self._busy_text
    @busy_text.setter
    def busy_text(self, value):
        self._busy_text = value
        self.busy_text_changed.emit(value)

    # ... (similar properties for scan_progress_text, value, max_value, time strings) ...
    # For brevity, direct emission will be used from slots for now for these simple ones.

    # --- Slots for ScanEngineWorker Signals ---
    @pyqtSlot(str)
    def _on_scan_phase_changed(self, phase_name: str):
        logger.info(f"Scan phase changed: {phase_name}")
        self.scan_progress_text_changed.emit(f"Phase: {phase_name}")
        # self.busy_text = f"Phase: {phase_name}" # Can also update busy text

    @pyqtSlot(ScanProgressEventArgs)
    def _on_progress_updated(self, args: ScanProgressEventArgs):
        self.scan_progress_value_changed.emit(args.current_value)
        self.scan_progress_max_value_changed.emit(args.max_value)

        current_file_display = os.path.basename(args.current_file) if args.current_file else "..."
        self.scan_progress_text_changed.emit(f"{args.phase}: {current_file_display}")

        if args.elapsed_time is not None:
            self.elapsed_time_changed.emit(self._format_time(args.elapsed_time))
        if args.estimated_remaining is not None:
            self.remaining_time_changed.emit(f"~{self._format_time(args.estimated_remaining)}")
        else:
            self.remaining_time_changed.emit("~Calculating...")

    @pyqtSlot(dict)
    def _on_scan_completed(self, duplicates_result: Dict[str, DuplicateItemGroup]):
        logger.info("Scan completed in VM.")
        self.is_scanning = False
        self.is_busy = False # Scan specific busy state
        self._duplicates = duplicates_result
        self.duplicates_updated.emit(self._duplicates) # Send to UI for display
        self._update_summary_stats()
        # TODO: Trigger thumbnail retrieval if setting is enabled (another worker?)
        # self.scan_engine.Scanner.RetrieveThumbnails(); from C#
        # This might need its own worker or be part of ScanEngine flow.

    @pyqtSlot(str)
    def _on_scan_aborted(self, reason: str):
        logger.warning(f"Scan aborted in VM. Reason: {reason}")
        self.is_scanning = False
        self.is_busy = False
        # Reset progress
        self.scan_progress_value_changed.emit(0)
        self.scan_progress_text_changed.emit(f"Scan Aborted: {reason}")

    @pyqtSlot(int)
    def _on_files_enumerated(self, count: int):
        logger.info(f"{count} files enumerated.")
        self.is_busy = False # Assuming enumeration was a busy phase

    @pyqtSlot(int)
    def _on_info_gathering_complete(self, count: int):
        logger.info(f"{count} files had information gathered/updated.")
        # is_busy state for this phase handled by main scan completion/abort

    def _format_time(self, seconds: float) -> str:
        secs = int(seconds)
        hrs = secs // 3600
        mins = (secs % 3600) // 60
        secs = secs % 60
        return f"{hrs:02}:{mins:02}:{secs:02}"

    def _update_summary_stats(self):
        num_groups = len(self._duplicates)
        num_duplicates = sum(len(group.items) for group in self._duplicates.values())
        # TODO: Calculate total size
        self.total_groups_changed.emit(num_groups)
        self.total_duplicates_changed.emit(num_duplicates)
        # self.total_size_duplicates_changed.emit(formatted_size_str)


    # --- Public Command-like Methods (called by View) ---
    def start_full_scan(self):
        if self.is_scanning:
            logger.warning("Scan already in progress.")
            return

        logger.info("Starting Full Scan...")
        self.is_scanning = True
        self.is_busy = True
        self.busy_text = "Preparing scan..."

        # TODO: Perform pre-scan checks (FFmpeg exists, settings valid) like in C#
        # TODO: Copy settings from self.core_settings (GUI version) to self.scan_engine.settings
        # For now, assume self.scan_engine.settings is kept up-to-date by settings UI.
        # self.scan_engine.settings = self.core_settings # Or selective copy

        self.scan_worker_thread = ScanEngineWorker(self.scan_engine)
        self._connect_worker_signals(self.scan_worker_thread)
        self.scan_worker_thread.start_full_scan()

    def start_compare_only(self):
        if self.is_scanning: return
        logger.info("Starting Compare Only Scan...")
        self.is_scanning = True
        self.is_busy = True
        self.busy_text = "Preparing comparison..."
        # TODO: Copy settings
        self.scan_worker_thread = ScanEngineWorker(self.scan_engine)
        self._connect_worker_signals(self.scan_worker_thread)
        self.scan_worker_thread.start_compare_only()


    def pause_scan(self):
        if self.is_scanning and not self.is_paused:
            logger.info("Pausing scan...")
            self.scan_engine.pause_scan()
            self.is_paused = True # This will emit signal

    def resume_scan(self):
        if self.is_scanning and self.is_paused:
            logger.info("Resuming scan...")
            self.scan_engine.resume_scan()
            self.is_paused = False # This will emit signal

    def stop_scan(self):
        if self.is_scanning:
            logger.info("Stopping scan...")
            # self.is_busy = True # Optional: set busy while stopping
            # self.busy_text = "Stopping scan threads..."
            if self.scan_worker_thread and self.scan_worker_thread.isRunning():
                 self.scan_engine.stop_scan() # Signal ScanEngine first
                 # Worker thread should see cancellation and emit scan_aborted("cancelled")
            else: # If thread somehow finished or not started but is_scanning was true
                 self.is_scanning = False
                 self.is_busy = False


    def cleanup_database(self):
        if self.is_scanning:
            logger.warning("Cannot cleanup database while scan is in progress.")
            return
        logger.info("Starting database cleanup...")
        self.is_busy = True
        self.busy_text = "Cleaning up database..."
        self.scan_worker_thread = ScanEngineWorker(self.scan_engine)
        self._connect_worker_signals(self.scan_worker_thread) # For completion/error
        self.scan_worker_thread.start_cleanup_db()
        # Note: _on_scan_completed will set is_busy=False if cleanup is successful.
        # _on_scan_aborted will also handle it if cleanup fails.


    def _connect_worker_signals(self, worker: ScanEngineWorker):
        worker.scan_phase_changed.connect(self._on_scan_phase_changed)
        worker.progress_updated.connect(self._on_progress_updated)
        worker.scan_completed.connect(self._on_scan_completed)
        worker.scan_aborted.connect(self._on_scan_aborted)
        worker.files_enumerated.connect(self._on_files_enumerated)
        worker.info_gathering_complete.connect(self._on_info_gathering_complete)
        worker.finished.connect(self._on_worker_finished) # Clean up thread object

    @pyqtSlot()
    def _on_worker_finished(self):
        logger.debug("ScanEngineWorker thread finished.")
        if self.scan_worker_thread:
            self.scan_worker_thread.deleteLater() # Schedule for deletion
            self.scan_worker_thread = None
        # Check if scan was aborted by error and UI wasn't reset
        if self.is_scanning and not self.scan_engine._is_scanning: # If engine stopped but UI flag is still on
            logger.warning("Worker finished but UI still thought it was scanning. Resetting UI state.")
            self._on_scan_aborted("worker_finished_unexpectedly")


    # Placeholder for other VM methods (settings, selection, deletion etc.)
    def clear_log(self):
        logger.info("Log clear requested by user.")
        # This VM doesn't hold the log items directly if UILogHandler appends to view.
        # The view itself (MainWindow.log_text_edit) would need to be cleared.
        # Or, UILogHandler could also store them and VM could clear its store,
        # then signal view to refresh from empty store.
        # For now, let's assume view handles clearing its QTextEdit.
        # We can add a signal if VM needs to explicitly tell View to clear.
        self.log_message_added.emit("---- Log Cleared ----\n") # Signal to view, view can interpret this.

    def save_log_to_file(self):
        logger.info("Save log to file requested by user.")
        # This would involve:
        # 1. Opening a QFileDialog from the View (triggered by VM or View action).
        # 2. Getting the chosen file path.
        # 3. Getting log content (either from View's QTextEdit or if VM aggregated logs).
        # 4. Writing to file.
        # For now, just a log message. The actual implementation needs View interaction.
        self.log_message_added.emit("---- Save Log to File (Not Implemented Yet) ----\n")

    def clear_all_selection(self):
        logger.info("Clear all selection requested by user from VM.")
        # This signal will be caught by MainWindow, which will then interact with its model.
        self.request_clear_selection_signal.emit()


# Custom Log Handler to emit signals for UI
class UILogHandler(logging.Handler):
    def __init__(self, vm_instance: MainWindowVM, parent=None):
        super().__init__()
        self.vm = vm_instance

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        self.vm.log_message_added.emit(msg)

```
