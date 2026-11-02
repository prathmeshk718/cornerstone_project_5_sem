/*
 * ============================================================================
 * Project: Development of a Machine Learning Framework for Predictive
 *          Condition Monitoring and Maintenance Analytics
 * Module:  Arduino Uno Multi-Sensor Acquisition Firmware
 * Target:  Arduino Uno (ATmega328P) -> USB Serial -> ROS 2 Humble (Ubuntu 22.04)
 * ============================================================================
 *
 * Connected Sensors (Independent Measurements):
 *   1. DS18B20 Digital Temperature Sensor  -> Equipment Temperature (°C)
 *   2. DHT11 Temperature & Humidity Sensor -> Environmental Temp (°C) & RH (%)
 *   3. YG1006 NPN Silicon Phototransistor  -> Infrared / Flame Detection Status
 *   4. Analog Smoke Sensor                 -> Raw Analog Reading (0 - 1023 ADC)
 *
 * Required Arduino Libraries (Install via Arduino Library Manager):
 *   - OneWire (by Jim Studt / Paul Stoffregen)
 *   - DallasTemperature (by Miles Burton)
 *   - DHT sensor library (by Adafruit)
 *   - Adafruit Unified Sensor
 * ============================================================================
 */

#include <OneWire.h>
#include <DallasTemperature.h>
#include <DHT.h>

// ============================================================================
// [CUSTOMIZABLE HARDWARE PIN & SENSOR CONFIGURATION]
// Modify the pin definitions below to match your exact physical wiring.
// ============================================================================

#define PIN_DS18B20          2       // Digital Pin D2 (Requires 4.7kOhm pull-up to 5V)
#define PIN_DHT11            3       // Digital Pin D3 (DHT11 Single-Wire Data)
#define PIN_YG1006_DIGITAL   4       // Digital Pin D4 (YG1006 Comparator DO pin)
#define PIN_YG1006_ANALOG    A1      // Analog Pin A1  (Optional YG1006 raw IR AO pin)
#define PIN_SMOKE_ANALOG     A0      // Analog Pin A0  (Smoke Sensor Analog Output)

#define DHT_TYPE             DHT11   // DHT11 Sensor Type
#define YG1006_ACTIVE_LOW    true    // Most YG1006 modules output LOW (0) when flame is detected
#define SMOKE_SENSOR_MODEL   "Configurable MQ-Series (e.g., MQ-2 / MQ-135)" // Set to your specific model (e.g., "MQ-2", "MQ-135")

#define SERIAL_BAUD_RATE     115200  // USB Serial communication rate for ROS 2 node
#define SAMPLE_INTERVAL_MS   1000    // Sampling period in milliseconds (1 Hz default)

// ============================================================================
// Sensor Driver Instances
// ============================================================================
OneWire oneWire(PIN_DS18B20);
DallasTemperature ds18b20(&oneWire);
DHT dht11(PIN_DHT11, DHT_TYPE);

// State Tracking
unsigned long lastSampleTimeMs = 0;
unsigned long packetSeq = 0;
float lastValidDs18b20Temp = 25.0;
float lastValidDhtTemp = 25.0;
float lastValidDhtHumidity = 50.0;

void setup() {
  Serial.begin(SERIAL_BAUD_RATE);
  while (!Serial) {
    ; // Wait for USB serial port connection
  }

  pinMode(PIN_YG1006_DIGITAL, INPUT);
  pinMode(PIN_YG1006_ANALOG, INPUT);
  pinMode(PIN_SMOKE_ANALOG, INPUT);

  ds18b20.begin();
  dht11.begin();

  // Emit startup banner comment (ignored by ROS 2 JSON parser if prefixed with #)
  Serial.println(F("#INFO:Arduino Uno Predictive Condition Monitoring Node Initialized"));
}

void loop() {
  unsigned long nowMs = millis();

  if (nowMs - lastSampleTimeMs >= SAMPLE_INTERVAL_MS) {
    lastSampleTimeMs = nowMs;
    packetSeq++;

    // ------------------------------------------------------------------------
    // 1. Read DS18B20 Equipment Temperature Sensor (Digital OneWire)
    // ------------------------------------------------------------------------
    ds18b20.requestTemperatures();
    float equipTempC = ds18b20.getTempCByIndex(0);
    bool ds18b20Valid = (equipTempC != DEVICE_DISCONNECTED_C && equipTempC > -55.0 && equipTempC < 125.0);
    if (ds18b20Valid) {
      lastValidDs18b20Temp = equipTempC;
    } else {
      equipTempC = lastValidDs18b20Temp;
    }

    // ------------------------------------------------------------------------
    // 2. Read DHT11 Environmental Temperature & Relative Humidity (Digital)
    // ------------------------------------------------------------------------
    float envHumidityPct = dht11.readHumidity();
    float envTempC = dht11.readTemperature();
    bool dhtValid = (!isnan(envHumidityPct) && !isnan(envTempC));
    if (dhtValid) {
      lastValidDhtTemp = envTempC;
      lastValidDhtHumidity = envHumidityPct;
    } else {
      envTempC = lastValidDhtTemp;
      envHumidityPct = lastValidDhtHumidity;
    }

    // ------------------------------------------------------------------------
    // 3. Read YG1006 NPN Silicon Phototransistor (Infrared / Flame Status)
    // ------------------------------------------------------------------------
    int ygDigitalRaw = digitalRead(PIN_YG1006_DIGITAL);
    int ygAnalogRaw  = analogRead(PIN_YG1006_ANALOG);
    int flameDetected = 0;
    if (YG1006_ACTIVE_LOW) {
      flameDetected = (ygDigitalRaw == LOW) ? 1 : 0;
    } else {
      flameDetected = (ygDigitalRaw == HIGH) ? 1 : 0;
    }

    // ------------------------------------------------------------------------
    // 4. Read Smoke Sensor (Raw 10-bit Analog ADC: 0 - 1023)
    // ------------------------------------------------------------------------
    int smokeRawAdc = analogRead(PIN_SMOKE_ANALOG);

    // ------------------------------------------------------------------------
    // 5. Transmit Structured JSON Packet over USB Serial to ROS 2 Humble
    // ------------------------------------------------------------------------
    Serial.print(F("{\"seq\":"));
    Serial.print(packetSeq);
    Serial.print(F(",\"ts_ms\":"));
    Serial.print(nowMs);
    Serial.print(F(",\"ds18b20_temp_c\":"));
    Serial.print(equipTempC, 2);
    Serial.print(F(",\"dht11_temp_c\":"));
    Serial.print(envTempC, 2);
    Serial.print(F(",\"dht11_humidity_pct\":"));
    Serial.print(envHumidityPct, 1);
    Serial.print(F(",\"yg1006_flame\":"));
    Serial.print(flameDetected);
    Serial.print(F(",\"yg1006_ir_raw\":"));
    Serial.print(ygAnalogRaw);
    Serial.print(F(",\"smoke_raw_adc\":"));
    Serial.print(smokeRawAdc);
    Serial.print(F(",\"sensor_valid\":"));
    Serial.print((ds18b20Valid && dhtValid) ? 1 : 0);
    Serial.println(F("}"));
  }
}
