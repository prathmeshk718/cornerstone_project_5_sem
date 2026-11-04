/*
 * Project: Development of a Machine Learning Framework for Predictive
 *          Condition Monitoring and Maintenance Analytics
 *
 * Hardware: Arduino Uno (ATmega328P)
 * Sensors:
 *   1. DS18B20 Waterproof/Probe Temperature Sensor -> Digital Pin D2 (4.7k pull-up to 5V)
 *   2. DHT11 Temperature & Relative Humidity Sensor -> Digital Pin D3
 *   3. IR Flame Sensor Module (4-pin LM393/KY-026)  -> Digital DO on D4, Analog AO on A0
 *
 * Required Arduino Libraries (install via Library Manager):
 *   - OneWire by Paul Stoffregen
 *   - DallasTemperature by Miles Burton
 *   - DHT sensor library by Adafruit (+ Adafruit Unified Sensor)
 *
 * Serial Protocol:
 *   115200 baud, 8N1, newline-delimited JSON emitted every 1000 ms.
 *   Each sensor carries an explicit "ok" boolean so missing/faulty hardware
 *   is never confused with a genuine 0 measurement.
 */

#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>

// ---------------------- Configurable Pin Definitions ----------------------
#define DS18B20_PIN         2     // OneWire bus pin (D2)
#define DHT11_PIN           3     // DHT11 data pin (D3)
#define FLAME_DO_PIN        4     // Flame digital comparator output (D4)
#define FLAME_AO_PIN        A0    // Flame analog IR intensity pin (A0)

// ---------------------- Hardware Behaviour Settings -----------------------
#define SERIAL_BAUD_RATE    115200
#define PUBLISH_INTERVAL_MS 1000UL   // Emit 1 JSON frame per second (heartbeat + data)
#define DHT_READ_PERIOD_MS  1500UL   // DHT11 minimum safe sampling period (>=1s)
#define FLAME_ACTIVE_LOW    true     // Most LM393/KY-026 flame modules pull DO LOW on flame

// Optional open-circuit check on A0: if module GND is lost and A0 pins hard at 1023
// while DO stays HIGH continuously, keep configurable tolerance:
#define FLAME_FAULT_ADC_MIN 2        // Short-to-GND fault check when DO does not match

OneWire oneWire(DS18B20_PIN);
DallasTemperature ds18b20(&oneWire);
DHT dht(DHT11_PIN, DHT11);

// Timing and sequence state
unsigned long lastPublishMs = 0;
unsigned long lastDhtReadMs = 0;
unsigned long frameSeq = 0;
bool dsConversionPending = false;
bool firstDsRead = true;

// Cached DHT11 state between 1.5s hardware polls
bool dhtOk = false;
float dhtTempC = 0.0f;
float dhtHumidityPct = 0.0f;

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);
  while (!Serial) {
    ; // Wait for USB serial connection if native USB
  }

  pinMode(FLAME_DO_PIN, INPUT_PULLUP);
  pinMode(FLAME_AO_PIN, INPUT);

  // Initialize DS18B20 in non-blocking 12-bit mode
  ds18b20.begin();
  ds18b20.setResolution(12);
  ds18b20.setWaitForConversion(false);
  ds18b20.requestTemperatures();
  dsConversionPending = true;

  // Initialize DHT11
  dht.begin();
}

void readDhtIfDue(unsigned long nowMs) {
  if (nowMs - lastDhtReadMs < DHT_READ_PERIOD_MS) {
    return;
  }
  lastDhtReadMs = nowMs;

  float h = dht.readHumidity();
  float t = dht.readTemperature(); // Celsius

  if (isnan(h) || isnan(t) || h < 0.0f || h > 100.0f || t < -20.0f || t > 80.0f) {
    dhtOk = false;
  } else {
    dhtOk = true;
    dhtTempC = t;
    dhtHumidityPct = h;
  }
}

void loop() {
  unsigned long nowMs = millis();

  // Poll DHT11 at its safe hardware period
  readDhtIfDue(nowMs);

  if (nowMs - lastPublishMs < PUBLISH_INTERVAL_MS) {
    return;
  }
  lastPublishMs = nowMs;
  frameSeq++;

  // 1. Read DS18B20 Temperature (independent from DHT11)
  bool dsOk = false;
  float dsTempC = 0.0f;
  if (dsConversionPending) {
    float rawDs = ds18b20.getTempCByIndex(0);
    // DEVICE_DISCONNECTED_C is -127.0f; 85.0f on very first power-on read indicates uninitialized scratchpad
    if (rawDs != DEVICE_DISCONNECTED_C && rawDs > -55.0f && rawDs < 125.0f) {
      if (!(firstDsRead && fabs(rawDs - 85.0f) < 0.01f)) {
        dsOk = true;
        dsTempC = rawDs;
        firstDsRead = false;
      }
    }
  }
  // Trigger next asynchronous conversion for the next 1s cycle
  ds18b20.requestTemperatures();
  dsConversionPending = true;

  // 2. Read Flame Sensor (Digital DO + Analog AO)
  int rawDo = digitalRead(FLAME_DO_PIN);
  int rawAo = analogRead(FLAME_AO_PIN); // 0..1023 (lower value = stronger IR radiation)

  int flameDetected = FLAME_ACTIVE_LOW ? (rawDo == LOW ? 1 : 0) : (rawDo == HIGH ? 1 : 0);
  // Convert 10-bit ADC (1023 = dark, 0 = intense IR) to 0..100% intensity
  float flameIntensityPct = ((1023.0f - (float)rawAo) / 1023.0f) * 100.0f;
  if (flameIntensityPct < 0.0f) flameIntensityPct = 0.0f;
  if (flameIntensityPct > 100.0f) flameIntensityPct = 100.0f;

  // Detect obvious hardware wiring fault (e.g., analog shorted to 0V while comparator claims no flame)
  bool flameOk = true;
  if (rawAo < FLAME_FAULT_ADC_MIN && flameDetected == 0) {
    flameOk = false;
  }

  // 3. Emit single-line JSON packet over USB Serial
  Serial.print(F("{\"seq\":"));
  Serial.print(frameSeq);
  Serial.print(F(",\"uptime_ms\":"));
  Serial.print(nowMs);

  // DS18B20 block
  Serial.print(F(",\"ds18b20\":{\"ok\":"));
  Serial.print(dsOk ? F("true") : F("false"));
  Serial.print(F(",\"temp_c\":"));
  if (dsOk) {
    Serial.print(dsTempC, 2);
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F("}"));

  // DHT11 block
  Serial.print(F(",\"dht11\":{\"ok\":"));
  Serial.print(dhtOk ? F("true") : F("false"));
  Serial.print(F(",\"temp_c\":"));
  if (dhtOk) {
    Serial.print(dhtTempC, 1);
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"humidity_pct\":"));
  if (dhtOk) {
    Serial.print(dhtHumidityPct, 1);
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F("}"));

  // Flame sensor block
  Serial.print(F(",\"flame\":{\"ok\":"));
  Serial.print(flameOk ? F("true") : F("false"));
  Serial.print(F(",\"detected\":"));
  if (flameOk) {
    Serial.print(flameDetected);
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"analog_raw\":"));
  if (flameOk) {
    Serial.print(rawAo);
  } else {
    Serial.print(F("null"));
  }
  Serial.print(F(",\"intensity_pct\":"));
  if (flameOk) {
    Serial.print(flameIntensityPct, 1);
  } else {
    Serial.print(F("null"));
  }
  Serial.println(F("}}"));
}
