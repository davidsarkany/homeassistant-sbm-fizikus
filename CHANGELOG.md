# Changelog

## 0.1.0 (2026-11-04)

The first release.

- Set up from the UI with the SBM username and password. Only a hash of the password is stored. When SBM refuses the login, Home Assistant asks for the password again.
- Every apartment of the account can be set up, each as its own entry. When the account has more than one apartment that is not set up yet, the setup asks which one. A new password entered for one apartment updates the other apartments of the account too.
- A climate entity for every thermostat: measured temperature and humidity, target temperature, heating, cooling or off (read only: heating or cooling is set centrally for the building), and whether the thermostat is active.
- A switch on the device of the apartment switches the SBM Fizikus system on and off, like the app. While the system is off, the climate entities show off and stay available.
- Sensors for every thermostat: humidity (enabled by default), dew point, temperature, target temperature and system mode (disabled by default).
- Outdoor temperature and humidity of the building, on the device of the apartment (disabled by default). SBM is asked for them only while one of them is enabled.
- Thermostats added to the apartment later show up at the next poll, without a reload.
- The device of the apartment shows the firmware version that SBM reports.
- Diagnostics, with the username and the password hash removed.
- English and Hungarian translations.
- Requires Home Assistant 2026.11 or newer.

Compared with the [MQTT bridge](https://github.com/davidsarkany/sbm-fizikus-to-mqtt):

- The entity IDs are the same, so automations, dashboards and history carry over. See [Switching from the MQTT bridge](README.en.md#switching-from-the-mqtt-bridge).
- Target temperatures are accepted with 0.1 °C resolution. The bridge accepts only multiples of 0.5 °C.
- New: a dew point sensor. The humidity sensor is enabled by default.
- The outdoor sensors are polled only while one of them is enabled. The bridge polls the outdoor weather when it is turned on in its settings.
- Every apartment of the account can be set up. The bridge uses only the first one.
- Not available: the system mode of the SBM Fizikus device.
