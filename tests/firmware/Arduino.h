// Minimal host simulation for testing the real firmware state machine.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#define LOW 0
#define HIGH 1
#define OUTPUT 1
#define F(value) value
unsigned long micros();
unsigned long millis();
void pinMode(uint8_t, uint8_t);
void digitalWrite(uint8_t, uint8_t);
struct FakeSerial {
  std::string output;
  void begin(unsigned long) {}
  int available() { return 0; }
  int read() { return -1; }
  int availableForWrite() { return 64; }
  void write(uint8_t ch) { output += static_cast<char>(ch); }
  void println(const char *text) { output += std::string(text) + "\n"; }
};
extern FakeSerial Serial;
