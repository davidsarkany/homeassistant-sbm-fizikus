# SBM Fizikus Home Assistant integration: design

This document describes how the integration works and why: the SBM API it uses, the behaviour of its entities, and the decisions behind them, with the alternatives that were not chosen. It describes version 0.3.0.

The reference implementation of the SBM API is the [sbm-fizikus-to-mqtt](https://github.com/davidsarkany/sbm-fizikus-to-mqtt) bridge. Paths starting with `src/` refer to that repository.

## Purpose

Home Assistant users can control their SBM Fizikus thermostats without running the MQTT bridge and an MQTT broker. The integration talks to the SBM cloud directly, is set up from the Home Assistant UI with the SBM username and password, and exposes each thermostat as a climate entity.

The MQTT bridge stays as it is. The integration is a second, independent way to use SBM, and users of the bridge can switch to it without losing their entity IDs, automations or history.

## Scope

Supported:

- Each thermostat's measured temperature, humidity, dew point and target temperature, and whether it calls for heating or cooling.
- Each thermostat's condensation risk, the notice on the room card of the app.
- Setting a thermostat's target temperature.
- Switching the SBM Fizikus system of an apartment on and off.
- The outdoor temperature and humidity of the building, fetched only while an outdoor sensor is enabled.
- Setup and reauthentication from the UI; every apartment of the account, one config entry per apartment.
- The same entity IDs as the MQTT bridge.

Not supported:

- Meters, energy and volume counters from `get_apartment_data`, and the second water channel (`…2…`), which SBM reports as not connected (`-1`).
- The building-level child lock (`childLock` of `get_building_data`) and the manual cooling disable (`coolingDisabledManually`).
- Changing heating or cooling, which is set centrally for the building.
- SBM's refresh token (see [Open points](#open-points)).
- Automatic migration of the bridge's MQTT entities.

Never:

- Storing the plain password.
- Reading an unknown `operationMode` as `off`: an unknown value is unknown. `off` is shown only while SBM reports the system as switched off (`disabled`).
- Entity IDs that differ from the bridge's for the same entity.

## SBM API

All calls are `PUT https://ps9et7hsp3.execute-api.eu-central-1.amazonaws.com/frontend` with the JSON body `{"operation": "<name>", "payload": {...}}`. The token goes into the payload as `jwt_token`. The bridge's client is in `src/SbmFizikusToMqtt.Infrastructure/Sbm/`, and its anonymized fixtures, copied into `tests/fixtures/`, are in `src/SbmFizikusToMqtt.Infrastructure.Tests/TestData/sbm/`.

| Operation | Payload | Answer used | When |
|---|---|---|---|
| `get_jw_token` | `username_or_email`, `pwhash` | `token`, `expiration` | setup, reauthentication, token expiry, after 401/403 |
| `get_building_access_rights` | `jwt_token` | positional `[[buildingId, country, postalCode, city, street, outdoorTemp, outdoorHum, name], ...]`: `buildingId`, `outdoorTemp`, `outdoorHum` | setup; once per load to find the building of the apartment; every poll while an outdoor sensor is enabled |
| `get_apartment_access_rights` | `building_id` (**string**) | positional `[[buildingId, apartmentId, name, isOnline], ...]`: `apartmentId`, `name` | setup and reauthentication, for every building; once per load to find the building of the apartment |
| `get_building_data` | `building_id` (int) | `data.disabled` | every 10 minutes |
| `get_apartment_data` | `apartment_id` (int) | `operationMode`, `communicationActiveThermostats`, `disabled`, `disabledCommand`, `fwVer`, `thermostats[]` | every poll |
| `update_thermostat_settings` | `thermostat_id` (int), `temp_setpoint_degC` (number) | `thermostat_id` | set temperature |
| `update_apartment_settings` | `apartment_id` (**string**), `disabled` (bool) | `apartment_id` (string), next to `message`: `Apartment settings updated successfully.` | switch the system on or off |

Per thermostat: `ID`, `name` (may be null), `measuredTempDegC`, `measuredHumPerc`, `dewPointDegC`, `temperatureSetpointDegC`, `temperatureSetpointCommandDegC`, `active`, `childLock`, `childLockCommand`, `condensationRiskLevel`.

Known facts:

- `pwhash` is the lowercase hex SHA-256 of the UTF-8 password.
- A new setpoint first shows up as pending in `temperatureSetpointCommandDegC`; it moves to `temperatureSetpointDegC` (and the command becomes `null`) only when the thermostat confirms it, 1–2 minutes later. Switching the system works the same way with `disabledCommand` and `disabled`, confirmed within about 40–80 s, and the child lock with `childLockCommand` and `childLock`, within about 30–60 s (live captures, 2026-11-04 and 2026-11-07).
- SBM keeps setpoints with 0.1 °C resolution, for example 22.3.
- `disabled: true` means the system is switched off in the SBM Fizikus app. A switched-off system reported `communicationActiveRelayModule: false` in one capture and `true` in another, while its thermostats still report fresh values. The integration does not read that flag, see [Availability and logging](#availability-and-logging).
- Timestamps look like `2026-10-10 12:45:11.578277` (no offset, UTC) or `2026-10-11 12:45:26.411281+00:00`. A live check on 2026-11-07 confirmed UTC for `lastStateUpdate` and the token `expiration`.
- 401 and 403 mean the token was rejected.
- A refused login is always HTTP 500 with `{"error": "invalid password"}` or `{"error": "user not found"}` (live captures, 2026-11-07), never a 401 or 403.
- Numbers come as integers or floats (`19`, `23.2`); unused meter channels are `-1`.
- An outdoor value that SBM does not know is `-1`. The integration reads an outdoor humidity of `-1` as unknown, and an outdoor temperature of `-1` only when the humidity is `-1` too, because -1 °C is a real winter temperature.

## Architecture

| Module | Role |
|---|---|
| `api.py` | The SBM client. It does not depend on Home Assistant. It uses Home Assistant's shared `aiohttp` session, a 30 s request timeout and an `asyncio.Lock` around the login. |
| `coordinator.py` | `SbmFizikusCoordinator` polls the apartment and runs the actions of the entities with their error handling (`async_run_action`); `SbmFizikusOutdoorCoordinator` polls the outdoor weather; `SbmFizikusData` is the runtime data of a config entry. |
| `entity.py` | The base entity of a thermostat, and the helper that adds the entities of new thermostats. |
| `climate.py`, `sensor.py`, `switch.py` | The platforms. |
| `config_flow.py` | Setup, apartment choice and reauthentication. |
| `diagnostics.py` | Diagnostics with the secrets redacted. |

The client mirrors the bridge's `SbmClient`: positional arrays are validated by length and type, an answer that is not valid JSON is an invalid response, and error messages never contain the login answer or a payload with the token. The request bodies are byte for byte the bridge's: compact JSON, the same key order, whole setpoints as integers like `22`. Answers in error messages are shortened to 300 characters (the bridge: 1000), because they also show up in the UI.

## Behaviour

### Authentication

- The config flow asks for the username and the password, computes `pwhash` at once, and stores only `username` and `pwhash` in the config entry. The password is never stored or logged.
- `pwhash` is as good as the password for SBM, so it is treated as a secret: redacted in diagnostics, never logged.
- The token and its expiration live in memory only. Before a call the client logs in again when the token expires within 30 s. On 401/403 it drops the token, logs in once more and retries the call once, like the bridge's `SbmSession`.
- A login that SBM refuses (status 500 with the `error` `invalid password` or `user not found`) raises `ConfigEntryAuthFailed`, which starts the reauthentication flow. A 401 or 403 of the login is not a refusal: SBM never answers a wrong password with it, and it may come from the API gateway, so it is a failure that the next poll retries instead of asking for the password. An answer that is not valid JSON or has no valid token is an invalid response, so a broken answer does not start a reauthentication. Any other status 500 is a failure of SBM, not a refusal, so it does not start one either.
- The reauthentication flow asks for the password and the username, filled in with the one of the entry, as the username or email of the account may have changed. It logs in and checks that the account still lists the apartment of the entry; otherwise it shows `wrong_account`, so a new username cannot move the entry to an account without the apartment. It also gives the new login to the other entries with the old username (the other apartments of the account) that the account lists, and reloads them, which closes their own reauthentication flows. Not chosen: a separate reconfigure flow; the only settings are the credentials, which the reauthentication covers (quality rule `reconfiguration-flow`).
- Other HTTP errors (including login route errors such as 404), network errors and timeouts raise `UpdateFailed` (poll) or `HomeAssistantError` (actions); the next poll tries again.
- Every exception shown to users uses `translation_domain` and `translation_key`, with the texts in the `exceptions` section of the translation files (quality rule `exception-translations`).

### Apartments and config entries

- Every apartment is a config entry of its own. Config entry `unique_id` = `apartment_id` (string).
- The flow logs in and lists the apartments of every building, leaving out those already set up. One left: it is set up at once. More: an `apartment` step offers them in a dropdown (label: the apartment name, or its id without one). The entry title is the apartment name.
- No buildings or apartments: abort with `no_apartments`. Every apartment already set up: abort with `all_apartments_configured`. An apartment set up by another flow while this one waits for the choice: abort with `already_configured`.
- Not chosen: one entry per account with the apartments as config subentries. It would share the login and the reauthentication, but entries of one apartment would need a migration, and the subentry flows need more code than the shared reauthentication above.

### Polling

- `SbmFizikusCoordinator` calls `get_apartment_data` every 120 s, the bridge's default. It is stored in `ConfigEntry.runtime_data`. Every 10 minutes it also calls `get_building_data`, see [Building switched off](#building-switched-off).
- Setup calls `async_config_entry_first_refresh`, so a failed first poll makes the entry retry (`ConfigEntryNotReady`) and a refused login starts reauthentication, before any entity is created (quality rule `test-before-setup`).
- After a successful action the coordinator refreshes at once (`async_request_refresh`); there is no optimistic state.
- The base URL is a constant and the interval is fixed; there is no options flow.

### Devices

- One device per thermostat: identifier `(sbm_fizikus, "{apartment_id}_{thermostat_id}")`, named after the thermostat, or `Thermostat {id}` without a name (as in the bridge, not translated), manufacturer `SBM-Verting`, model `Fizikus thermostat`.
- One device per apartment: identifier `(sbm_fizikus, "{apartment_id}")`, name `SBM Fizikus` (the bridge's fixed name, `DiscoveryBuilder.cs`), manufacturer `SBM-Verting`, model `Fizikus`, `sw_version` = `fwVer`, set at setup and updated at the poll that reports a new one, without a reload; a poll without `fwVer` keeps the known version. The apartment name goes into the config entry title, not the device name. With more than one apartment, every apartment device is called `SBM Fizikus`, so the entity IDs of the later ones get a `_2` suffix; the fixed name keeps the bridge's entity IDs.
- The apartment device is created in `async_setup_entry` with `device_registry.async_get_or_create` before the platforms are set up. Thermostats point to it with `via_device_id`. Not `via_device`: that field is removed in Home Assistant 2027.8. Not `ChildDeviceInfo`: the name of a parent device becomes part of new entity IDs, which would break the bridge-compatible IDs.

### Climate entity (one per thermostat)

- `has_entity_name = True` and `name = None`, so it takes the device name: `climate.living_room`, like the bridge. `unique_id` = `{apartment_id}_{thermostat_id}_climate`.
- `native_temperature_unit` °C; `native_current_temperature` = `measuredTempDegC`, `current_humidity` = `measuredHumPerc` as a whole percent, `native_target_temperature` = `temperatureSetpointCommandDegC` while a new setpoint is pending, otherwise `temperatureSetpointDegC`, like the app. Only the `native_` properties: the old ones are deprecated and stop working in Home Assistant 2027.11.
- `min_temp` 5, `max_temp` 35, `target_temperature_step` 0.5 (the card steps by 0.5), `precision` tenths. `climate.set_temperature` accepts any value in range and rounds it to 0.1 °C before sending; Home Assistant checks the range. The bridge accepts only multiples of 0.5 °C.
- `hvac_modes` = `[HEAT, COOL]`, like the bridge; `OFF` is added only while the system is switched off. `hvac_mode`: `OFF` while the system is switched off, otherwise `operationMode` 0 is `HEAT`, 1 is `COOL`, anything else is `None` (unknown, not off). The more-info dialog therefore shows a button for the other mode that has no effect; accepted for parity with the bridge. Not chosen: a dynamic `hvac_modes` holding only the current mode.
- `set_hvac_mode` does nothing (another mode is logged at debug level), like the bridge. A scene sets the mode before the temperature, so an error would stop a scene saved in the other season, or while the system was off, before it sets its target temperature. `climate.set_temperature` with an `hvac_mode` ignores the mode the same way and sets the temperature. Home Assistant itself still refuses `OFF` while the system is on, as it is not in `hvac_modes`: a scene saved while the system was off fails then (a known limitation in the README). Not chosen: a `ServiceValidationError` for another mode; it gave feedback in the more-info dialog, but broke such scenes.
- `hvac_action`: `OFF` while the system is switched off; otherwise `active` and heating is `HEATING`, `active` and cooling is `COOLING`, not active is `IDLE`, `active` with an unknown mode is `None`.
- `supported_features` = `TARGET_TEMPERATURE` only, so no `turn_on` and `turn_off`. `PARALLEL_UPDATES = 1`: actions go to SBM one at a time.

### Thermostat sensors

| Key | Name | Value | Class | Enabled by default |
|---|---|---|---|---|
| `humidity` | Humidity | `measuredHumPerc` as an integer: the thermostats measure whole percents and SBM sends integers like `62` (live capture, 2026-11-04); a float would be rounded half up | humidity, measurement, % | yes |
| `dew_point` | Dew point | `dewPointDegC` rounded half up to 0.1 °C: SBM sends it with full precision, like `13.04477696992492` (live capture, 2026-11-07), and the same value for every thermostat | temperature, measurement, °C | no |
| `temperature` | Temperature | `measuredTempDegC` | temperature, measurement, °C | no |
| `target_temperature` | Target temperature | `temperatureSetpointCommandDegC` while pending, otherwise `temperatureSetpointDegC` | temperature, measurement, °C | no |
| `system_mode` | System mode | `off` while the system is switched off, `heating` / `cooling` while active, otherwise `idle`; unknown while active with an unknown mode | enum (`heating`, `cooling`, `idle`, `off`), diagnostic | no |
| `condensation_risk` | Condensation risk | `condensationRiskLevel`: `0` is `none`, `1` is `high_humidity`, `2` is `cooling_disabled`, the notices on the room card of the app; anything else is unknown | enum (`none`, `high_humidity`, `cooling_disabled`) | no |

`unique_id` = `{apartment_id}_{thermostat_id}_{key}`. `PARALLEL_UPDATES = 0`, because the coordinator polls. The bridge has all of them but the dew point and the condensation risk, with the same names; its sensors are disabled by default too.

The condensation risk is SBM's value as it is. The app shows no notice while the system is switched off, whatever SBM reports; the sensor does not hide the level then, as high humidity is worth knowing with the system off too. It is not a diagnostic entity: it is a state to automate on, for example to start ventilation.

### Entity names and IDs

Home Assistant builds the entity ID of a new entity from its name in the language of the server when that language is in `NATIVE_ENTITY_IDS` (`homeassistant/generated/languages.py` in core), otherwise from English. Hungarian is in that list, so a translated name would give `sensor.nappali_paratartalom` instead of the bridge's `sensor.nappali_humidity` on a Hungarian server. Therefore:

- Every entity has a fixed English name, so no entity ID depends on the language, and the bridge's entities keep their IDs. The names stay English in every UI language.
- `translation_key` is used only for translated states, such as those of `system_mode`. A translated name would take precedence over the `name` of the entity description (only `_attr_name` takes precedence over a translation), so the translation files must not have a `name` for these keys.
- Device names come from SBM or are the fixed `SBM Fizikus` and `Thermostat {id}`.
- Changed room names update the device registry at the next poll. Only the reported name is updated: user-defined device and entity names and existing entity IDs are preserved. Missing names keep the last known name, also when the name is missing while the entry is set up, after a restart or a reload: the entities then take the name the device already has in the registry, and `Thermostat {id}` only for a thermostat that never had a name.
- A test checks the entity IDs with the server language set to `en` and to `hu`.
- Not chosen: translated names with entity IDs set by the integration. Home Assistant discourages setting `entity_id`, and such IDs would not follow a renamed device when the entity IDs are regenerated.

### Building switched off

- `get_building_data` (live capture, 2026-11-07) has `data.disabled`, the building-level switch of the app. While it is true, the app shows "Épület szinten a rendszer kikapcsolva", treats every apartment as switched off and greys out the apartment's switch; the apartment's own `disabled` can still be false.
- The integration does the same: the climate entities and the system mode show `off` (`SbmApartmentData.switched_off`), and the System switch is unavailable, while its `is_on` stays the apartment's own setting.
- `SbmFizikusCoordinator` asks `get_building_data` every 10 minutes (`BUILDING_UPDATE_INTERVAL`), not at every poll: the switch is set by hand and rarely changes. In between, a poll keeps the last value. A change is logged once at info level.
- The building of the apartment is looked up once per load: `get_building_access_rights`, then `get_apartment_access_rights` for each building until one lists the apartment. The outdoor coordinator uses the same lookup, so it is done once for both. A lock makes a coordinator wait for a lookup of the other one that is running, instead of starting a second one.
- The building switch is extra information: when the lookup or `get_building_data` fails, the poll keeps the last known value and logs the cause at debug level instead of failing, so the thermostats stay available. The next poll looks the building up again, as it may be gone, and asks again, without waiting for the 10 minutes, so a failure at startup does not hide a switched-off building; after an answer the 10 minutes apply again. A refused login still starts the reauthentication.
- The answer holds the address of the building, so the debug log leaves it out, like the list of buildings. Diagnostics show only `building_off`.
- Not chosen: the outdoor values of `get_building_data` (`externalTemperatureDegC`, `externalHumidityPerc`) for the outdoor sensors. They are the ones of `get_building_access_rights`, which the outdoor sensors already poll every 2 minutes.

### Child lock switch (one per thermostat)

- A `switch` on the device of the thermostat, named `Child lock` (`switch.<room>_child_lock`), `unique_id` `{apartment_id}_{thermostat_id}_child_lock`, entity category config, disabled by default. It locks the buttons of the thermostat with `update_thermostat_settings` and `child_lock`, like the room card of the app, then refreshes.
- On is `childLockCommand` while a change is pending, otherwise `childLock`, like the app. SBM reports `null` for a lock that was never set (live capture, 2026-11-07: every room), which is off; after the first unlock it is `false`.
- Availability like the other entities of the thermostat. The answer must name the thermostat (`thermostat_id`), for the target temperature too.
- Not chosen: the settings page of the app, which switches every room and sends the current target temperature with it; a lock per room is what the room card does, and one switch per room covers both.

### Switched-off system and the System switch

- `system_off` is `disabledCommand` while a switching is pending, otherwise `disabled`, like the app, so the switch, the climate entities and the system mode show a switching at once. Switching the system does not affect the availability of the entities.
- A `switch` on the apartment device, named `System`, `unique_id` `{apartment_id}_system`, enabled by default, sends `update_apartment_settings` like the app and then refreshes. `PARALLEL_UPDATES = 1`.
- The climate entities show `off` but do not switch the system: off for one room, for example from an automation for an open window, would switch off every room.
- The target temperature can still be set while the system is off.

### Water sensors (on the apartment device)

| Key | Name | Value | Class | Enabled by default |
|---|---|---|---|---|
| `forward_water_temperature` | Forward water temperature | `forwardWaterTemperature1DegC` | temperature, measurement, °C | no |
| `return_water_temperature` | Return water temperature | `returningWaterTemperature1DegC` | temperature, measurement, °C | no |
| `flow_rate` | Flow rate | `flowRateLiterPerHour1` | volume flow rate, measurement, L/h | no |

- The only values of the "Statisztika" page of the app (live capture, 2026-11-07: 26.0 °C, 25.4 °C, 0.0 L/h). They come with every `get_apartment_data`, so they need no request of their own. `unique_id` = `{apartment_id}_{key}`, entity IDs `sensor.sbm_fizikus_<key>`.
- `-1` means a channel that is not connected, so it is unknown: water in the pipes is never at -1 °C, and a flow is never negative. Only the first channel; SBM reports the second one as `-1`.
- The device of the apartment reports them, not the thermostats, so they stay available when SBM does not reach the thermostats, like the System switch.

### Outdoor sensors (on the apartment device)

| Key | Name | Value | Class | Enabled by default |
|---|---|---|---|---|
| `outdoor_temperature` | Outdoor temperature | `outdoorTemp` of the building | temperature, measurement, °C | no |
| `outdoor_humidity` | Outdoor humidity | `outdoorHum` of the building | humidity, measurement, % | no |

- The names of the bridge's apartment sensors, so the entity IDs are `sensor.sbm_fizikus_outdoor_temperature` and `sensor.sbm_fizikus_outdoor_humidity`. `unique_id` = `{apartment_id}_{key}`.
- `SbmFizikusOutdoorCoordinator` polls `get_building_access_rights` every 120 s. It has no first refresh at setup, and a coordinator polls only while entities listen to it. A disabled entity is not added, so it never listens; enabling one reloads the config entry, and disabling one removes its listener at once. The first outdoor sensor that is added requests a refresh in the background, so the values do not wait for the first interval and a slow SBM does not hold up the setup of the entry. Not chosen: a longer interval. The history of a week (2026-11-07) shows that SBM updates the outdoor values at least every 2 minutes, so a longer interval would show real changes later; most of the changes are small, like 9.4, 9.6, 9.5 °C.
- Not chosen: the `async_contexts()` gating of some core integrations (for example `iron_os`, `jvc_projector`); the outdoor data has its own endpoint, so a separate coordinator does the same with the standard listener behaviour. Also not chosen: an option to turn the outdoor weather on; Home Assistant enables optional entities through the entity registry (quality rule `entity-disabled-by-default`).
- The building of the apartment is not stored. The coordinator of the apartment looks it up once per load, also for [Building switched off](#building-switched-off), and the outdoor coordinator uses that lookup; when the building is no longer listed, it asks `get_apartment_access_rights` for each building again until it finds the apartment. No building with the apartment is an `UpdateFailed` (`building_not_found`) of the outdoor coordinator. The bridge uses the first building instead.
- An outdoor error makes only the outdoor sensors unavailable, and the thermostat flags do not affect them. A refused login starts reauthentication, like the main coordinator.

### Availability and logging

- All thermostat entities are unavailable when the last poll failed, or when `communicationActiveThermostats` is false, the only connection indicator of the app. `communicationActiveRelayModule` is not read, as the app does not use it either. The bridge's `IsConnected` also requires it, so the integration differs from the bridge here on purpose.
- A thermostat missing from the latest answer makes its entities unavailable. Thermostats added later get their entities at the next poll (quality rule `dynamic-devices`): each platform keeps the ids it has added entities for, in `SbmFizikusData.known_thermostats`. Deleting a stale thermostat device forgets its id, so it gets entities again if SBM reports it later.
- `async_remove_config_entry_device` allows deleting a thermostat device that is no longer in the latest answer, and refuses present thermostats and the apartment device (quality rule `stale-devices`). While the entry is not loaded (for example while setup retries) there is no answer to check against, so it refuses every device.
- Failed polls are logged once by the coordinator as an error, and the recovery once as a warning. It also logs a warning once when SBM stops reaching the thermostats, and once when it reaches them again (quality rule `log-when-unavailable`). The recoveries are warnings because Home Assistant hides info messages by default, so the log would show only the start of an outage; the info message of the base class stays.
- When SBM cannot be reached while the entry is set up, for example at startup, a warning with the cause is logged once, and another one when a retry succeeds. Home Assistant logs this only as an info message. Every retry has a new coordinator, so the entries with a logged outage are kept in `hass.data`.
- The config flow logs a warning with the cause when SBM cannot be reached while adding the integration or during a reauthentication, as the form shows only the kind of the error.
- The debug log shows every request with its answer, status and duration, the parsed state of every poll, and the cause of every failed call. That also covers the errors of the config flow, which the form shows only by kind, and the repeated failures of a poll, which the coordinator logs only once. The username, the password hash and the token are replaced in the requests. The answers to the login and to `get_building_access_rights` are left out, because they hold the token and the address of the building; the parsed outdoor values are logged instead.

### Diagnostics

`pwhash`, `token` and `username` are redacted. The entry title, the apartment name and the thermostat names stay, because they help to match the entities. The outdoor part shows the parsed values and the building id, not SBM's answer, which holds the address of the building.

## Switching from the MQTT bridge

There is no migration code. The README explains the switch:

1. Stop the bridge, otherwise it publishes its retained discovery again.
2. Delete the SBM Fizikus devices of the MQTT integration in Home Assistant.
3. Add the SBM Fizikus integration.

Because the device and entity names match, Home Assistant gives the new entities the old entity IDs, so automations, dashboards and recorder history continue. Customizations of the old entities in the registry (names, areas, icons) and the bridge's apartment-level system mode sensor do not carry over.

## Packaging

- **Layout:** domain `sbm_fizikus`, a custom integration in `custom_components/sbm_fizikus/` at the repository root, distributed through HACS.
- **Manifest:** `iot_class: cloud_polling`, `integration_type: hub`, `config_flow: true`, `categories` `climate.thermostats_heating` (the category of thermostats, which the Marketplace catalog lists the integration under), no external requirements, and the keys a custom integration needs: `version` (the release tag), `issue_tracker`, `documentation`, `codeowners`.
- **hacs.json:** `name`, and `homeassistant` 2026.11.0 as the minimum: the `native_` climate properties first exist in 2026.11, and setup and unload return `None`, as 2026.11 expects.
- **Brand images:** `custom_components/sbm_fizikus/brand/` (supported since Home Assistant 2026.3), own artwork. The artwork is square, so only `icon.png` (256 px) and `icon@2x.png` (512 px); a missing logo falls back to the icon.
- **Translations:** custom integrations have no `strings.json`, so `translations/en.json` (complete, the fallback) and `translations/hu.json` are the sources, with the `config`, `entity` and `exceptions` sections.
- **Config flow schemas:** `probatio`, which replaced `voluptuous` in Home Assistant 2026.11.

## Tests and CI

- `pytest` with `pytest-homeassistant`, the test package Home Assistant core publishes with each release (`pytest-homeassistant-custom-component` stopped at 2026.10). A fake SBM API answers every operation from the fixtures; tests queue other answers.
- The tests cover the API client (request bodies equal the bridge's and the app's, the hash, re-login on 401/403, token expiry, parsing), the config flow (success, errors with recovery, no apartments, choosing an apartment, already configured, reauthentication of all apartments of the account), the coordinators, the climate mapping, the sensors, the switch, the outdoor sensors, availability, diagnostics, entity IDs in English and Hungarian, new and stale thermostats, and that the manifest, `pyproject.toml`, `uv.lock`, the changelog and this document name the same version.
- Coverage is 100 % for lines and branches, and CI enforces it. The quality scale has no tiers for custom integrations, but its rules up to Silver, and the Gold rules named here, are the target.
- The tests need the Python version of the Home Assistant release they run against, pinned in `.python-version`; `uv` manages the environment. Home Assistant does not run on Windows, so the README runs the tests in Docker or WSL there.
- CI runs `ruff`, `mypy` (strict, on the integration only: the tests read states and private values without the checks strict typing asks for), `hassfest` and the HACS validation action, and the tests twice: with the Home Assistant release of `uv.lock`, and with the minimum release in `hacs.json`, so code that needs a newer release fails before it reaches users. Dependabot opens one grouped pull request a month for the Python dependencies, which moves the tests to the newest release, and one for the GitHub Actions. A weekly workflow runs the tests against the newest release, beta included, to notice deprecations before the stable release; GitHub notifies only about its failures.
- A pushed version tag publishes the GitHub release with the section of the tag in `CHANGELOG.md` as its notes (`release.yml`). The tag must be the version of the manifest. The workflow first runs the lint, the type check and the tests (`tests.yml`, as a reusable workflow) on the commit of the tag, and publishes nothing when one fails. A release that already exists, for example one made by hand, stays as it is.

## Repository

`github.com/davidsarkany/homeassistant-sbm-fizikus`, GPL-3.0. `README.md` is the user documentation in Hungarian, `README.en.md` the same in English; keep the two in step. `CHANGELOG.md` lists the changes.

## Open points

- The SBM Fizikus web app renews its token with `refresh_jw_token` (payload `refresh_token`); the integration logs in again instead.
- The bridge's README does not point to the integration yet.
