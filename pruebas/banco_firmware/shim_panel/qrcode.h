#pragma once
#include <Arduino.h>
#define ECC_LOW 0
typedef struct { uint8_t version, size; uint8_t *modules; } QRCode;
static inline int qrcode_getBufferSize(int v) { return (v * 4 + 17) * (v * 4 + 17) / 8 + 1; }
static inline int qrcode_initText(QRCode *q, uint8_t *buf, int v, int, const char *) {
  q->version = (uint8_t)v; q->size = (uint8_t)(v * 4 + 17); q->modules = buf; return 0;
}
static inline bool qrcode_getModule(QRCode *q, int x, int y) { return ((x + y) & 1) != 0; }
