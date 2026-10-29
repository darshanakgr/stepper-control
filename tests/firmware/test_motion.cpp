#include "Arduino.h"
#include <cassert>
#include <cstring>
#include "../../arduino/StepperControl/StepperControl.ino"

FakeSerial Serial;
unsigned long simulatedUs = 0;
int pins[64] = {};
unsigned long cwPulses = 0, ccwPulses = 0;
unsigned long micros() { return simulatedUs; }
unsigned long millis() { return simulatedUs / 1000UL; }
void pinMode(uint8_t, uint8_t) {}
void digitalWrite(uint8_t pin, uint8_t value) {
  if (pin == STEP_PIN && value == HIGH && pins[pin] == LOW) {
    if (pins[DIR_PIN] == HIGH) ++cwPulses;
    else ++ccwPulses;
  }
  pins[pin] = value;
}
void command(const char *text) {
  char buffer[96];
  strcpy(buffer, text);
  handleCommand(buffer);
}
void advance(unsigned long durationUs) {
  for (unsigned long t = 0; t < durationUs; t += 100) {
    simulatedUs += 100;
    serviceMotion();
    serviceOutput();
  }
}
int main() {
  setup();
  command("CONFIG 100 800 10");
  command("START MANUAL_LOOP 3");
  assert(mode == MANUAL_LOOP && clockwise && remaining == 3);
  advance(2000);
  assert(cwPulses == 3 && ccwPulses == 0 && pausing);
  advance(11000);
  assert(cwPulses == 3 && ccwPulses == 3 && pausing);
  advance(11000);
  assert(cwPulses == 6 && ccwPulses == 3 && pausing);
  assert(Serial.output.find("mode=MANUAL_LOOP") != std::string::npos);
  command("SPEED 500");
  assert(mode == MANUAL_LOOP && stepDelayUs == 500);
  command("STOP");
  auto total = cwPulses + ccwPulses;
  advance(20000);
  assert(mode == IDLE && pins[ENABLE_PIN] == HIGH);
  assert(cwPulses + ccwPulses == total);

  command("START MANUAL_LOOP 0");
  assert(mode == IDLE);
  command("START MANUAL_LOOP 10000001");
  assert(mode == IDLE);
  command("START MANUAL_LOOP 2");
  advance(3100000);
  assert(mode == IDLE && pins[ENABLE_PIN] == HIGH); // Host timeout.

  command("START MANUAL 4 CCW");
  auto before = ccwPulses;
  advance(10000);
  assert(mode == IDLE && ccwPulses == before + 4);
  command("CONFIG 100 5 10");
  command("START CONTINUOUS");
  before = cwPulses;
  advance(2000);
  assert(mode == CONTINUOUS && cwPulses == before + 5 && pausing);
  command("STOP");
  command("CONFIG 100 800 0");
  command("START MANUAL_LOOP 1");
  before = cwPulses + ccwPulses;
  advance(5000);
  assert(cwPulses + ccwPulses > before + 2 && mode == MANUAL_LOOP);
  command("STOP");
}
