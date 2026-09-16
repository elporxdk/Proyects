#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <string>
class HTTPClient {
 public:
  void setConnectTimeout(int) {} void setTimeout(int) {} void setReuse(bool) {}
  void collectHeaders(const char**, size_t) {}
  bool begin(const std::string&, uint16_t, const char*) { return true; }
  int  GET() { return -1; }
  int  sendRequest(const char*) { return -1; }
  bool hasHeader(const char*) { return false; }
  std::string getString() { return "{}"; }
  void end() {}
};
