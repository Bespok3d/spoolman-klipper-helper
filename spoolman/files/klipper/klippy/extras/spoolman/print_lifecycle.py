class PrintLifecycle:
    def __init__(self, printer, logs, helper):
        self.printer = printer
        self.logs = logs
        self.helper = helper

        self.printer.register_event_handler('print_stats:start', self.on_print_start)
        self.printer.register_event_handler('print_stats:stop', self.on_print_stop)
        self.printer.register_event_handler('pause_resume:cancel', self.on_print_cancel)

    def on_print_start(self):
        self.logs.verbose("New print job start!")
        self.helper.sync_spools_tools()

    def on_print_stop(self):
        self.logs.verbose("Print job ended, clearing.")
        self.helper.u1_tools.clear_map()
        self.helper.tracking.clear_active()

    def on_print_cancel(self):
        self.logs.verbose("Print job canceled, clearing.")
        self.helper.u1_tools.clear_map()
        self.helper.tracking.clear_active()

    def on_print_resume(self):
        self.logs.verbose("Print job resumed, resuming spool tracking")
        if self.helper.mode != "manual":
            self.helper.u1_tools.update_map()
