// Sustituto de la NVS del ESP32. El banco simula que la particion falla con
// la variable g_nvsRota, para poder probar el aviso de "no se puede guardar".
#pragma once
#include <Arduino.h>
extern bool g_nvsRota;          // true = la NVS no deja abrir el namespace
extern bool g_nvsReparable;     // true = borrar la particion la arregla
int nvs_flash_erase();
int nvs_flash_init();
