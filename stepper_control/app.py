"""Tk desktop interface. Run with uv run stepper-control."""
import queue
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from serial.tools import list_ports

from .protocol import number
from .transport import Connection


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.connection = None
        self.ready = False
        self.running = False
        self.closing = False
        self.last_error = ""
        root.title("Stepper Control · Arduino")
        root.geometry("760x720")
        root.minsize(700, 680)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", padding=7)
        style.configure("Title.TLabel", font=("Segoe UI", 20, "bold"))
        body = ttk.Frame(root, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Stepper Control", style="Title.TLabel").pack(anchor="w")
        ttk.Label(body, text="Arduino STEP / DIR driver · USB serial · 115200 baud").pack(anchor="w", pady=(2, 14))

        connect = ttk.Frame(body)
        connect.pack(fill="x")
        self.port = ttk.Combobox(connect, width=18)
        self.port.pack(side="left")
        ttk.Button(connect, text="Refresh ports", command=self.refresh).pack(side="left", padx=8)
        self.connect_button = ttk.Button(connect, text="Connect", command=self.toggle_connection)
        self.connect_button.pack(side="left")
        self.status = tk.StringVar(value="Disconnected")
        ttk.Label(body, textvariable=self.status).pack(anchor="w", pady=10)

        settings = ttk.LabelFrame(body, text="Motion settings", padding=12)
        settings.pack(fill="x")
        self.values = {}
        for row, (key, label, default) in enumerate([
            ("delay", "Step HIGH and LOW delay (µs)", "100"),
            ("spr", "Steps per revolution", "800"),
            ("pause", "Pause between moves (ms)", "1000"),
        ]):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=default)
            self.values[key] = var
            ttk.Entry(settings, textvariable=var, width=16).grid(row=row, column=1, padx=16)
            var.trace_add("write", lambda *_: self.update_estimate())
        self.estimate = tk.StringVar()
        ttk.Label(settings, textvariable=self.estimate).grid(row=3, column=0, columnspan=2, sticky="w", pady=5)
        self.apply_button = ttk.Button(settings, text="Apply settings", command=self.apply)
        self.apply_button.grid(row=0, column=2, rowspan=2, padx=8)
        ttk.Label(settings, text="Delay applies live; revolution size and pause apply on next Start.").grid(row=4, column=0, columnspan=3, sticky="w")

        mode = ttk.LabelFrame(body, text="Operating mode", padding=12)
        mode.pack(fill="x", pady=12)
        self.mode = tk.StringVar(value="CONTINUOUS")
        self.mode.trace_add("write", lambda *_: self.update_controls())
        self.radios = []
        for value, label in [("CONTINUOUS", "Continuous: one revolution CW, then CCW, repeat"),
                             ("MANUAL", "Manual: move a specified number of steps")]:
            radio = ttk.Radiobutton(mode, text=label, variable=self.mode, value=value)
            radio.pack(anchor="w", pady=3)
            self.radios.append(radio)
        manual = ttk.Frame(mode)
        manual.pack(fill="x", pady=8)
        ttk.Label(manual, text="Manual steps").pack(side="left")
        self.steps = tk.StringVar(value="800")
        self.steps_entry = ttk.Entry(manual, textvariable=self.steps, width=14)
        self.steps_entry.pack(side="left", padx=10)
        self.direction = ttk.Combobox(manual, values=["CW", "CCW"], state="readonly", width=8)
        self.direction.set("CW")
        self.direction.pack(side="left")
        ttk.Label(manual, text="CW = clockwise").pack(side="left", padx=10)
        self.manual_loop = tk.BooleanVar(value=False)
        self.manual_loop.trace_add("write", lambda *_: self.update_controls())
        self.loop_checkbox = ttk.Checkbutton(
            mode, text="Loop manual steps: CW, then CCW, repeat until Stop",
            variable=self.manual_loop,
        )
        self.loop_checkbox.pack(anchor="w", pady=(0, 4))

        controls = ttk.Frame(body)
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="▶ Start motor", command=self.start)
        self.start_button.pack(side="left", fill="x", expand=True)
        self.stop_button = ttk.Button(controls, text="■ STOP (Space)", command=self.stop)
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.live = tk.StringVar(value="Motor idle")
        ttk.Label(body, textvariable=self.live, font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=12)
        ttk.Label(body, text="Stop releases the driver. The motor has no acceleration ramp.").pack(anchor="w")
        self.log = ScrolledText(body, height=7, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, pady=(8, 0))
        root.bind("<space>", lambda event: self.stop_key())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()
        self.update_estimate()
        self.update_controls()
        root.after(50, self.poll)

    def refresh(self):
        ports = list(list_ports.comports())
        self.port["values"] = [p.device for p in ports]
        if ports and not self.port.get():
            self.port.set(ports[0].device)

    def update_estimate(self):
        try:
            delay = number(self.values["delay"].get(), "delay")
            spr = number(self.values["spr"].get(), "spr")
            self.estimate.set(f"Nominal: {500_000 / delay:,.1f} steps/s · {30_000_000 / (delay * spr):,.1f} RPM (timing overhead excluded)")
        except ValueError:
            self.estimate.set("Enter valid delay and steps per revolution to estimate speed.")

    def update_controls(self):
        self.start_button["state"] = "normal" if self.ready and not self.running else "disabled"
        self.stop_button["state"] = "normal" if self.connection else "disabled"
        self.apply_button["state"] = "normal" if self.ready else "disabled"
        for radio in self.radios:
            radio["state"] = "disabled" if self.running else "normal"
        manual = self.mode.get() == "MANUAL"
        self.loop_checkbox["state"] = "normal" if manual and not self.running else "disabled"
        self.steps_entry["state"] = "normal" if manual and not self.running else "disabled"
        self.direction["state"] = (
            "readonly" if manual and not self.manual_loop.get() and not self.running else "disabled"
        )
        self.port["state"] = "disabled" if self.connection else "normal"

    def toggle_connection(self):
        if self.connection:
            self.connection.close()
            self.ready = False
            self.connect_button["state"] = "disabled"
        elif self.port.get().strip():
            self.connection = Connection(self.port.get().strip())
            self.last_error = ""
            self.status.set("Opening Arduino serial connection…")
            self.connect_button.configure(text="Disconnect")
            self.connection.start()
        else:
            messagebox.showerror("Serial port", "Select or enter a serial port, such as COM3.")
        self.update_controls()

    def config(self):
        return [number(self.values[key].get(), key) for key in ("delay", "spr", "pause")]

    def apply(self):
        try:
            delay, spr, pause = self.config()
            command = f"SPEED {delay}" if self.running else f"CONFIG {delay} {spr} {pause}"
            if self.ready:
                self.connection.send(command)
                self.add_log("> " + command)
                if self.running:
                    self.add_log("Only delay applied while running. Other settings apply on next Start.")
        except ValueError as error:
            messagebox.showerror("Invalid settings", str(error))

    def start(self):
        if not self.ready or self.running:
            return
        try:
            delay, spr, pause = self.config()
            command = "START CONTINUOUS"
            if self.mode.get() == "MANUAL":
                steps = number(self.steps.get(), "steps")
                if self.manual_loop.get():
                    command = f"START MANUAL_LOOP {steps}"
                else:
                    command = f"START MANUAL {steps} {self.direction.get()}"
            self.connection.send(f"CONFIG {delay} {spr} {pause}")
            self.connection.send(command)
            self.running = True
            self.update_controls()
            self.add_log("> " + command)
        except ValueError as error:
            messagebox.showerror("Invalid settings", str(error))

    def stop_key(self):
        self.stop()
        return "break"

    def stop(self):
        if self.connection:
            self.connection.stop()
            self.add_log("> STOP")

    def add_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 300:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def poll(self):
        connection = self.connection
        if connection:
            while True:
                try:
                    kind, data = connection.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "connected":
                    self.ready = True
                    self.last_error = ""
                    self.status.set(f"Connected to {data}")
                elif kind == "progress":
                    self.ready = False
                    self.status.set(data)
                    self.add_log(data)
                elif kind == "state":
                    self.running = data["mode"] != "IDLE"
                    self.live.set(f"{data['mode']} · {data['phase']} · {data['dir']} · {data['remaining']} steps left · delay {data['delay']} µs")
                elif kind == "disconnected":
                    self.ready = self.running = False
                    self.connection = None
                    self.status.set("Connection failed — see error in log" if self.last_error else "Disconnected")
                    self.live.set("Disconnected — motor state unavailable")
                    self.connect_button.configure(text="Connect", state="normal")
                    self.add_log(data)
                else:
                    if kind == "error":
                        self.last_error = data
                    self.add_log(("ERROR: " if kind == "error" else "") + data)
            self.update_controls()
        if self.closing and self.connection is None:
            self.root.destroy()
            return
        self.root.after(50, self.poll)

    def close(self):
        self.closing = True
        if self.connection:
            self.connection.close()
            self.ready = False
            self.update_controls()
        else:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
