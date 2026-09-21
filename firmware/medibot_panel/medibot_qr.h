// =====================================================================
//  medibot_qr  -  codificador de codigos QR, INCLUIDO EN EL SKETCH
// =====================================================================
//  Por que esta aqui y no se usa la libreria "QRCode" del gestor:
//
//  El core ESP32 (3.x) ya trae un fichero llamado qrcode.h -el componente
//  de Espressif, con esp_qrcode_generate()-, asi que un
//      #include "qrcode.h"
//  se resuelve a ESE y no a la libreria de ricmoo, aunque la instales:
//      error: 'QRCode' does not name a type
//      error: 'qrcode_getModule' ... did you mean 'esp_qrcode_get_module'?
//
//  Con el codificador aqui dentro y con otro nombre no hay choque ni hay
//  nada que instalar: el IDE de Arduino compila solo los ficheros que estan
//  junto al .ino.
//
//  Es la libreria de Richard Moore (MIT), sin tocar salvo el #include de la
//  linea correspondiente. Su licencia, integra, va justo debajo.
// =====================================================================

/**
 * The MIT License (MIT)
 *
 * This library is written and maintained by Richard Moore.
 * Major parts were derived from Project Nayuki's library.
 *
 * Copyright (c) 2017 Richard Moore     (https://github.com/ricmoo/QRCode)
 * Copyright (c) 2017 Project Nayuki    (https://www.nayuki.io/page/qr-code-generator-library)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

/**
 *  Special thanks to Nayuki (https://www.nayuki.io/) from which this library was
 *  heavily inspired and compared against.
 *
 *  See: https://github.com/nayuki/QR-Code-generator/tree/master/cpp
 */


#ifndef MEDIBOT_QR_H
#define MEDIBOT_QR_H

#ifndef __cplusplus
typedef unsigned char bool;
static const bool false = 0;
static const bool true = 1;
#endif

#include <stdint.h>


// QR Code Format Encoding
#define MODE_NUMERIC        0
#define MODE_ALPHANUMERIC   1
#define MODE_BYTE           2


// Error Correction Code Levels
#define ECC_LOW            0
#define ECC_MEDIUM         1
#define ECC_QUARTILE       2
#define ECC_HIGH           3


// If set to non-zero, this library can ONLY produce QR codes at that version
// This saves a lot of dynamic memory, as the codeword tables are skipped
#ifndef LOCK_VERSION
#define LOCK_VERSION       0
#endif


typedef struct QRCode {
    uint8_t version;
    uint8_t size;
    uint8_t ecc;
    uint8_t mode;
    uint8_t mask;
    uint8_t *modules;
} QRCode;


#ifdef __cplusplus
extern "C"{
#endif  /* __cplusplus */



uint16_t qrcode_getBufferSize(uint8_t version);

int8_t qrcode_initText(QRCode *qrcode, uint8_t *modules, uint8_t version, uint8_t ecc, const char *data);
int8_t qrcode_initBytes(QRCode *qrcode, uint8_t *modules, uint8_t version, uint8_t ecc, uint8_t *data, uint16_t length);

bool qrcode_getModule(QRCode *qrcode, uint8_t x, uint8_t y);



#ifdef __cplusplus
}
#endif  /* __cplusplus */


#endif  /* MEDIBOT_QR_H */
