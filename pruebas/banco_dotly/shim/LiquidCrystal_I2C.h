// =====================================================================
//  ESTO NO ES FIRMWARE. LCD 16x2 (HD44780 por I2C) simulado.
// =====================================================================
//  Imita lo que importa del chip: la DDRAM (lo que se ve), la CGRAM (los 8
//  caracteres propios) y el detalle traicionero de createChar(): deja el
//  puntero de escritura en la CGRAM, asi que un write() sin setCursor()
//  despues estropea un glifo. El banco lee la pantalla decodificando los
//  pixeles de la CGRAM, no lo que el firmware "cree" que ha pintado.
// =====================================================================
#pragma once
#include <Arduino.h>
#include <string>

class LiquidCrystal_I2C {
 public:
  LiquidCrystal_I2C(uint8_t addr, uint8_t cols, uint8_t rows);
  void init();
  void begin(uint8_t cols, uint8_t rows) { (void)cols; (void)rows; init(); }
  void clear();
  void home() { setCursor(0, 0); }
  void setCursor(uint8_t col, uint8_t row);
  size_t write(uint8_t c);
  void print(const char *s) { while (*s) write((uint8_t)*s++); }
  void createChar(uint8_t location, uint8_t charmap[]);
  void backlight();
  void noBacklight();
  void blink();
  void noBlink();
  void cursor() {}
  void noCursor() {}
  void display() {}
  void noDisplay() {}
};

// ---- lo que usa el banco ----
std::string lcdFila(int fila);            // la fila tal como se ve, en UTF-8
uint8_t     lcdByte(int col, int fila);   // el codigo guardado en la DDRAM
int         lcdCeldaEn(int col, int fila);// puntos de la celda braille (-1 si no es una)
bool        lcdLuz();                     // luz de fondo encendida
int         lcdApagones();                // cuantas veces se ha apagado la luz
bool        lcdParpadeo(int *col, int *fila);
int         lcdEscriturasCgramSueltas();  // write() con el puntero en la CGRAM
