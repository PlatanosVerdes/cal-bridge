#pragma once

// WiFi
#define WIFI_SSID  "tu_wifi"
#define WIFI_PASS  "tu_contraseña"

// cal-bridge — IP local del Pi (el ESP32 está en WiFi, no en Tailscale)
// Puerto 8091 expuesto por Docker. Sin TLS, sin DNS especial.
#define CAL_HOST  "http://192.168.1.xxx:8091"
#define CAL_KEY   "tu_api_key"

// Intervalo de refresco automático (ms)
#define REFRESH_MS  900000  // 15 minutos

// Pines de botones (ajusta según tu cableado)
#define PIN_BTN_A  12  // siguiente escena / long press = refresco forzado
#define PIN_BTN_B  14  // escena anterior

#define LONG_PRESS_MS  800  // ms para considerar pulsación larga

// Escenas: cada entrada es { sufijo de URL, etiqueta en pantalla }
// Añade o quita escenas aquí sin tocar el resto del código.
struct Scene {
    const char* path;
    const char* label;
};

const Scene SCENES[] = {
    { "/events?account=personal&ms_account=work&days=7", "All · 7 days"   },
    { "/today?account=personal&ms_account=work",         "Today · All"    },
    { "/events?account=personal&days=7",                 "Personal · 7d"  },
    { "/events?ms_account=work&days=7",                  "Work · 7d"      },
};
const int NUM_SCENES = sizeof(SCENES) / sizeof(SCENES[0]);
