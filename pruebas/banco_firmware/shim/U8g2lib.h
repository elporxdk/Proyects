// Shim de U8g2: no dibuja, pero anota el texto de la pantalla para poder
// comprobar desde el banco de pruebas QUE se esta mostrando.
#pragma once
#include <Arduino.h>
#include <vector>
#include <string>

#define U8G2_R0 0
typedef const char *u8g2_font_t;
extern u8g2_font_t u8g2_font_4x6_tr, u8g2_font_5x7_tr, u8g2_font_6x10_tr, u8g2_font_helvB08_tr;

class U8G2_ST7920_128X64_F_HW_SPI {
 public:
  U8G2_ST7920_128X64_F_HW_SPI(int rot, int cs, int rst) { (void)rot; (void)cs; (void)rst; }
  void begin() { began = true; }
  void setBusClock(unsigned long hz) { busClock = hz; busClockSetBeforeBegin = !began; }
  void enableUTF8Print() {}
  void setFontMode(int) {}
  void setFont(u8g2_font_t f) { font = f; }
  void clearBuffer() { pend.clear(); }
  void sendBuffer();
  void setDrawColor(int c) { color = c; }
  void drawStr(int x, int y, const char *s) { (void)x; (void)y; pend.push_back(s); }
  int  getStrWidth(const char *s) { return (int)strlen(s) * 6; }
  void drawHLine(int, int, int) {}
  void drawLine(int, int, int, int) {}
  void drawPixel(int, int) {}
  void drawBox(int, int, int, int) {}
  void drawRBox(int, int, int, int, int) {}
  void drawRFrame(int, int, int, int, int) {}
  void drawDisc(int, int, int) {}
  void drawCircle(int, int, int) {}
  void drawTriangle(int, int, int, int, int, int) {}

  bool began = false, busClockSetBeforeBegin = false;
  unsigned long busClock = 0;
  int color = 1;
  u8g2_font_t font = nullptr;
  std::vector<std::string> pend;      // texto del frame en construccion
};
// Ultimo frame enviado a la pantalla (protegido, lo leen dos hilos)
std::vector<std::string> pantallaUltimoFrame();
bool pantallaContiene(const char *fragmento);
