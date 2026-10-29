/*
 * STEP/DIR controller for the Python stepper_control app.
 * Serial: 115200 baud, ASCII commands terminated by newline.
 *
 * CONFIG <delay_us> <steps_per_rev> <pause_ms>  (idle only)
 * SPEED <delay_us>                            (also while running)
 * START CONTINUOUS
 * START MANUAL <steps> CW|CCW
 * START MANUAL_LOOP <steps>  (CW, then CCW, repeat until STOP)
 * STOP
 * PING
 * STATUS
 *
 * Delay is the duration of EACH HIGH and LOW phase, matching the original
 * sketch. Nominal pulse frequency = 1,000,000 / (2 * delay_us).
 * Continuous mode starts CW, pauses after each revolution, then reverses.
 * Manual loop does the same using the requested steps per move, independent
 * of steps_per_rev. Both loops use pause_ms between moves.
 * Stop, completion and a 3-second host timeout disable the driver.
 * No acceleration ramp is implemented. Start slowly for your motor/load.
 */
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

const uint8_t STEP_PIN = 3;
const uint8_t DIR_PIN = 4;
const uint8_t ENABLE_PIN = 5;
const uint8_t ENABLE_ACTIVE = LOW;  // Change for an active-high driver.

const unsigned long HOST_TIMEOUT_MS = 3000;
const unsigned long STATUS_INTERVAL_MS = 500;
const unsigned long DIRECTION_SETTLE_US = 10;

enum Mode { IDLE, CONTINUOUS, MANUAL, MANUAL_LOOP };
Mode mode = IDLE;

unsigned long stepDelayUs = 100;
unsigned long stepsPerRev = 800;  // 200-step motor with 1/4 microstepping.
unsigned long pauseMs = 1000;
unsigned long remaining = 0;
unsigned long loopSteps = 0;
unsigned long lastHostMs = 0;
unsigned long lastStatusMs = 0;
unsigned long edgeStartedUs = 0;
unsigned long pauseStartedMs = 0;
unsigned long phaseDurationUs = 0;
bool clockwise = true;
bool stepHigh = false;
bool pausing = false;

char inputLine[96];
uint8_t inputLength = 0;
bool discardLine = false;

// Transmit incrementally so a full UART buffer never delays a STEP edge.
char outputLine[192];
size_t outputLength = 0;
size_t outputOffset = 0;
bool statePending = true;
const char *errorPending = NULL;

void stopMotor() {
  digitalWrite(ENABLE_PIN, ENABLE_ACTIVE == LOW ? HIGH : LOW);
  digitalWrite(STEP_PIN, LOW);
  stepHigh = false;
  pausing = false;
  remaining = 0;
  mode = IDLE;
  statePending = true;
}

void beginLeg(unsigned long steps, bool cw) {
  clockwise = cw;
  remaining = steps;
  pausing = false;
  stepHigh = false;
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, cw ? HIGH : LOW);
  digitalWrite(ENABLE_PIN, ENABLE_ACTIVE);
  edgeStartedUs = micros();
  // Also allow the driver to wake after enabling before the first pulse.
  phaseDurationUs = stepDelayUs > DIRECTION_SETTLE_US
                      ? stepDelayUs : DIRECTION_SETTLE_US;
  if (phaseDurationUs < 1000UL) phaseDurationUs = 1000UL;
  statePending = true;
}

// Parse bounded decimal integers without signed values or overflow.
bool parseNumber(const char *text, unsigned long low, unsigned long high,
                 unsigned long &result) {
  if (!text || !*text) return false;
  unsigned long value = 0;
  for (const char *p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
    unsigned long digit = (unsigned long)(*p - '0');
    if (value > high / 10UL ||
        (value == high / 10UL && digit > high % 10UL)) return false;
    value = value * 10UL + digit;
  }
  if (value < low) return false;
  result = value;
  return true;
}

void error(const char *message) {
  errorPending = message;
  statePending = true;
}

void handleCommand(char *line) {
  char *args[6];
  uint8_t count = 0;
  char *save = NULL;
  char *token = strtok_r(line, " \t\r", &save);
  while (token) {
    if (count == 6) {
      error("too_many_arguments");
      return;
    }
    args[count++] = token;
    token = strtok_r(NULL, " \t\r", &save);
  }
  if (count == 0) return;

  unsigned long a, b, c;
  if (strcmp(args[0], "STOP") == 0 && count == 1) {
    stopMotor();
  } else if ((strcmp(args[0], "PING") == 0 ||
              strcmp(args[0], "STATUS") == 0) && count == 1) {
    statePending = true;
  } else if (strcmp(args[0], "CONFIG") == 0 && count == 4) {
    if (mode != IDLE) { error("busy_use_SPEED_or_STOP"); return; }
    if (!parseNumber(args[1], 100, 1000000UL, a) ||
        !parseNumber(args[2], 1, 10000000UL, b) ||
        !parseNumber(args[3], 0, 60000UL, c)) {
      error("CONFIG_range_delay_100_to_1000000_spr_1_to_10000000_pause_0_to_60000");
      return;
    }
    stepDelayUs = a;
    stepsPerRev = b;
    pauseMs = c;
    statePending = true;
  } else if (strcmp(args[0], "SPEED") == 0 && count == 2) {
    if (!parseNumber(args[1], 100, 1000000UL, a)) {
      error("SPEED_range_100_to_1000000"); return;
    }
    // Finish the current phase at its old duration, then use the new delay.
    stepDelayUs = a;
    statePending = true;
  } else if (strcmp(args[0], "START") == 0) {
    if (mode != IDLE) { error("busy_STOP_before_START"); return; }
    if (count == 2 && strcmp(args[1], "CONTINUOUS") == 0) {
      mode = CONTINUOUS;
      beginLeg(stepsPerRev, true);
    } else if (count == 3 && strcmp(args[1], "MANUAL_LOOP") == 0 &&
               parseNumber(args[2], 1, 10000000UL, a)) {
      loopSteps = a;
      mode = MANUAL_LOOP;
      beginLeg(loopSteps, true);
    } else if (count == 4 && strcmp(args[1], "MANUAL") == 0 &&
               parseNumber(args[2], 1, 10000000UL, a) &&
               (strcmp(args[3], "CW") == 0 || strcmp(args[3], "CCW") == 0)) {
      mode = MANUAL;
      beginLeg(a, strcmp(args[3], "CW") == 0);
    } else {
      error("use_START_CONTINUOUS_or_MANUAL_steps_CW_or_CCW_or_MANUAL_LOOP_steps"); return;
    }
  } else {
    error("unknown_command_or_arguments"); return;
  }
  lastHostMs = millis();
}

void serviceInput() {
  // Bound work per loop even when the host sends many bytes at once.
  for (uint8_t i = 0; i < 16 && Serial.available() > 0; ++i) {
    char ch = (char)Serial.read();
    if (ch == '\n') {
      if (discardLine) {
        error("line_too_long");
      } else {
        inputLine[inputLength] = '\0';
        handleCommand(inputLine);
      }
      inputLength = 0;
      discardLine = false;
    } else if (!discardLine) {
      if (inputLength < sizeof(inputLine) - 1) {
        inputLine[inputLength++] = ch;
      } else {
        discardLine = true;
      }
    }
  }
}

void serviceMotion() {
  if (mode == IDLE) return;
  unsigned long nowMs = millis();
  if ((unsigned long)(nowMs - lastHostMs) >= HOST_TIMEOUT_MS) {
    stopMotor();
    error("host_timeout_motor_stopped");
    return;
  }
  if (pausing) {
    if ((unsigned long)(nowMs - pauseStartedMs) >= pauseMs) {
      beginLeg(mode == MANUAL_LOOP ? loopSteps : stepsPerRev, !clockwise);
    }
    return;
  }
  unsigned long nowUs = micros();
  // Unsigned subtraction remains valid across micros() rollover.
  if ((unsigned long)(nowUs - edgeStartedUs) < phaseDurationUs) return;
  edgeStartedUs = nowUs;
  phaseDurationUs = stepDelayUs;
  if (!stepHigh) {
    digitalWrite(STEP_PIN, HIGH);
    stepHigh = true;
    --remaining;  // Drivers step on the rising edge.
  } else {
    digitalWrite(STEP_PIN, LOW);
    stepHigh = false;
    if (remaining == 0) {
      if (mode == MANUAL) {
        stopMotor();
      } else {
        pausing = true;
        pauseStartedMs = nowMs;
        statePending = true;
      }
    }
  }
}

void serviceOutput() {
  if (outputOffset == outputLength) {
    outputOffset = 0;
    outputLength = 0;
    if (errorPending) {
      outputLength = (size_t)snprintf(outputLine, sizeof(outputLine),
                                     "ERR %s\n", errorPending);
      errorPending = NULL;
    } else if (statePending) {
      const char *modeName = mode == IDLE ? "IDLE" :
                             mode == CONTINUOUS ? "CONTINUOUS" :
                             mode == MANUAL_LOOP ? "MANUAL_LOOP" : "MANUAL";
      const char *phase = mode == IDLE ? "IDLE" : pausing ? "PAUSE" : "MOVING";
      outputLength = (size_t)snprintf(outputLine, sizeof(outputLine),
        "STATE mode=%s dir=%s remaining=%lu delay=%lu spr=%lu pause=%lu phase=%s\n",
        modeName, clockwise ? "CW" : "CCW", remaining, stepDelayUs,
        stepsPerRev, pauseMs, phase);
      statePending = false;
      lastStatusMs = millis();
    }
    if (outputLength >= sizeof(outputLine)) outputLength = sizeof(outputLine) - 1;
  }
  for (uint8_t i = 0; i < 8 && outputOffset < outputLength &&
       Serial.availableForWrite() > 0; ++i) {
    Serial.write((uint8_t)outputLine[outputOffset++]);
  }
}

void setup() {
  // Set the inactive enable level before configuring the pin as output.
  digitalWrite(ENABLE_PIN, ENABLE_ACTIVE == LOW ? HIGH : LOW);
  pinMode(ENABLE_PIN, OUTPUT);
  pinMode(STEP_PIN, OUTPUT);
  pinMode(DIR_PIN, OUTPUT);
  digitalWrite(STEP_PIN, LOW);
  digitalWrite(DIR_PIN, LOW);
  stopMotor();
  Serial.begin(115200);
  // This short startup message fits in the UART buffer; motor is disabled.
  Serial.println(F("READY protocol=1"));
  lastHostMs = millis();
}

void loop() {
  serviceMotion();
  serviceInput();
  serviceMotion();
  if ((unsigned long)(millis() - lastStatusMs) >= STATUS_INTERVAL_MS) {
    statePending = true;
  }
  serviceOutput();
}
