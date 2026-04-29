/******************************************************
  Pillbox Os - ESP32 port
******************************************************/

#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <EEPROM.h>
#include <RTClib.h>
#include <Stepper.h>  // Stepper

// Configuración OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1

// Pines I2C (puedes cambiarlos si tu ESP32 usa otros, por ej. SDA=21 SCL=22)
#define SDA_PIN 21    // era D2 en ESP8266
#define SCL_PIN 22    // era D1 en ESP8266

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Configuración WiFi STA (modo cliente) - CAMBIO AQUÍ
const char* WIFI_SSID = "Horusx";
const char* WIFI_PASS = "20220110";  // Deja vacío si no tiene contraseña

// Pines motor paso a paso (ULN2003) - 28BYJ-48
// OJO: En ESP8266 usabas D5/D6/D7/D8. Aquí son GPIOs directos.
// Asegúrate de cablear a estos GPIO en el ESP32.
#define IN1 13  // GPIO13
#define IN2 12  // GPIO12
#define IN3 14  // GPIO14
#define IN4 16  // GPIO16

// Pin buzzer
#define BUZZER_PIN 15     // GPIO15

// Configuración motor paso a paso 28BYJ-48
const int stepsPerRevolution = 2048;  // Pasos por revolución para 28BYJ-48
Stepper motor(stepsPerRevolution, IN1, IN3, IN2, IN4);  // Orden de pines: IN1, IN3, IN2, IN4

const int STEPS_FOR_DISPENSE = stepsPerRevolution / 8;  // 256 pasos para ~45 grados
const unsigned long SERVO_COOLDOWN = 2000;              // ms entre dispensaciones

// Direcciones EEPROM - Aumentadas para almacenar nombres
#define EEPROM_SIZE 1024  // Aumentamos para almacenar nombres
#define EEPROM_ALARM_COUNT_ADDR 10
#define EEPROM_ALARMS_ADDR 50  // Comenzamos más tarde para dejar espacio
#define EEPROM_BUZZER_DURATION_ADDR 500
#define EEPROM_INITIALIZED_ADDR 600

// Constantes de tiempo pantalla
const unsigned long DISPLAY_TIMEOUT = 150000; // 2.5 minutos

WebServer server(80);
RTC_DS3231 rtc;

// Variable para estado del RTC
bool rtcAvailable = false;

// Variables para conexión WiFi
unsigned long wifiConnectStartTime = 0;
const unsigned long WIFI_TIMEOUT = 30000; // 30 segundos para conectar

// Estructuras de datos
struct SystemTime {
  uint8_t hours;
  uint8_t minutes;
  uint8_t seconds;
};

// NUEVO: Estructura Alarm con nombre (max 20 caracteres)
struct Alarm {
  uint8_t hour;
  uint8_t minute;
  bool triggered;
  char name[21];  // 20 caracteres + terminador nulo
};

// CAMBIO: Aumentado a 7 alarmas
Alarm alarms[7];
uint8_t alarmCount = 0;
SystemTime currentTime = {0, 0, 0};
unsigned long lastTimeUpdate = 0;

// Estados del sistema
enum SystemState { IDLE, DISPENSING, BUZZING };
SystemState systemState = IDLE;

// Variables de control para el motor paso a paso
bool isDispensing = false;
unsigned long dispenseStartTime = 0;
unsigned long lastDispenseTime = 0;
unsigned long buzzerStart = 0;
uint16_t buzzerDuration = 500; // ms
bool infiniteBuzzer = false;
bool infiniteModeEnabled = false;

// Variables para pantalla
unsigned long lastDisplayUpdate = 0;
String lastUserAction = "Bienvenido!";
String nextAlarm = "No hay alarmas";

// Bandera para EEPROM
bool eepromModified = false;

// Modo apagado
bool sleepMode = false;
bool wasSleeping = false;

// Control de pantalla
bool displayOn = true;
unsigned long lastActivityTime = 0;

// ========= Helpers =========

// Función para formatear dígitos
String formatTwoDigits(uint8_t number) {
  return number < 10 ? "0" + String(number) : String(number);
}

// Función para ordenar alarmas por hora (primero la que suena antes)
void sortAlarms() {
  for (int i = 0; i < alarmCount - 1; i++) {
    for (int j = i + 1; j < alarmCount; j++) {
      // Comparar horas y minutos
      bool shouldSwap = false;
      if (alarms[i].hour > alarms[j].hour) {
        shouldSwap = true;
      } else if (alarms[i].hour == alarms[j].hour && alarms[i].minute > alarms[j].minute) {
        shouldSwap = true;
      }
      
      if (shouldSwap) {
        // Intercambiar alarmas
        Alarm temp = alarms[i];
        alarms[i] = alarms[j];
        alarms[j] = temp;
      }
    }
  }
}

// Función para verificar si una alarma ya existe
bool alarmExists(uint8_t hour, uint8_t minute) {
  for (uint8_t i = 0; i < alarmCount; i++) {
    if (alarms[i].hour == hour && alarms[i].minute == minute) {
      return true;
    }
  }
  return false;
}

// Función para obtener el próximo índice de alarma disponible
int getNextAlarmIndex() {
  for (int i = 0; i < 7; i++) {
    bool indexUsed = false;
    for (uint8_t j = 0; j < alarmCount; j++) {
      // Esto es una simplificación - asumimos que el índice corresponde al orden
      if (j == i) {
        indexUsed = true;
        break;
      }
    }
    if (!indexUsed) {
      return i;
    }
  }
  return -1;
}

// Función para limpiar nombre (remover caracteres especiales)
String sanitizeName(String input) {
  String result = "";
  for (unsigned int i = 0; i < input.length() && i < 20; i++) {
    char c = input.charAt(i);
    if (isAlphaNumeric(c) || c == ' ' || c == '-' || c == '_') {
      result += c;
    }
  }
  return result;
}

// Función para apagar la pantalla
void turnOffDisplay() {
  if (displayOn) {
    display.clearDisplay();
    display.display();
    displayOn = false;
  }
}

// Función para encender la pantalla
void wakeUpDisplay() {
  if (!displayOn) {
    display.ssd1306_command(SSD1306_DISPLAYON);
    displayOn = true;
    lastDisplayUpdate = 0;           // Forzar actualización
    lastActivityTime = millis();     // Reiniciar temporizador
  }
}

// Macro para actualizar actividad
#define UPDATE_ACTIVITY() { lastActivityTime = millis(); wakeUpDisplay(); }

// ========= Dispensador / Stepper =========

// Función para iniciar la dispensación con el motor paso a paso
void startStepperDispense() {
  isDispensing = true;
  dispenseStartTime = millis();

  // Usar la librería Stepper para mover el motor
  motor.step(STEPS_FOR_DISPENSE);

  isDispensing = false;
  lastDispenseTime = millis();

  Serial.println("Dispensación completada: " + String(STEPS_FOR_DISPENSE) + " pasos");

  if (systemState == DISPENSING) {
    systemState = BUZZING;
    buzzerStart = millis();
    if (infiniteModeEnabled) infiniteBuzzer = true;
  }
}

// Función para probar el motor al inicio
void testStepper() {
  Serial.println("Probando motor paso a paso...");

  // Realizar una pequeña rotación de prueba (64 pasos)
  motor.step(64);
  delay(500);

  // Volver a la posición inicial
  motor.step(-64);

  Serial.println("Prueba de motor completada");
}

// ========= Tiempo / RTC =========

void updateTime() {
  if (rtcAvailable) {
    DateTime now = rtc.now();
    currentTime.hours   = now.hour();
    currentTime.minutes = now.minute();
    currentTime.seconds = now.second();
  } else {
    // Fallback a tiempo interno
    if (millis() - lastTimeUpdate >= 1000) {
      lastTimeUpdate = millis();
      if (++currentTime.seconds >= 60) {
        currentTime.seconds = 0;
        if (++currentTime.minutes >= 60) {
          currentTime.minutes = 0;
          currentTime.hours = (currentTime.hours + 1) % 24;
        }
      }
    }
  }
}

void calculateNextAlarm() {
  if (alarmCount == 0) {
    nextAlarm = "No hay alarmas";
    return;
  }

  uint16_t currentMinutes = currentTime.hours * 60 + currentTime.minutes;
  uint16_t closestTime = 1440; // Maximo minutos en un dia
  uint8_t  closestIndex = 255;

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t alarmMinutes = alarms[i].hour * 60 + alarms[i].minute;
    if (alarmMinutes > currentMinutes && alarmMinutes < closestTime) {
      closestTime = alarmMinutes;
      closestIndex = i;
    }
  }

  if (closestIndex != 255) {
    String alarmName = String(alarms[closestIndex].name);
    if (alarmName.length() == 0) alarmName = "Alarma";
    nextAlarm = formatTwoDigits(alarms[closestIndex].hour) + ":" +
                formatTwoDigits(alarms[closestIndex].minute) + " - " + alarmName;
  } else {
    String alarmName = String(alarms[0].name);
    if (alarmName.length() == 0) alarmName = "Alarma";
    nextAlarm = formatTwoDigits(alarms[0].hour) + ":" +
                formatTwoDigits(alarms[0].minute) + " - " + alarmName + " (Sig)";
  }
}

// ========= Lógica de alarma y buzzer =========

void triggerAlarm() {
  if (isDispensing) return;  // Evitar múltiples dispensaciones simultáneas

  systemState = DISPENSING;
  startStepperDispense();

  // Buscar la alarma que se está disparando
  for (uint8_t i = 0; i < alarmCount; i++) {
    if (alarms[i].hour == currentTime.hours && 
        alarms[i].minute == currentTime.minutes &&
        !alarms[i].triggered) {
      
      String alarmName = String(alarms[i].name);
      if (alarmName.length() == 0) alarmName = "Alarma";
      
      lastUserAction = alarmName + ": " +
        formatTwoDigits(currentTime.hours) + ":" +
        formatTwoDigits(currentTime.minutes);
      break;
    }
  }
}

void processBuzzer() {
  if (systemState != BUZZING) return;

  if (infiniteBuzzer) {
    // En ESP32 existe tone() nativo, no necesitamos ledcAttachPin ni nuestra propia función
    tone(BUZZER_PIN, 1500);
    return;
  }

  static unsigned long buzzerEnd = 0;
  if (buzzerEnd == 0) {
    tone(BUZZER_PIN, 1500);                 // iniciar
    buzzerEnd = millis() + buzzerDuration;
  } else if (millis() >= buzzerEnd) {
    noTone(BUZZER_PIN);                     // detener
    systemState = IDLE;
    buzzerEnd = 0;
  }
}

void checkAlarms() {
  if (systemState != IDLE) return;

  for (uint8_t i = 0; i < alarmCount; i++) {
    if (!alarms[i].triggered &&
        alarms[i].hour   == currentTime.hours &&
        alarms[i].minute == currentTime.minutes &&
        currentTime.seconds < 5) {

      alarms[i].triggered = true;

      // Reactivar sistema si está apagado
      if (sleepMode) {
        handleWake(); // declarado más abajo, Arduino genera prototipo
      }

      // Encender pantalla si está apagada
      wakeUpDisplay();

      triggerAlarm();
      break;
    }
  }
  
  // Resetear alarmas a la medianoche
  if (currentTime.hours == 0 && currentTime.minutes == 0 && currentTime.seconds == 0) {
    for (uint8_t i = 0; i < alarmCount; i++) {
      alarms[i].triggered = false;
    }
  }
}

// ========= Pantalla OLED =========

void updateDisplay() {
  if (!displayOn) return;
  if (millis() - lastDisplayUpdate < 1000) return;
  lastDisplayUpdate = millis();

  display.clearDisplay();
  display.setCursor(0, 0);

  // Hora actual
  display.print(formatTwoDigits(currentTime.hours));
  display.print(":");
  display.print(formatTwoDigits(currentTime.minutes));
  display.print(":");
  display.println(formatTwoDigits(currentTime.seconds));

  // Proxima alarma
  display.setCursor(0, 16);
  display.print("Prox: ");
  display.println(nextAlarm.substring(0, 21)); // Limitar a 21 caracteres

  // Estado del sistema
  display.setCursor(0, 32);
  switch(systemState) {
    case DISPENSING: display.print("Dispensando"); break;
    case BUZZING:    display.print("ALARMA!");     break;
    default:
      if (sleepMode) display.print("APAGADO");
      else {
        if (WiFi.status() == WL_CONNECTED) {
          display.print("Conectado");
        } else {
          display.print("Sin WiFi");
        }
      }
  }

  // Ultima accion
  display.setCursor(0, 48);
  display.print(lastUserAction.substring(0, 21));

  display.display();
}

// ========= EEPROM =========

void markEEPROMForUpdate() {
  eepromModified = true;
}

void saveToEEPROM() {
  if (!eepromModified) return;

  EEPROM.write(EEPROM_INITIALIZED_ADDR, 0xAA);
  EEPROM.write(EEPROM_ALARM_COUNT_ADDR, alarmCount);

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t addr = EEPROM_ALARMS_ADDR + (i * 25); // 25 bytes por alarma (1 hora + 1 minuto + 1 triggered + 22 para nombre)
    EEPROM.write(addr,     alarms[i].hour);
    EEPROM.write(addr + 1, alarms[i].minute);
    EEPROM.write(addr + 2, alarms[i].triggered ? 1 : 0);
    
    // Guardar nombre (máx 20 caracteres + terminador nulo)
    for (uint8_t j = 0; j < 21; j++) {
      EEPROM.write(addr + 3 + j, alarms[i].name[j]);
    }
  }

  EEPROM.write(EEPROM_BUZZER_DURATION_ADDR,      buzzerDuration & 0xFF);
  EEPROM.write(EEPROM_BUZZER_DURATION_ADDR + 1, (buzzerDuration >> 8) & 0xFF);
  EEPROM.write(EEPROM_BUZZER_DURATION_ADDR + 2, infiniteModeEnabled ? 1 : 0);

  if (EEPROM.commit()) {
    Serial.println("EEPROM guardada");
    eepromModified = false;
  }
}

void loadFromEEPROM() {
  if (EEPROM.read(EEPROM_INITIALIZED_ADDR) != 0xAA) {
    markEEPROMForUpdate();
    return;
  }

  alarmCount = EEPROM.read(EEPROM_ALARM_COUNT_ADDR);
  // CAMBIO: Ahora soporta hasta 7 alarmas
  if (alarmCount > 7) alarmCount = 0;

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t addr = EEPROM_ALARMS_ADDR + (i * 25);
    alarms[i].hour      = EEPROM.read(addr);
    alarms[i].minute    = EEPROM.read(addr + 1);
    alarms[i].triggered = (EEPROM.read(addr + 2) == 1);
    
    // Cargar nombre
    for (uint8_t j = 0; j < 21; j++) {
      alarms[i].name[j] = EEPROM.read(addr + 3 + j);
    }
    alarms[i].name[20] = '\0'; // Asegurar terminación nula
  }

  buzzerDuration = EEPROM.read(EEPROM_BUZZER_DURATION_ADDR) |
                   (EEPROM.read(EEPROM_BUZZER_DURATION_ADDR + 1) << 8);

  if (buzzerDuration < 100 || buzzerDuration > 5000) buzzerDuration = 500;

  infiniteModeEnabled = (EEPROM.read(EEPROM_BUZZER_DURATION_ADDR + 2) == 1);
}

// ========= Conexión WiFi =========

bool connectToWiFi() {
  Serial.println("Conectando a WiFi...");
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println("Conectando WiFi");
  display.println(WIFI_SSID);
  display.display();
  
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 30) { // 30 intentos = 15 segundos
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi conectado!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
    
    display.clearDisplay();
    display.setCursor(0, 0);
    display.println("WiFi Conectado");
    display.print("IP: ");
    display.println(WiFi.localIP());
    display.display();
    delay(2000);
    
    return true;
  } else {
    Serial.println("\nError conectando WiFi");
    display.clearDisplay();
    display.setCursor(0, 0);
    display.println("Error WiFi");
    display.println("Modo offline");
    display.display();
    delay(2000);
    return false;
  }
}

// ========= Handlers HTTP =========

void handleRoot() {
  UPDATE_ACTIVITY();
  String html = R"rawliteral(
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pillbox</title>
<style>
  :root {
    --primary-color: #00FF80;
    --secondary-color: #32CD32;
    --accent-color: #00FF7F;
    --bg-dark: #000000;
    --bg-light: #FFFFFF;
    --text-dark: #FFFFFF;
    --text-light: #333333;
    --card-bg-dark: #1A1A1A;
    --card-bg-light: #F5F5F5;
    --border-dark: #333333;
    --border-light: #DDDDDD;
    --shadow-dark: rgba(0, 0, 0, 0.3);
    --shadow-light: rgba(0, 0, 0, 0.1);
  }

  /* Tema Azul */
  .theme-blue {
    --primary-color: #00BFFF;
    --secondary-color: #1E90FF;
    --accent-color: #87CEEB;
  }

  /* Tema Morado */
  .theme-purple {
    --primary-color: #9370DB;
    --secondary-color: #8A2BE2;
    --accent-color: #DDA0DD;
  }

  /* Tema Naranja */
  .theme-orange {
    --primary-color: #FF8C00;
    --secondary-color: #FF7F50;
    --accent-color: #FFA500;
  }

  /* Tema Rosa */
  .theme-pink {
    --primary-color: #FF69B4;
    --secondary-color: #FF1493;
    --accent-color: #FFB6C1;
  }

  /* Tema Cyan */
  .theme-cyan {
    --primary-color: #00FFFF;
    --secondary-color: #20B2AA;
    --accent-color: #AFEEEE;
  }

  /* Tema Rojo */
  .theme-red {
    --primary-color: #FF4500;
    --secondary-color: #DC143C;
    --accent-color: #FF6347;
  }

  body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    text-align: center;
    background-color: var(--bg-dark);
    color: var(--text-dark);
    margin: 0;
    padding: 0;
    transition: all 0.3s ease;
  }
  body.light-mode {
    background-color: var(--bg-light);
    color: var(--text-light);
  }
  .container {
    max-width: 600px;
    margin: 0 auto;
    padding: 20px;
  }
  .time {
    font-size: 2.5em;
    margin: 20px;
    color: var(--primary-color);
    text-shadow: 0 0 10px rgba(0, 255, 128, 0.3);
    transition: all 0.3s ease;
  }
  button {
    color: white;
    padding: 12px 24px;
    border: none;
    border-radius: 8px;
    margin: 5px;
    cursor: pointer;
    font-weight: 500;
    transition: all 0.3s ease;
    box-shadow: 0 2px 5px var(--shadow-dark);
  }
  button:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 10px var(--shadow-dark);
  }
  input[type="time"], input[type="text"], input[type="number"] {
    background: var(--card-bg-dark);
    border: 2px solid var(--secondary-color);
    color: var(--text-dark);
    padding: 8px 12px;
    border-radius: 6px;
    margin: 10px;
    transition: all 0.3s ease;
  }
  body.light-mode input[type="time"], 
  body.light-mode input[type="text"],
  body.light-mode input[type="number"] {
    background: var(--card-bg-light);
    border: 2px solid var(--secondary-color);
    color: var(--text-light);
  }
  input[type="time"]:focus, 
  input[type="text"]:focus,
  input[type="number"]:focus {
    outline: none;
    border-color: var(--accent-color);
    box-shadow: 0 0 5px rgba(0, 255, 127, 0.3);
  }
  .alarm-list {
    margin: 20px auto;
    padding: 20px;
    background: linear-gradient(145deg, var(--card-bg-dark), #0F0F0F);
    border-radius: 12px;
    border: 1px solid var(--border-dark);
    box-shadow: 0 4px 10px var(--shadow-dark);
    transition: all 0.3s ease;
  }
  body.light-mode .alarm-list {
    background: linear-gradient(145deg, var(--card-bg-light), #EEEEEE);
    border: 1px solid var(--border-light);
    box-shadow: 0 4px 10px var(--shadow-light);
  }
  .alarm-list h3, .alarm-list h4 {
    color: var(--accent-color);
    margin-bottom: 15px;
    transition: all 0.3s ease;
  }
  .footer {
    margin-top: 30px;
    color: #888888;
    font-size: 0.8em;
    padding: 20px;
    border-top: 1px solid var(--border-dark);
  }
  body.light-mode .footer {
    border-top: 1px solid var(--border-light);
  }
  .cooldown {
    color: #FF6B6B;
    margin: 10px;
    font-weight: 500;
  }
  .dispense-button {
    background: linear-gradient(145deg, var(--accent-color), var(--secondary-color)) !important;
    font-size: 1em !important;
    padding: 10px 20px !important;
    border-radius: 8px;
    margin: 10px 5px;
    min-width: 140px;
  }
  .dispense-button:hover {
    background: linear-gradient(145deg, var(--secondary-color), var(--primary-color)) !important;
  }
  .dispense-button:disabled {
    background: linear-gradient(145deg, #666666, #555555) !important;
    cursor: not-allowed !important;
    transform: none !important;
    box-shadow: none !important;
  }
  .processing {
    background: linear-gradient(145deg, #FFA500, #FF8C00) !important;
    cursor: wait !important;
  }
  .stop-button {
    background: linear-gradient(145deg, #FF6B6B, #FF5555) !important;
    padding: 10px 20px !important;
    margin: 10px 5px;
    min-width: 120px;
  }
  .stop-button:hover {
    background: linear-gradient(145deg, #FF5555, #FF3333) !important;
  }
  .sleep-button {
    padding: 10px 20px !important;
    margin: 10px 5px;
    min-width: 120px;
    color: white !important;
    transition: all 0.3s ease;
  }
  .sleep-button:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 4px 10px var(--shadow-dark);
  }
  .alarm-item {
    background: #2A2A2A;
    padding: 12px;
    margin: 8px 0;
    border-radius: 6px;
    border-left: 4px solid var(--secondary-color);
    transition: all 0.3s ease;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  body.light-mode .alarm-item {
    background: #F0FFF0;
    color: var(--text-light);
    border-left: 4px solid var(--secondary-color);
  }
  .alarm-info {
    flex-grow: 1;
    text-align: left;
  }
  .alarm-time {
    font-weight: bold;
    color: var(--accent-color);
  }
  .alarm-name {
    font-style: italic;
    color: #AAAAAA;
    margin-left: 10px;
  }
  body.light-mode .alarm-name {
    color: #666666;
  }
  .remove-link {
    color: #FF6B6B;
    text-decoration: none;
    padding: 4px 8px;
    border-radius: 4px;
    transition: background 0.3s ease;
    white-space: nowrap;
  }
  .remove-link:hover {
    background: rgba(255, 107, 107, 0.1);
  }
  h1 {
    color: var(--accent-color);
    font-size: 2.2em;
    margin-bottom: 10px;
    text-shadow: 0 0 15px rgba(0, 255, 127, 0.2);
    transition: all 0.3s ease;
  }
  h3 {
    color: #CCCCCC;
    margin: 15px 0;
    transition: all 0.3s ease;
  }
  body.light-mode h3 {
    color: #666666;
  }
  label {
    color: #CCCCCC;
    display: inline-block;
    margin: 10px 0;
    transition: all 0.3s ease;
  }
  body.light-mode label {
    color: #555555;
  }
  input[type="checkbox"] {
    accent-color: var(--secondary-color);
    margin-right: 8px;
  }
  .theme-toggle {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: linear-gradient(145deg, var(--primary-color), var(--secondary-color));
    color: white;
    border: none;
    border-radius: 50px;
    padding: 12px 18px;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.3s ease;
    box-shadow: 0 3px 8px rgba(60, 179, 113, 0.3);
    z-index: 1000;
  }
  .theme-toggle:hover {
    background: linear-gradient(145deg, var(--secondary-color), var(--accent-color));
    transform: scale(1.05);
    box-shadow: 0 5px 12px rgba(60, 179, 113, 0.4);
  }

  /* Selector de colores */
  .color-selector {
    position: fixed;
    bottom: 80px;
    right: 20px;
    background: var(--card-bg-dark);
    border: 2px solid var(--secondary-color);
    border-radius: 15px;
    padding: 15px;
    display: none;
    z-index: 1001;
    box-shadow: 0 5px 15px var(--shadow-dark);
    transition: all 0.3s ease;
  }
  body.light-mode .color-selector {
    background: var(--card-bg-light);
    border: 2px solid var(--secondary-color);
    box-shadow: 0 5px 15px var(--shadow-light);
  }
  .color-selector.show {
    display: block;
    animation: slideIn 0.3s ease;
  }
  @keyframes slideIn {
    from { opacity: 0; transform: translateY(20px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .color-selector h4 {
    color: var(--primary-color);
    margin: 0 0 10px 0;
    font-size: 0.9em;
  }
  .color-option {
    display: flex;
    align-items: center;
    margin: 8px 0;
    cursor: pointer;
    padding: 5px;
    border-radius: 8px;
    transition: background 0.3s ease;
  }
  .color-option:hover {
    background: rgba(255, 255, 255, 0.1);
  }
  body.light-mode .color-option:hover {
    background: rgba(0, 0, 0, 0.05);
  }
  .color-preview {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    margin-right: 8px;
    border: 2px solid rgba(255, 255, 255, 0.3);
  }
  .color-name {
    color: var(--text-dark);
    font-size: 0.8em;
    min-width: 60px;
  }
  body.light-mode .color-name {
    color: var(--text-light);
  }
  .color-button {
    position: fixed;
    bottom: 20px;
    right: 120px;
    background: linear-gradient(145deg, var(--accent-color), var(--secondary-color));
    color: white;
    border: none;
    border-radius: 50px;
    padding: 12px 18px;
    cursor: pointer;
    font-size: 0.9em;
    transition: all 0.3s ease;
    box-shadow: 0 3px 8px rgba(60, 179, 113, 0.3);
    z-index: 1000;
  }
  .color-button:hover {
    background: linear-gradient(145deg, var(--secondary-color), var(--primary-color));
    transform: scale(1.05);
    box-shadow: 0 5px 12px rgba(60, 179, 113, 0.4);
  }

  /* Estilos para botones con colores dinámicos */
  .dynamic-button {
    background: linear-gradient(145deg, var(--primary-color), var(--secondary-color)) !important;
  }
  .dynamic-button:hover {
    background: linear-gradient(145deg, var(--secondary-color), var(--accent-color)) !important;
  }

  @media (max-width: 768px) {
    .color-selector {
      right: 10px;
      bottom: 70px;
      padding: 10px;
    }
    .theme-toggle, .color-button {
      bottom: 10px;
      padding: 10px 14px;
      font-size: 0.8em;
    }
    .color-button {
      right: 110px;
    }
    .alarm-item {
      flex-direction: column;
      align-items: flex-start;
    }
    .remove-link {
      align-self: flex-end;
      margin-top: 5px;
    }
  }
</style>
<script>
  function updateTime() {
    fetch('/current-time')
      .then(r => r.json())
      .then(t => {
        document.getElementById('time').innerHTML =
          `${t.h.toString().padStart(2, '0')}:${t.m.toString().padStart(2, '0')}:${t.s.toString().padStart(2, '0')}`;
      });
  }

  setInterval(updateTime, 1000);

  // Cargar configuración guardada al iniciar
  window.onload = function() {
    const savedTheme = localStorage.getItem('theme');
    const savedColor = localStorage.getItem('colorTheme');
    const themeBtn = document.getElementById('theme-btn');

    // Aplicar tema claro/oscuro
    if (savedTheme === 'light') {
      document.body.classList.add('light-mode');
      themeBtn.textContent = 'Oscuro';
    } else {
      themeBtn.textContent = 'Claro';
    }

    // Aplicar tema de color
    if (savedColor && savedColor !== 'default') {
      document.body.className = document.body.className.replace(/theme-\w+/g, '');
      if (savedTheme === 'light') {
        document.body.className = 'light-mode ' + savedColor;
      } else {
        document.body.className = savedColor;
      }
    }
  };

  function toggleTheme() {
    const body = document.body;
    const themeBtn = document.getElementById('theme-btn');
    const isLightMode = body.classList.contains('light-mode');

    if (isLightMode) {
      body.classList.remove('light-mode');
      themeBtn.textContent = 'Claro';
      localStorage.setItem('theme', 'dark');
    } else {
      body.classList.add('light-mode');
      themeBtn.textContent = 'Oscuro';
      localStorage.setItem('theme', 'light');
    }
  }

  function toggleColorSelector() {
    const selector = document.getElementById('color-selector');
    selector.classList.toggle('show');
  }

  function changeColorTheme(theme) {
    const body = document.body;
    const isLightMode = body.classList.contains('light-mode');

    // Remover temas de color anteriores
    body.className = body.className.replace(/theme-\w+/g, '');

    // Aplicar nuevo tema
    if (theme !== 'default') {
      if (isLightMode) {
        body.className = 'light-mode ' + theme;
      } else {
        body.className = theme;
      }
    } else {
      if (isLightMode) {
        body.className = 'light-mode';
      } else {
        body.className = '';
      }
    }

    // Guardar selección
    localStorage.setItem('colorTheme', theme);

    // Ocultar selector
    document.getElementById('color-selector').classList.remove('show');
  }

  // Cerrar selector al hacer clic fuera
  document.addEventListener('click', function(event) {
    const selector = document.getElementById('color-selector');
    const colorButton = document.getElementById('color-btn');

    if (!selector.contains(event.target) && !colorButton.contains(event.target)) {
      selector.classList.remove('show');
    }
  });
</script>
</head>
<body>
  <button class="theme-toggle" id="theme-btn" onclick="toggleTheme()">Modo Claro</button>
  <button class="color-button" id="color-btn" onclick="toggleColorSelector()">Colores</button>

  <div class="color-selector" id="color-selector">
    <h4>Seleccionar Tema</h4>
    <div class="color-option" onclick="changeColorTheme('default')">
      <div class="color-preview" style="background: linear-gradient(45deg, #00FF80, #32CD32);"></div>
      <span class="color-name">Verde</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-blue')">
      <div class="color-preview" style="background: linear-gradient(45deg, #00BFFF, #1E90FF);"></div>
      <span class="color-name">Azul</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-purple')">
      <div class="color-preview" style="background: linear-gradient(45deg, #9370DB, #8A2BE2);"></div>
      <span class="color-name">Morado</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-orange')">
      <div class="color-preview" style="background: linear-gradient(45deg, #FF8C00, #FF7F50);"></div>
      <span class="color-name">Naranja</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-pink')">
      <div class="color-preview" style="background: linear-gradient(45deg, #FF69B4, #FF1493);"></div>
      <span class="color-name">Rosa</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-cyan')">
      <div class="color-preview" style="background: linear-gradient(45deg, #00FFFF, #20B2AA);"></div>
      <span class="color-name">Cyan</span>
    </div>
    <div class="color-option" onclick="changeColorTheme('theme-red')">
      <div class="color-preview" style="background: linear-gradient(45deg, #FF4500, #DC143C);"></div>
      <span class="color-name">Rojo</span>
    </div>
  </div>

  <div class="container">
    <h1>Pillbox</h1>
    <div class="time" id="time">)rawliteral";

  // Añadir hora actual
  html += formatTwoDigits(currentTime.hours) + ":" +
          formatTwoDigits(currentTime.minutes) + ":" +
          formatTwoDigits(currentTime.seconds);

  html += R"rawliteral(</div>
    <h3>Siguiente dosis: )rawliteral";

  // Añadir proxima alarma
  html += nextAlarm;

  html += R"rawliteral(</h3>

    <div class="alarm-list">
      <h4>Programar Nueva Alarma</h4>
      <form action="/add-alarm" method="post">
        <input type="time" name="alarm" required><br>
        <input type="text" name="name" placeholder="Nombre de la alarma (opcional)" maxlength="20"><br>
        <button type="submit" class="dynamic-button">Agregar Alarma</button>
      </form>

      <h4>Alarmas Activas ()rawliteral";
  
  html += String(alarmCount) + "/7)";

  html += R"rawliteral(</h4>)rawliteral";

  // Generar lista de alarmas
  for (uint8_t i = 0; i < alarmCount; i++) {
    html += "<div class='alarm-item'>";
    html += "<div class='alarm-info'>";
    html += "<span class='alarm-time'>";
    html += formatTwoDigits(alarms[i].hour) + ":" + formatTwoDigits(alarms[i].minute);
    html += "</span>";
    String alarmName = String(alarms[i].name);
    if (alarmName.length() > 0) {
      html += "<span class='alarm-name'>" + alarmName + "</span>";
    }
    html += "</div>";
    html += "<a href='/remove-alarm?id=" + String(i) + "' class='remove-link'>Eliminar</a></div>";
  }

  html += R"rawliteral(
    </div>

    <div class="alarm-list">
      <h3>Configuracion</h3>
      <form action="/set-time" method="post">
        <input type="time" name="time" required step="1">
        <button type="submit" class="dynamic-button">Establecer Hora</button>
      </form>
    </div>

    <div class="alarm-list">
      <h3>Configuracion de la alarma</h3>
      <form action="/set-buzzer" method="post">
        <label for="duration">Duracion del sonido (segundos):</label><br>
        <input type="number" name="duration" value=")rawliteral";

  // Añadir duración del buzzer en segundos
  html += String(buzzerDuration / 1000.0, 1);

  html += R"rawliteral(" min="0.1" max="5" step="0.1" required>
        <br>
        <button type="submit" class="dynamic-button">Guardar Configuracion</button>
      </form>
      <p>
        <label>
          <input type="checkbox" id="infiniteMode" onchange="updateInfiniteMode(this.checked)" )rawliteral";

  // Añadir estado del modo infinito
  html += infiniteModeEnabled ? "checked" : "";

  html += R"rawliteral(>
          Activar alarma indefinida (cuando suene una alarma)
        </label>
      </p>
      <script>
        function updateInfiniteMode(checked) {
          fetch('/infinite-buzzer?enabled=' + (checked ? '1' : '0'))
            .then(response => {
              if (!response.ok) alert('Error al actualizar configuracion');
            });
        }
      </script>
    </div>

    <div style="margin-top: 30px;">
      <button onclick="dispenseNow()" class="dispense-button">Dispensar</button>
      <button onclick="location.href='/stop-buzzer'" class="stop-button">Detener Alarma</button>
      <button onclick="sleepSystem()" class="sleep-button" id="sleepBtn")rawliteral";

  // Determinar estado del botón de apagado - VERDE cuando hay alarmas, ROJO cuando no hay
  if (alarmCount == 0) {
    html += " disabled style='background: linear-gradient(145deg, #FF6B6B, #FF5555) !important;'";
  } else {
    html += " style='background: linear-gradient(145deg, #68D391, #2F855A) !important;'";
  }

  html += R"rawliteral(>
        Apagar Sistema
      </button>
      <div id="cooldownMessage" class="cooldown"></div>
    </div>

    <script>
      let isDispensing = false;
      let dispenseTimeout;

      function dispenseNow() {
        // Evitar multiples clics
        if (isDispensing) {
          document.getElementById('cooldownMessage').innerText = 'Dispensando... Por favor espere';
          return;
        }

        const lastDispense = )rawliteral";

  // Añadir tiempo de ultima dispensacion
  html += String(lastDispenseTime);

  html += R"rawliteral(;
        const now = Date.now();
        const cooldown = )rawliteral";

  // Añadir tiempo de cooldown
  html += String(SERVO_COOLDOWN);

  html += R"rawliteral(;

        if (now - lastDispense < cooldown) {
          const remaining = Math.ceil((cooldown - (now - lastDispense)) / 1000);
          document.getElementById('cooldownMessage').innerText =
            'Espere ' + remaining + ' segundos para dispensar';
          return;
        }

        // Marcar como procesando
        isDispensing = true;
        const dispenseBtn = document.querySelector('.dispense-button');
        const originalText = dispenseBtn.textContent;

        // Cambiar apariencia del boton
        dispenseBtn.disabled = true;
        dispenseBtn.classList.add('processing');
        dispenseBtn.textContent = 'Dispensando...';

        document.getElementById('cooldownMessage').innerText = 'Procesando dispensado...';

        // Redirigir al endpoint
        window.location.href = '/dispense';

        // Timeout de seguridad para resetear el estado (en caso de error de red)
        dispenseTimeout = setTimeout(() => {
          isDispensing = false;
          dispenseBtn.disabled = false;
          dispenseBtn.classList.remove('processing');
          dispenseBtn.textContent = originalText;
          document.getElementById('cooldownMessage').innerText = 'Timeout - Intente nuevamente';
        }, 10000); // 10 segundos timeout
      }

      function sleepSystem() {
        if (confirm("Apagar sistema. Se reactivara automaticamente con la proxima alarma.")) {
          const sleepBtn = document.getElementById('sleepBtn');
          sleepBtn.disabled = true;
          sleepBtn.innerHTML = 'Apagando...';
          sleepBtn.style.background = 'linear-gradient(145deg, #68D391, #2F855A)';

          fetch('/sleep')
            .then(response => {
              if (response.ok) {
                sleepBtn.innerHTML = 'Apagado';
              } else {
                sleepBtn.innerHTML = 'Error';
                setTimeout(() => {
                  sleepBtn.innerHTML = 'Apagar Sistema';
                  sleepBtn.disabled = false;
                  sleepBtn.style.background = 'linear-gradient(145deg, #68D391, #2F855A)';
                }, 2000);
              }
            });
        }
      }

      // Resetear estado cuando la pagina se recarga
      window.addEventListener('beforeunload', function() {
        if (dispenseTimeout) {
          clearTimeout(dispenseTimeout);
        }
      });
    </script>

    <div class="footer">
      <p>1ECA - Made with love by:<br>Diego Rivera, Julio Sura,<br>Diego Yanes y Elian Lemus.</p>
      <p>Conectado a: )rawliteral";
      
  html += WIFI_SSID;
  html += "<br>IP: ";
  if (WiFi.status() == WL_CONNECTED) {
    html += WiFi.localIP().toString();
  } else {
    html += "Sin conexión";
  }
  
  html += R"rawliteral(</p>
      <p><a href="/list" target="_blank">Ver alarmas en formato JSON</a></p>
    </div>
  </div>
</body></html>)rawliteral";

  server.send(200, "text/html", html);
}

void handleCurrentTime() {
  UPDATE_ACTIVITY();
  String json = "{";
  json += "\"h\":" + String(currentTime.hours) + ",";
  json += "\"m\":" + String(currentTime.minutes) + ",";
  json += "\"s\":" + String(currentTime.seconds);
  json += "}";
  server.send(200, "application/json", json);
}

// NUEVO: Endpoint para listar alarmas en JSON
void handleListAlarms() {
  UPDATE_ACTIVITY();
  String json = "[";
  for (uint8_t i = 0; i < alarmCount; i++) {
    if (i > 0) json += ",";
    json += "{";
    json += "\"id\":" + String(i) + ",";
    json += "\"hour\":" + String(alarms[i].hour) + ",";
    json += "\"minute\":" + String(alarms[i].minute) + ",";
    json += "\"name\":\"" + String(alarms[i].name) + "\",";
    json += "\"triggered\":" + String(alarms[i].triggered ? "true" : "false") + ",";
    json += "\"timeString\":\"" + formatTwoDigits(alarms[i].hour) + ":" + formatTwoDigits(alarms[i].minute) + "\",";
    
    // Calcular minutos totales para ordenamiento
    uint16_t totalMinutes = alarms[i].hour * 60 + alarms[i].minute;
    json += "\"totalMinutes\":" + String(totalMinutes);
    
    json += "}";
  }
  json += "]";
  
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

void handleSetTime() {
  UPDATE_ACTIVITY();
  if (server.method() == HTTP_POST) {
    String timeParam = server.arg("time");
    uint8_t newHours   = timeParam.substring(0, 2).toInt();
    uint8_t newMinutes = timeParam.substring(3, 5).toInt();
    uint8_t newSeconds = timeParam.length() >= 8 ? timeParam.substring(6, 8).toInt() : 0;

    if (newHours < 24 && newMinutes < 60 && newSeconds < 60) {
      if (rtcAvailable) {
        rtc.adjust(DateTime(2023, 1, 1, newHours, newMinutes, newSeconds));
      }
      currentTime.hours = newHours;
      currentTime.minutes = newMinutes;
      currentTime.seconds = newSeconds;
      lastUserAction = "Hora actualizada";
    }
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleAddAlarm() {
  UPDATE_ACTIVITY();
  if (server.method() == HTTP_POST) {
    // CAMBIO: Aumentado límite a 7 alarmas
    if (alarmCount >= 7) {
      lastUserAction = "Error: Max 7 alarmas";
      server.sendHeader("Location", "/");
      server.send(303);
      return;
    }

    String alarmTime = server.arg("alarm");
    uint8_t alarmHour   = alarmTime.substring(0, 2).toInt();
    uint8_t alarmMinute = alarmTime.substring(3, 5).toInt();
    String alarmName = server.arg("name");
    
    // Limpiar el nombre
    alarmName = sanitizeName(alarmName);
    
    // Si no hay nombre, asignar uno por defecto
    if (alarmName.length() == 0) {
      alarmName = "Alarma " + String(alarmCount + 1);
    }

    // Verificar si la alarma ya existe
    if (alarmExists(alarmHour, alarmMinute)) {
      lastUserAction = "Error: Alarma duplicada";
      server.sendHeader("Location", "/");
      server.send(303);
      return;
    }

    if (alarmHour < 24 && alarmMinute < 60) {
      // Encontrar el índice para insertar manteniendo el orden
      int insertIndex = alarmCount;
      uint16_t newAlarmMinutes = alarmHour * 60 + alarmMinute;
      
      for (uint8_t i = 0; i < alarmCount; i++) {
        uint16_t existingAlarmMinutes = alarms[i].hour * 60 + alarms[i].minute;
        if (newAlarmMinutes < existingAlarmMinutes) {
          insertIndex = i;
          break;
        }
      }
      
      // Desplazar alarmas si es necesario
      for (int i = alarmCount; i > insertIndex; i--) {
        alarms[i] = alarms[i-1];
      }
      
      // Crear nueva alarma
      alarms[insertIndex].hour = alarmHour;
      alarms[insertIndex].minute = alarmMinute;
      alarms[insertIndex].triggered = false;
      alarmName.toCharArray(alarms[insertIndex].name, 21);
      
      alarmCount++;
      
      lastUserAction = "Alarma '" + alarmName + "' agregada";
      markEEPROMForUpdate();
    }
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleDispense() {
  UPDATE_ACTIVITY();
  if (millis() - lastDispenseTime > SERVO_COOLDOWN && systemState == IDLE) {
    triggerAlarm();
    lastUserAction = "Dispensacion manual";
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleSnooze() {
  UPDATE_ACTIVITY();
  currentTime.minutes = (currentTime.minutes + 10) % 60;
  if (currentTime.minutes < 10) currentTime.hours = (currentTime.hours + 1) % 24;

  for (uint8_t i = 0; i < alarmCount; i++) {
    alarms[i].triggered = false;
  }

  lastUserAction = "Alarma pospuesta";
  markEEPROMForUpdate();
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleRemoveAlarm() {
  UPDATE_ACTIVITY();
  if (server.args() > 0) {
    uint8_t id = server.arg("id").toInt();
    if (id < alarmCount) {
      String removedName = String(alarms[id].name);
      if (removedName.length() == 0) removedName = "Alarma";
      
      for (uint8_t i = id; i < alarmCount - 1; i++) {
        alarms[i] = alarms[i + 1];
      }
      alarmCount--;

      lastUserAction = "Alarma '" + removedName + "' eliminada";
      markEEPROMForUpdate();
    }
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleSetBuzzer() {
  UPDATE_ACTIVITY();
  if (server.method() == HTTP_POST) {
    float seconds = server.arg("duration").toFloat();
    uint16_t newDuration = (uint16_t)(seconds * 1000);
    if (newDuration >= 100 && newDuration <= 5000) {
      buzzerDuration = newDuration;
      lastUserAction = "Buzzer actualizado";
      markEEPROMForUpdate();
    }
  }
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleStopBuzzer() {
  UPDATE_ACTIVITY();
  infiniteBuzzer = false;
  noTone(BUZZER_PIN);
  systemState = IDLE;
  lastUserAction = "Alarma detenida";
  server.sendHeader("Location", "/");
  server.send(303);
}

void handleInfiniteBuzzer() {
  UPDATE_ACTIVITY();
  if (server.args() > 0) {
    infiniteModeEnabled = (server.arg("enabled") == "1");
    lastUserAction = infiniteModeEnabled ? "Modo infinito ON" : "Modo infinito OFF";
    markEEPROMForUpdate();
    server.send(200, "text/plain", "OK");
  }
}

// ======== FUNCIONES DE APAGADO / REACTIVACIÓN ======== //
void handleSleep() {
  if (alarmCount > 0) {
    sleepMode = true;
    wasSleeping = true;

    // Apagar pantalla
    display.clearDisplay();
    display.display();
    display.ssd1306_command(SSD1306_DISPLAYOFF);
    displayOn = false;

    // Desactivar WiFi
    WiFi.disconnect();
    server.close();

    // Apagar buzzer
    digitalWrite(BUZZER_PIN, LOW);

    lastUserAction = "Modo apagado activado";
    server.send(200, "text/plain", "OK");
    Serial.println("Sistema apagado");
  } else {
    server.send(403, "text/plain", "No hay alarmas programadas");
  }
}

void handleWake() {
  sleepMode = false;

  // Reactivar WiFi
  connectToWiFi();
  
  // Reiniciar servidor
  server.begin();

  // Reactivar pantalla
  display.ssd1306_command(SSD1306_DISPLAYON);
  displayOn = true;
  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  display.display();

  lastUserAction = "Sistema reactivado";
  server.send(200, "text/plain", "OK");
  Serial.println("Sistema reactivado");

  UPDATE_ACTIVITY();
}

// ========= setup / loop =========

void setup() {
  Serial.begin(115200);

  // Configurar velocidad del motor
  motor.setSpeed(10);  // 10 RPM aprox

  // Inicializar buzzer
  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  // Inicializar EEPROM con tamaño aumentado
  EEPROM.begin(EEPROM_SIZE);

  // Inicializar pantalla OLED
  Wire.begin(SDA_PIN, SCL_PIN);
  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    Serial.println("Error OLED");
  } else {
    display.clearDisplay();
    display.setTextSize(1);
    display.setTextColor(WHITE);
    display.setCursor(0,0);
    display.println("Iniciando...");
    display.display();
  }

  // Inicializar RTC
  rtcAvailable = rtc.begin();
  if (rtcAvailable) {
    Serial.println("RTC encontrado");
    if (rtc.lostPower()) {
      Serial.println("RTC ajustado a tiempo de compilacion");
      rtc.adjust(DateTime(F(__DATE__), F(__TIME__)));
    }
  } else {
    Serial.println("RTC no encontrado, usando tiempo interno");
    lastTimeUpdate = millis();
  }

  // Conectar a WiFi (modo cliente) - CAMBIO AQUÍ
  if (!connectToWiFi()) {
    // Si no se puede conectar, continuar en modo offline
    Serial.println("Modo offline - Las alarmas seguirán funcionando");
    lastUserAction = "Modo offline";
  }

  // Cargar configuración persistente
  loadFromEEPROM();

  // Ordenar alarmas al iniciar
  sortAlarms();

  // Probar el motor al inicio
  testStepper();

  // Configurar rutas del servidor
  server.on("/", HTTP_GET, handleRoot);
  server.on("/set-time", HTTP_POST, handleSetTime);
  server.on("/add-alarm", HTTP_POST, handleAddAlarm);
  server.on("/dispense", HTTP_GET, handleDispense);
  server.on("/snooze", HTTP_GET, handleSnooze);
  server.on("/remove-alarm", HTTP_GET, handleRemoveAlarm);
  server.on("/current-time", HTTP_GET, handleCurrentTime);
  server.on("/set-buzzer", HTTP_POST, handleSetBuzzer);
  server.on("/stop-buzzer", HTTP_GET, handleStopBuzzer);
  server.on("/infinite-buzzer", HTTP_GET, handleInfiniteBuzzer);
  server.on("/sleep", HTTP_GET, handleSleep);
  server.on("/wake", HTTP_GET, handleWake);
  server.on("/list", HTTP_GET, handleListAlarms);  // NUEVO: Endpoint para JSON

  server.begin();
  Serial.println("Servidor iniciado");

  // Mostrar info inicial
  display.clearDisplay();
  display.setCursor(0,0);
  if (WiFi.status() == WL_CONNECTED) {
    display.println("Conectado");
    display.print("IP: ");
    display.println(WiFi.localIP());
  } else {
    display.println("Modo offline");
    display.println("Alarmas activas");
  }
  display.display();

  // Inicializar tiempo de actividad
  lastActivityTime = millis();
}

void loop() {
  if (!sleepMode) {
    server.handleClient();
  }

  static unsigned long lastUpdate = 0;
  if (millis() - lastUpdate > 100) {
    updateTime();
    checkAlarms();
    processBuzzer();

    // Control de tiempo de pantalla
    if (displayOn && millis() - lastActivityTime > DISPLAY_TIMEOUT) {
      turnOffDisplay();
    }

    if (!sleepMode) {
      if (displayOn) {
        updateDisplay();
      }
      calculateNextAlarm();
    }

    if (eepromModified) {
      static unsigned long lastSave = 0;
      if (millis() - lastSave > 5000) {
        saveToEEPROM();
        lastSave = millis();
      }
    }

    lastUpdate = millis();
  }
}
