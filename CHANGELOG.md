# Changelog

## Unreleased

- Fixed: room names changed in SBM update at the next poll without a reload. Custom names in Home Assistant and existing entity IDs are preserved; a missing room name keeps the last known name.
- Fixed: a login response with a missing or invalid token is treated as an API failure instead of invalid credentials. Home Assistant retries automatically instead of asking for the password again.
- Fixed: building API responses are also redacted in error messages, so building addresses stay out of logs when a request fails or returns invalid JSON.

## 0.2.0 (2026-11-07)

- Fixed: a wrong password or username is recognized. SBM refuses a login with status 500, which the integration took for an outage: the setup said it could not connect, and after a password change the entities stayed unavailable without asking for the new password. Now the setup says the username or password is invalid, and Home Assistant asks for the password again. Other failures of SBM still count as an outage.
- Fixed: the dew point is rounded to 0.1 °C (`13.0 °C`, not `13.04477696992492 °C`). SBM sends it with full precision. The values recorded before stay as they are.
- The device of the apartment shows a new firmware version at the next poll, without a reload or restart.
- New: a system switched off for the whole building shows as off. The climate entities and the system mode show `off`, and the System switch is unavailable, like in the SBM Fizikus app. The integration checks it every 10 minutes.
- New: forward and return water temperature and flow rate sensors on the device of the apartment (disabled by default), the values of the "Statisztika" page of the SBM Fizikus app.
- New: a child lock switch for every thermostat (a configuration switch, disabled by default) locks its buttons, like the room card of the SBM Fizikus app.
- When Home Assistant asks for the password again, the username can be changed too, for example after a new email address in the SBM Fizikus app. The account must have access to the apartment. The other apartments of the account get the new login too.

## 0.1.3 (2026-11-07)

- Changed: the entities no longer become unavailable when SBM reports the relay module as disconnected. Like the SBM Fizikus app, the integration now uses only the connection of the thermostats, so a real failure of the relay module does not show in Home Assistant.
- The log says when SBM reports the thermostats as disconnected, and when they are back.

## 0.1.2 (2026-11-05)

- More detailed debug logs: every request to SBM with its answer, status and duration, the parsed state, and the cause of every failed call. The username, the password hash, the token and the address of the building stay out of the log.
- The log now also says, without debug logging, when SBM cannot be reached at startup, when SBM can be reached again, when the relay module and the thermostats are back, and why SBM could not be reached while adding the integration or during a reauthentication.

## 0.1.1 (2026-11-04)

- Fixed: the humidity of the thermostats is shown as a whole number, like SBM reports it (`62 %`, not `62.0 %`), on the humidity sensor and the climate entity. The recorder history is not affected.

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
