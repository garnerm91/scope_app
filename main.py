"""
main.py

Entry point. Wires the VisaWorker (background thread) to the ScopeWindow
(GUI, main thread) through a queue.Queue, and wires the CSV export module.
"""

import queue
import dearpygui.dearpygui as dpg

from gui.theme import build_theme
from gui.scope_window import ScopeWindow
from instruments.visa_worker import VisaWorker, list_visa_resources
from export.sigrok_csv import write_multi_channel_csv


def main():
    dpg.create_context()

    theme = build_theme()
    dpg.bind_theme(theme)

    worker = VisaWorker(resource_name=None, poll_interval=0.3)  # start simulated

    def save_plot(filepath: str):
        data = window.get_last_capture()
        if not data:
            window.append_log("(nothing to save)")
            return
        try:
            n_rows = write_multi_channel_csv(filepath, data)
            window.append_log(
                f"Saved {n_rows} rows, {len(data)} channel(s) -> {filepath}\n"
                f"   In PulseView: File > Import > CSV. Column format specs: t,*a ,\n"
                f"   Get channel names from first line: checked. Samplerate (Hz): 0 to auto-detect \n"
                f"   Start line: 1. After this right click on the channels to convert to logic"
            )
        except Exception as e:
            window.append_log(f"Save failed: {e}")

    window = ScopeWindow(
        on_run_stop_clicked=worker.toggle_run_stop,
        on_plot_clicked=worker.request_capture,
        on_single_seq_clicked=worker.request_single_sequence,
        on_continuous_toggled=worker.set_continuous,
        on_channels_changed=worker.set_enabled_channels,
        on_send_command=worker.send_command,
        on_query_command=worker.query_command,
        on_refresh_resources=lambda: window.set_resource_options(list_visa_resources()),
        on_connect=worker.connect,
        on_save_plot=save_plot,
    )

    dpg.create_viewport(title="Oscilloscope Viewer", width=1050, height=820)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main_window", True)

    # Populate the resource combo once at startup so the user isn't staring
    # at an empty list before they think to click Refresh.
    window.set_resource_options(list_visa_resources())

    worker.set_enabled_channels(["CH1"])  # matches the CH1 checkbox checked by default
    worker.start()
    window.set_status("Simulated data")

    # Manual render loop (instead of dpg.start_dearpygui()) so we can drain
    # the queue and touch the GUI ONLY from this thread, once per frame.
    try:
        while dpg.is_dearpygui_running():
            try:
                while True:  # drain everything available this frame
                    item = worker.data_queue.get_nowait()

                    if "error" in item:
                        window.set_status(f"Error: {item['error']}")

                    elif "status" in item:
                        window.set_status(item["status"])

                    elif "cmd_error" in item:
                        window.append_log(f">> {item['command']}\n   ERROR: {item['cmd_error']}")

                    elif item.get("cmd_type") == "send":
                        window.append_log(f">> {item['command']}\n   (sent)")

                    elif item.get("cmd_type") == "query":
                        window.append_log(f">> {item['command']}\n   {item['response']}")
                    elif "run_state" in item:
                        window.set_run_state(item["run_state"] == "RUN")
                    elif "channels" in item:
                        window.update_multi(item["channels"])
            except queue.Empty:
                pass

            dpg.render_dearpygui_frame()
    finally:
        worker.stop()
        dpg.destroy_context()


if __name__ == "__main__":
    main()
