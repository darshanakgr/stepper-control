"""Handshake regressions; no board or motor is accessed."""
import unittest
from unittest.mock import patch

from stepper_control.transport import Connection


IDLE = b"STATE mode=IDLE dir=CW remaining=0 delay=100 spr=800 pause=1000 phase=IDLE\n"


class FakeSerial:
    def __init__(self, clock, response=None):
        self.clock = clock
        self.response = response
        self.data = bytearray()
        self.writes = []
        self.dtr_changes = []
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    @property
    def dtr(self):
        return self.dtr_changes[-1]

    @dtr.setter
    def dtr(self, value):
        self.dtr_changes.append(value)

    @property
    def in_waiting(self):
        return len(self.data)

    def reset_input_buffer(self):
        self.data.clear()

    def write(self, data):
        self.writes.append(data)
        if self.response and data == b"STOP\nSTATUS\n":
            self.data.extend(self.response)

    def read(self, count):
        self.clock[0] += 0.1
        self.reads += 1
        # Force partial reads to exercise real serial line framing.
        count = min(count, 13)
        result = bytes(self.data[:count])
        del self.data[:count]
        return result


class HandshakeTests(unittest.TestCase):
    def run_connection(self, response=None, close_on_state=False):
        clock = [0.0]
        device = FakeSerial(clock, response)
        connection = Connection("TEST")
        events = []

        def emit(event):
            events.append(event)
            if close_on_state and event[0] == "state":
                connection.close()

        connection.events.put = emit
        with patch("stepper_control.transport.serial.Serial", return_value=device), \
             patch("stepper_control.transport.time.monotonic", side_effect=lambda: clock[0]), \
             patch("stepper_control.transport.RESET_PULSE_SECONDS", 0), \
             patch("stepper_control.transport.BOOT_WAIT_SECONDS", 0):
            connection._run()
        return connection, device, events

    def test_connects_without_ready_banner_and_with_fragmented_lines(self):
        _, device, events = self.run_connection(IDLE, close_on_state=True)
        self.assertIn(("connected", "TEST"), events)
        self.assertEqual(device.dtr_changes, [False, True])
        self.assertEqual(device.writes[0], b"STOP\nSTATUS\n")
        self.assertEqual(device.writes[-1], b"STOP\n")
        self.assertFalse(any(kind == "error" for kind, _ in events))

    def test_silence_explains_port_and_sketch_checks(self):
        _, _, events = self.run_connection()
        error = next(data for kind, data in events if kind == "error")
        self.assertIn("No serial bytes received", error)
        self.assertIn("Arduino Mega", error)

    def test_original_sketch_is_reported_as_wrong_protocol(self):
        _, _, events = self.run_connection(b"Stepper ready\nClockwise 360 degrees\n")
        error = next(data for kind, data in events if kind == "error")
        self.assertIn("no valid idle STATE", error)
        self.assertIn("Clockwise 360 degrees", error)

    def test_running_board_is_stopped_before_connection_is_ready(self):
        running = IDLE.replace(b"mode=IDLE", b"mode=MANUAL")
        _, device, events = self.run_connection(running)
        self.assertNotIn(("connected", "TEST"), events)
        self.assertIn(b"STOP\n", device.writes)

    def test_ready_requires_exact_protocol_version(self):
        _, _, events = self.run_connection(b"READY protocol=10\n")
        self.assertTrue(any(kind == "error" and "Unsupported" in data
                            for kind, data in events))


if __name__ == "__main__":
    unittest.main()
