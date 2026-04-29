#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include "config.h"

// ── Display ───────────────────────────────────────────────────────────────────
// Sustituye este bloque por la librería de tu display concreto.
// Opciones habituales:
//   E-ink Waveshare: GxEPD2  →  https://github.com/ZinggJM/GxEPD2
//   TFT color:       TFT_eSPI →  https://github.com/Bodmer/TFT_eSPI
//   OLED 128x64:     Adafruit SSD1306
//
// La función drawEvents() recibe el array de eventos y la etiqueta de escena.
// Impleméntala según tu hardware.

void displayInit() {
    // TODO: inicializar display
    Serial.begin(115200);
}

void drawEvents(JsonArray events, const char* sceneLabel) {
    // TODO: renderizar en el display real
    // Por ahora vuelca por Serial para depurar
    Serial.printf("\n=== %s (%d eventos) ===\n", sceneLabel, events.size());
    for (JsonObject e : events) {
        const char* title    = e["title"]    | "(sin título)";
        const char* start    = e["start"]    | "?";
        const char* calendar = e["calendar_name"] | "";
        const char* account  = e["account"]  | "";
        Serial.printf("  [%s/%s] %s  →  %s\n", account, calendar, start, title);
    }
}

// ── Estado ────────────────────────────────────────────────────────────────────

int           currentScene = 0;
unsigned long lastRefresh  = 0;

// ── Red + fetch ───────────────────────────────────────────────────────────────

void wifiConnect() {
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    Serial.print("Conectando WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.printf("\nWiFi OK: %s\n", WiFi.localIP().toString().c_str());
}

void fetchAndDraw() {
    if (WiFi.status() != WL_CONNECTED) wifiConnect();

    String url = String(CAL_HOST)
               + SCENES[currentScene].path
               + "&key=" + CAL_KEY;

    Serial.printf("Fetching: %s\n", url.c_str());

    HTTPClient http;
    http.begin(url);
    http.setTimeout(10000);
    int code = http.GET();

    if (code == 200) {
        DynamicJsonDocument doc(32768);
        DeserializationError err = deserializeJson(doc, http.getString());
        if (!err) {
            drawEvents(doc["events"].as<JsonArray>(), SCENES[currentScene].label);
        } else {
            Serial.printf("JSON error: %s\n", err.c_str());
        }
    } else {
        Serial.printf("HTTP error: %d\n", code);
    }

    http.end();
    lastRefresh = millis();
}

// ── Botones ───────────────────────────────────────────────────────────────────

// Devuelve la duración de la pulsación en ms (0 si no hay pulsación)
unsigned long pressDuration(int pin) {
    if (digitalRead(pin) != LOW) return 0;
    unsigned long t = millis();
    while (digitalRead(pin) == LOW) delay(10);
    return millis() - t;
}

void handleButtons() {
    unsigned long durA = pressDuration(PIN_BTN_A);
    if (durA > 0) {
        if (durA >= LONG_PRESS_MS) {
            Serial.println("Refresco forzado");
        } else {
            currentScene = (currentScene + 1) % NUM_SCENES;
            Serial.printf("Escena → %s\n", SCENES[currentScene].label);
        }
        fetchAndDraw();
        return;
    }

    unsigned long durB = pressDuration(PIN_BTN_B);
    if (durB > 0) {
        currentScene = (currentScene - 1 + NUM_SCENES) % NUM_SCENES;
        Serial.printf("Escena → %s\n", SCENES[currentScene].label);
        fetchAndDraw();
    }
}

// ── Setup / Loop ──────────────────────────────────────────────────────────────

void setup() {
    displayInit();
    pinMode(PIN_BTN_A, INPUT_PULLUP);
    pinMode(PIN_BTN_B, INPUT_PULLUP);
    wifiConnect();
    fetchAndDraw();
}

void loop() {
    if (millis() - lastRefresh > REFRESH_MS) fetchAndDraw();
    handleButtons();
    delay(50);
}
