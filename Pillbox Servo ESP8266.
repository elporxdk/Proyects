/******************************************************
  Pillbox System - Versión Estable y Estilizada
  Con RTC, OLED, Servo, Buzzer y Modo Apagado
******************************************************/

#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <EEPROM.h>
#include <Servo.h>
#include <RTClib.h>

// Configuración OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1

// Pines I2C
#define SDA_PIN 4    // D2
#define SCL_PIN 5    // D1

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Configuración WiFi
const char* AP_SSID = "Pillbox";
const char* AP_PASS = "123456789";

// Pines hardware
#define SERVO_PIN 14    // D5
#define BUZZER_PIN 2    // D4

// Direcciones EEPROM
#define EEPROM_SIZE 512
#define EEPROM_ALARM_COUNT_ADDR 10
#define EEPROM_ALARMS_ADDR 20
#define EEPROM_BUZZER_DURATION_ADDR 200
#define EEPROM_INITIALIZED_ADDR 300

// Constantes servo
const unsigned long SERVO_MOVE_DURATION = 42;
const unsigned long SERVO_COOLDOWN = 1500;

// Constantes de tiempo pantalla
const unsigned long DISPLAY_TIMEOUT = 300000; // 5 minutos

ESP8266WebServer server(80);
Servo dispenserServo;
RTC_DS3231 rtc;

// Variable para estado del RTC
bool rtcAvailable = false;

// Estructuras de datos
struct SystemTime {
  uint8_t hours;
  uint8_t minutes;
  uint8_t seconds;
};

struct Alarm {
  uint8_t hour;
  uint8_t minute;
  bool triggered;
};

Alarm alarms[5];
uint8_t alarmCount = 0;
SystemTime currentTime = {0, 0, 0};
unsigned long lastTimeUpdate = 0;

// Estados del sistema
enum SystemState { IDLE, DISPENSING, BUZZING };
SystemState systemState = IDLE;

// Variables de control
bool isDispensing = false;
unsigned long dispenseStartTime = 0;
unsigned long lastDispenseTime = 0;
unsigned long buzzerStart = 0;
uint16_t buzzerDuration = 500;
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

// Función para formatear dígitos
String formatTwoDigits(uint8_t number) {
  return number < 10 ? "0" + String(number) : String(number);
}

// Función para ordenar alarmas
void sortAlarms() {
  for (int i = 0; i < alarmCount - 1; i++) {
    for (int j = i + 1; j < alarmCount; j++) {
      // Comparar horas y minutos
      if (alarms[i].hour > alarms[j].hour || 
          (alarms[i].hour == alarms[j].hour && alarms[i].minute > alarms[j].minute)) {
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
    lastDisplayUpdate = 0; // Forzar actualización
    lastActivityTime = millis(); // Reiniciar temporizador
  }
}

// Macro para actualizar actividad
#define UPDATE_ACTIVITY() { lastActivityTime = millis(); wakeUpDisplay(); }

void setup() {
  Serial.begin(115200);
  
  // Inicializar servo
  dispenserServo.attach(SERVO_PIN);
  dispenserServo.write(90);
  
  // Inicializar buzzer
  pinMode(BUZZER_PIN, OUTPUT);
  noTone(BUZZER_PIN);

  // Inicializar EEPROM
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

  // Configurar WiFi
  WiFi.softAP(AP_SSID, AP_PASS);
  Serial.print("IP: ");
  Serial.println(WiFi.softAPIP());

  // Cargar configuración
  loadFromEEPROM();

  // Ordenar alarmas al iniciar
  sortAlarms();

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
  
  server.begin();
  Serial.println("Servidor iniciado");
  
  // Mostrar info inicial
  display.clearDisplay();
  display.setCursor(0,0);
  display.println("Sistema listo");
  display.println(WiFi.softAPIP());
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
    processServo();
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

// ======== FUNCIONES DE APAGADO ======== //
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
    WiFi.softAPdisconnect(true);
    server.close();
    
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
  WiFi.softAP(AP_SSID, AP_PASS);
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

// ======== FUNCIONES EXISTENTES MODIFICADAS ======== //
void checkAlarms() {
  if (systemState != IDLE) return;

  for (uint8_t i = 0; i < alarmCount; i++) {
    if (!alarms[i].triggered &&
        alarms[i].hour == currentTime.hours &&
        alarms[i].minute == currentTime.minutes &&
        currentTime.seconds < 5) {
      
      alarms[i].triggered = true;
      
      // Reactivar sistema si está apagado
      if (sleepMode) {
        handleWake();
      }
      
      // Encender pantalla si está apagada
      wakeUpDisplay();
      
      triggerAlarm();
      break;
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
  uint8_t closestIndex = 255;

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t alarmMinutes = alarms[i].hour * 60 + alarms[i].minute;
    if (alarmMinutes > currentMinutes && alarmMinutes < closestTime) {
      closestTime = alarmMinutes;
      closestIndex = i;
    }
  }

  if (closestIndex != 255) {
    nextAlarm = formatTwoDigits(alarms[closestIndex].hour) + ":" +
                formatTwoDigits(alarms[closestIndex].minute);
  } else {
    nextAlarm = formatTwoDigits(alarms[0].hour) + ":" +
                formatTwoDigits(alarms[0].minute) + " (Sig)";
  }
}

void updateTime() {
  if (rtcAvailable) {
    DateTime now = rtc.now();
    currentTime.hours = now.hour();
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

void triggerAlarm() {
  if (millis() - lastDispenseTime < SERVO_COOLDOWN) return;
  
  systemState = DISPENSING;
  startServoDispense();
  activateBuzzer();
  
  lastUserAction = "Dispensado: " + 
    formatTwoDigits(currentTime.hours) + ":" +
    formatTwoDigits(currentTime.minutes) + ":" +
    formatTwoDigits(currentTime.seconds);
}

void startServoDispense() {
  isDispensing = true;
  dispenseStartTime = millis();
  dispenserServo.write(0);
}

void processServo() {
  if (!isDispensing) return;
  
  if (millis() - dispenseStartTime >= SERVO_MOVE_DURATION) {
    dispenserServo.write(90);
    isDispensing = false;
    lastDispenseTime = millis();
    
    if (systemState == DISPENSING) {
      systemState = BUZZING;
      if (infiniteModeEnabled) infiniteBuzzer = true;
    }
  }
}

void processBuzzer() {
  if (systemState != BUZZING) return;

  if (infiniteBuzzer) {
    tone(BUZZER_PIN, 1500);
    return;
  }

  static unsigned long buzzerEnd = 0;
  if (buzzerEnd == 0) {
    tone(BUZZER_PIN, 1500);
    buzzerEnd = millis() + buzzerDuration;
  } else if (millis() >= buzzerEnd) {
    noTone(BUZZER_PIN);
    systemState = IDLE;
    buzzerEnd = 0;
  }
}

void activateBuzzer() {
  buzzerStart = millis();
}

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
  display.println(nextAlarm);

  // Estado del sistema
  display.setCursor(0, 32);
  switch(systemState) {
    case DISPENSING: display.print("Dispensando"); break;
    case BUZZING: display.print("ALARMA!"); break;
    default: 
      if (sleepMode) display.print("APAGADO");
      else display.print("Sistema listo");
  }

  // Ultima accion
  display.setCursor(0, 48);
  display.print(lastUserAction.substring(0, 21));

  display.display();
}

void markEEPROMForUpdate() {
  eepromModified = true;
}

void saveToEEPROM() {
  if (!eepromModified) return;
  
  EEPROM.write(EEPROM_INITIALIZED_ADDR, 0xAA);
  EEPROM.write(EEPROM_ALARM_COUNT_ADDR, alarmCount);

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t addr = EEPROM_ALARMS_ADDR + (i * 3);
    EEPROM.write(addr, alarms[i].hour);
    EEPROM.write(addr + 1, alarms[i].minute);
    EEPROM.write(addr + 2, alarms[i].triggered ? 1 : 0);
  }

  EEPROM.write(EEPROM_BUZZER_DURATION_ADDR, buzzerDuration & 0xFF);
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
  if (alarmCount > 5) alarmCount = 0;

  for (uint8_t i = 0; i < alarmCount; i++) {
    uint16_t addr = EEPROM_ALARMS_ADDR + (i * 3);
    alarms[i].hour = EEPROM.read(addr);
    alarms[i].minute = EEPROM.read(addr + 1);
    alarms[i].triggered = (EEPROM.read(addr + 2) == 1);
  }

  buzzerDuration = EEPROM.read(EEPROM_BUZZER_DURATION_ADDR) |
                   (EEPROM.read(EEPROM_BUZZER_DURATION_ADDR + 1) << 8);
  if (buzzerDuration < 100 || buzzerDuration > 5000) buzzerDuration = 500;

  infiniteModeEnabled = (EEPROM.read(EEPROM_BUZZER_DURATION_ADDR + 2) == 1);
}

void handleRoot() {
  UPDATE_ACTIVITY();
  String html = R"rawliteral(
<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pillbox</title>
<style>
  body {
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    text-align: center;
    background-color: #000000;
    color: #FFFFFF;
    margin: 0;
    padding: 0;
    transition: all 0.3s ease;
  }
  body.light-mode {
    background-color: #FFFFFF;
    color: #333333;
  }
  .container {
    max-width: 600px;
    margin: 0 auto;
    padding: 20px;
  }
  .time {
    font-size: 2.5em;
    margin: 20px;
    color: #00FF80;
    text-shadow: 0 0 10px rgba(0, 255, 128, 0.3);
    transition: all 0.3s ease;
  }
  body.light-mode .time {
    color: #00B050;
    text-shadow: 0 0 5px rgba(0, 176, 80, 0.2);
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
    box-shadow: 0 2px 5px rgba(0,0,0,0.2);
  }
  button:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
  }
  input[type="time"], input[type="number"] {
    background: #1A1A1A;
    border: 2px solid #32CD32;
    color: #FFFFFF;
    padding: 8px 12px;
    border-radius: 6px;
    margin: 10px;
    transition: all 0.3s ease;
  }
  body.light-mode input[type="time"], body.light-mode input[type="number"] {
    background: #F8FFF8;
    border: 2px solid #228B22;
    color: #333333;
  }
  input[type="time"]:focus, input[type="number"]:focus {
    outline: none;
    border-color: #00FF7F;
    box-shadow: 0 0 5px rgba(0, 255, 127, 0.3);
  }
  .alarm-list {
    margin: 20px auto;
    padding: 20px;
    background: linear-gradient(145deg, #1A1A1A, #0F0F0F);
    border-radius: 12px;
    border: 1px solid #333333;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.3);
    transition: all 0.3s ease;
  }
  body.light-mode .alarm-list {
    background: linear-gradient(145deg, #F5F5F5, #EEEEEE);
    border: 1px solid #DDDDDD;
    box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
  }
  .alarm-list h3, .alarm-list h4 {
    color: #00FF7F;
    margin-bottom: 15px;
    transition: all 0.3s ease;
  }
  body.light-mode .alarm-list h3, body.light-mode .alarm-list h4 {
    color: #228B22;
  }
  .footer {
    margin-top: 30px;
    color: #888888;
    font-size: 0.8em;
    padding: 20px;
    border-top: 1px solid #333333;
  }
  .cooldown {
    color: #FF6B6B;
    margin: 10px;
    font-weight: 500;
  }
  .dispense-button {
    background: linear-gradient(145deg, #00FF7F, #00E600) !important;
    font-size: 1em !important;
    padding: 10px 20px !important;
    border-radius: 8px;
    margin: 10px 5px;
    min-width: 140px;
  }
  .dispense-button:hover {
    background: linear-gradient(145deg, #00E600, #00CC00) !important;
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
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
  }
  .alarm-item {
    background: #2A2A2A;
    padding: 10px;
    margin: 8px 0;
    border-radius: 6px;
    border-left: 4px solid #32CD32;
    transition: all 0.3s ease;
  }
  body.light-mode .alarm-item {
    background: #F0FFF0;
    color: #333333;
    border-left: 4px solid #228B22;
  }
  .remove-link {
    color: #FF6B6B;
    text-decoration: none;
    margin-left: 15px;
    padding: 4px 8px;
    border-radius: 4px;
    transition: background 0.3s ease;
  }
  .remove-link:hover {
    background: rgba(255, 107, 107, 0.1);
  }
  h1 {
    color: #00FF7F;
    font-size: 2.2em;
    margin-bottom: 10px;
    text-shadow: 0 0 15px rgba(0, 255, 127, 0.2);
    transition: all 0.3s ease;
  }
  body.light-mode h1 {
    color: #228B22;
    text-shadow: 0 0 10px rgba(34, 139, 34, 0.1);
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
    accent-color: #32CD32;
    margin-right: 8px;
  }
  .theme-toggle {
    position: fixed;
    bottom: 20px;
    right: 20px;
    background: linear-gradient(145deg, #3CB371, #2E8B57);
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
    background: linear-gradient(145deg, #2E8B57, #228B22);
    transform: scale(1.05);
    box-shadow: 0 5px 12px rgba(60, 179, 113, 0.4);
  }
  body.light-mode .theme-toggle {
    background: linear-gradient(145deg, #228B22, #006400);
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
  
  // Cargar tema guardado al iniciar
  window.onload = function() {
    const savedTheme = localStorage.getItem('theme');
    const themeBtn = document.getElementById('theme-btn');
    
    if (savedTheme === 'light') {
      document.body.classList.add('light-mode');
      themeBtn.textContent = 'Modo Oscuro';
    } else {
      themeBtn.textContent = 'Modo Claro';
    }
  };
  
  function toggleTheme() {
    const body = document.body;
    const themeBtn = document.getElementById('theme-btn');
    const isLightMode = body.classList.contains('light-mode');
    
    if (isLightMode) {
      body.classList.remove('light-mode');
      themeBtn.textContent = 'Modo Claro';
      localStorage.setItem('theme', 'dark');
    } else {
      body.classList.add('light-mode');
      themeBtn.textContent = 'Modo Oscuro';
      localStorage.setItem('theme', 'light');
    }
  }
</script>
</head>
<body>
  <button class="theme-toggle" id="theme-btn" onclick="toggleTheme()">Modo Claro</button>
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
      <h3>Configuracion</h3>
      <form action="/set-time" method="post">
        <input type="time" name="time" required step="1">
        <button type="submit" style="background: linear-gradient(145deg, #28C76F, #20A85A);">Establecer Hora</button>
      </form>

      <h4>Programar Alarma</h4>
      <form action="/add-alarm" method="post">
        <input type="time" name="alarm" required>
        <button type="submit" style="background: linear-gradient(145deg, #28C76F, #20A85A);">Agregar Alarma</button>
      </form>

      <h4>Alarmas Activas</h4>)rawliteral";

  // Generar lista de alarmas
  for (uint8_t i = 0; i < alarmCount; i++) {
    html += "<div class='alarm-item'>";
    html += formatTwoDigits(alarms[i].hour) + ":" + formatTwoDigits(alarms[i].minute);
    html += " <a href='/remove-alarm?id=" + String(i) + "' class='remove-link'>Eliminar</a></div>";
  }

  html += R"rawliteral(
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
        <button type="submit" style="background: linear-gradient(145deg, #28C76F, #20A85A);">Guardar Configuracion</button>
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
  
  // Determinar estado del botón de apagado
  if (alarmCount == 0) {
    html += " disabled style='background: linear-gradient(145deg, #FF6B6B, #FF5555);'";
  } else {
    html += " style='background: linear-gradient(145deg, #28C76F, #20A85A);'";
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
        if (confirm("Apagar sistema.Se reactivara automaticamente con la proxima alarma.")) {
          const sleepBtn = document.getElementById('sleepBtn');
          sleepBtn.disabled = true;
          sleepBtn.innerHTML = 'Apagando...';
          sleepBtn.style.background = 'linear-gradient(145deg, #718096, #4A5568)';
          
          fetch('/sleep')
            .then(response => {
              if (response.ok) {
                sleepBtn.innerHTML = 'Apagado';
              } else {
                sleepBtn.innerHTML = 'Error';
                setTimeout(() => {
                  sleepBtn.innerHTML = 'Apagar Sistema';
                  sleepBtn.disabled = false;
                  sleepBtn.style.background = 'linear-gradient(145deg, #28C76F, #20A85A)';
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

void handleSetTime() {
  UPDATE_ACTIVITY();
  if (server.method() == HTTP_POST) {
    String timeParam = server.arg("time");
    uint8_t newHours = timeParam.substring(0, 2).toInt();
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
    // Verificar límite de alarmas
    if (alarmCount >= 5) {
      lastUserAction = "Error: Max 5 alarmas";
      server.sendHeader("Location", "/");
      server.send(303);
      return;
    }
    
    String alarmTime = server.arg("alarm");
    uint8_t alarmHour = alarmTime.substring(0, 2).toInt();
    uint8_t alarmMinute = alarmTime.substring(3, 5).toInt();

    // Verificar si la alarma ya existe
    if (alarmExists(alarmHour, alarmMinute)) {
      lastUserAction = "Error: Alarma duplicada";
      server.sendHeader("Location", "/");
      server.send(303);
      return;
    }

    if (alarmHour < 24 && alarmMinute < 60) {
      alarms[alarmCount] = { alarmHour, alarmMinute, false };
      alarmCount++;
      
      // Ordenar alarmas después de agregar
      sortAlarms();
      
      lastUserAction = "Alarma agregada";
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
      for (uint8_t i = id; i < alarmCount - 1; i++) {
        alarms[i] = alarms[i + 1];
      }
      alarmCount--;
      
      // Ordenar alarmas después de eliminar
      sortAlarms();
      
      lastUserAction = "Alarma eliminada";
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
