"""Serial I/O runs outside Tk's event loop; only the worker owns the port."""
import queue
import threading
import time

import serial

from .protocol import BAUD, LineBuffer, parse_state

RESET_PULSE_SECONDS = 0.1
BOOT_WAIT_SECONDS = 2.0
HANDSHAKE_TIMEOUT_SECONDS = 12.0
RESPONSE_TIMEOUT_SECONDS = 3.0


class Connection:
    def __init__(self, port: str):
        self.port = port
        self.events = queue.Queue()
        self.commands = queue.Queue()
        self.closing = threading.Event()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def send(self, command: str):
        self.commands.put(command)

    def stop(self):
        # Discard pending motion requests before putting STOP on the wire.
        self.stopping.set()

    def close(self):
        self.closing.set()

    def _clear_commands(self):
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                return

    def _run(self):
        try:
            with serial.Serial(self.port, BAUD, timeout=0.02, write_timeout=0.25) as device:
                self.events.put(("progress", f"Opened {self.port} at {BAUD} baud; resetting Arduino…"))
                # Explicit DTR transition supports the Mega's USB serial reset.
                # Do not send commands into the bootloader while it starts.
                device.dtr = False
                if self.closing.wait(RESET_PULSE_SECONDS):
                    return
                device.dtr = True
                self.events.put(("progress", "Waiting for Arduino to boot…"))
                if self.closing.wait(BOOT_WAIT_SECONDS):
                    return
                device.reset_input_buffer()
                framing = LineBuffer()
                ready = False
                handshake_started = time.monotonic()
                last_rx = handshake_started
                last_ping = float("-inf")
                received_bytes = 0
                last_line = ""
                self.events.put(("progress", "Checking Arduino firmware (STOP / STATUS)…"))
                while not self.closing.is_set():
                    now = time.monotonic()
                    if now - last_ping >= 0.5:
                        # No READY banner is needed: probe even if it was missed.
                        device.write(b"PING\n" if ready else b"STOP\nSTATUS\n")
                        last_ping = now
                    if self.stopping.is_set():
                        self._clear_commands()
                        device.write(b"STOP\n")
                        self.stopping.clear()
                    if ready:
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            pass
                        else:
                            device.write((command + "\n").encode("ascii"))
                    chunk = device.read(max(1, min(device.in_waiting, 512)))
                    received_bytes += len(chunk)
                    for line in framing.feed(chunk):
                        last_line = line
                        if line.startswith("READY "):
                            if "protocol=1" not in line.split():
                                raise ValueError("Unsupported Arduino firmware protocol")
                            # Reset during a connection: require fresh idle state.
                            ready = False
                            handshake_started = time.monotonic()
                            self._clear_commands()
                            self.events.put(("progress", "Arduino reset; checking firmware again…"))
                            device.write(b"STOP\nSTATUS\n")
                        state = parse_state(line)
                        if state is not None:
                            last_rx = time.monotonic()
                            if not ready:
                                if state["mode"] != "IDLE":
                                    device.write(b"STOP\n")
                                    continue
                                ready = True
                                self.events.put(("connected", self.port))
                            self.events.put(("state", state))
                        elif not line.startswith("OK PING"):
                            self.events.put(("log", line))
                    now = time.monotonic()
                    if not ready and now - handshake_started > HANDSHAKE_TIMEOUT_SECONDS:
                        if received_bytes == 0:
                            detail = "No serial bytes received. Check the COM port, USB cable and uploaded sketch."
                        else:
                            detail = f"Received {received_bytes} bytes but no valid idle STATE. Last line: {last_line[:160]!r}."
                        raise TimeoutError(
                            f"{detail} Upload arduino/StepperControl/StepperControl.ino "
                            "using board Arduino Mega or Mega 2560. Firmware must use Serial at 115200 baud."
                        )
                    if ready and now - last_rx > RESPONSE_TIMEOUT_SECONDS:
                        raise TimeoutError("Arduino stopped responding")
                device.write(b"STOP\n")
        except (serial.SerialException, OSError, ValueError, TimeoutError) as error:
            self.events.put(("error", str(error)))
        finally:
            self.events.put(("disconnected", "Serial connection closed"))
