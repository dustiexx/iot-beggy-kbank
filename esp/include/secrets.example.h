// Copy this file to secrets.h and fill it in. secrets.h is git-ignored.
#pragma once

#define WIFI_SSID     "your-wifi-name"
#define WIFI_PASSWORD "your-wifi-password"

// Optional extra networks, e.g. a phone hotspot (2.4 GHz, WPA2; on iPhone turn on
// "Maximize Compatibility"). The board joins whichever known network is in range.
// #define WIFI_SSID_2     "my-phone-hotspot"
// #define WIFI_PASSWORD_2 "hotspot-password"
// #define WIFI_SSID_3     ""
// #define WIFI_PASSWORD_3 ""

// AWS IoT Core endpoint, from the backend person
// (AWS console > IoT Core > Settings > Device data endpoint).
#define AWS_IOT_ENDPOINT "xxxxxxxxxxxxxx-ats.iot.ap-southeast-1.amazonaws.com"
