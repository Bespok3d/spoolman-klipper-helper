"""Tier 1, read only: the helper is alive and clean on the printer."""
KLIPPER_START_MARKER = "Start printer at"
HELPER_LOG_SUBJECT = "spool"
SUSPICIOUS_LOG_MARKERS = ("errno", "traceback", "error")


def test_klippy_is_ready(printer):
    assert printer.klippy_state() == "ready"


def test_moonraker_is_connected_to_spoolman(printer):
    assert printer.spoolman_connected()


def _lines_since_last_klipper_start(log_tail):
    log_lines = log_tail.splitlines()
    start_indexes = [
        line_index
        for line_index, line in enumerate(log_lines)
        if KLIPPER_START_MARKER in line
    ]
    if not start_indexes:
        return log_lines
    return log_lines[start_indexes[-1]:]


def _looks_like_helper_trouble(log_line):
    lowered_line = log_line.lower()
    if HELPER_LOG_SUBJECT not in lowered_line:
        return False
    return any(marker in lowered_line for marker in SUSPICIOUS_LOG_MARKERS)


def helper_error_lines(log_tail):
    recent_lines = _lines_since_last_klipper_start(log_tail)
    return [line for line in recent_lines if _looks_like_helper_trouble(line)]


def test_no_helper_errors_since_last_klipper_start(printer):
    assert helper_error_lines(printer.klippy_log_tail()) == []
