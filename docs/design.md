# SBM Fizikus Home Assistant integration: design

This document describes how the integration works and why: the SBM API it uses, the behaviour of its entities, and the decisions behind them, with the alternatives that were not chosen. It describes version 0.1.3.

The reference implementation of the SBM API is the [sbm-fizikus-to-mqtt](https://github.com/davidsarkany/sbm-fizikus-to-mqtt) bridge. Paths starting with `src/` refer to that repository.

## Purpose

Home Assistant users can control their SBM Fizikus thermostats without running the MQTT bridge and an MQTT broker. The integration talks to the SBM cloud directly, is set up from the Home Assistant UI with the SBM username and password, and exposes each thermostat as a climate entity.

The MQTT bridge stays as it is. The integration is a second, independent way to use SBM, and users of the bridge can switch to it without losing their entity IDs, automations or history.

## Scope

Supported:

- Each thermostat's measured temperature, humidity, dew point and target temperature, and whether it calls for heating or cooling.
- Setting a thermostat's target temperature.
- Switching the SBM Fizikus system of an apartment on and off.
- The outdoor temperature and humidity of the building, fetched only while an outdoor sensor is enabled.
- Setup and reauthentication from the UI; every apartment of the account, one config entry per apartment.
- The same entity IDs as the MQTT bridge.

Not supported:

- Meters, water temperatures and flow, energy and volume counters from `get_apartment_data`.
- The child lock (`childLock`) and the manual cooling disable (`coolingDisabledManually`).
- Changing heating or cooling, which is set centrally for the building.
- The building-level switch (`disabled` of `get_building_data`): a system switched off for the whole building does not show as off.
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
| `get_building_access_rights` | `jwt_token` | positional `[[buildingId, country, postalCode, city, street, outdoorTemp, outdoorHum, name], ...]`: `buildingId`, `outdoorTemp`, `outdoorHum` | setup; every poll while an outdoor sensor is enabled |
| `get_apartment_access_rights` | `building_id` (**string**) | positional `[[buildingId, apartmentId, name, isOnline], ...]`: `apartmentId`, `name` | setup, for every building; once per load to find the building of the apartment, while an outdoor sensor is enabled |
| `get_apartment_data` | `apartment_id` (int) | `operationMode`, `communicationActiveThermostats`, `disabled`, `disabledCommand`, `fwVer`, `thermostats[]` | every poll |
| `update_thermostat_settings` | `thermostat_id` (int), `temp_setpoint_degC` (number) | `thermostat_id` | set temperature |
| `update_apartment_settings` | `apartment_id` (**string**), `disabled` (bool) | `apartment_id` (string), next to `message`: `Apartment settings updated successfully.` | switch the system on or off |

Per thermostat: `ID`, `name` (may be null), `measuredTempDegC`, `measuredHumPerc`, `dewPointDegC`, `temperatureSetpointDegC`, `temperatureSetpointCommandDegC`, `active`.

Known facts:

- `pwhash` is the lowercase hex SHA-256 of the UTF-8 password.
- A new setpoint first shows up as pending in `temperatureSetpointCommandDegC`; it moves to `temperatureSetpointDegC` (and the command becomes `null`) only when the thermostat confirms it, 1–2 minutes later. Switching the system works the same way with `disabledCommand` and `disabled`, confirmed within about 40 s (live captures, 2026-11-04).
- SBM keeps setpoints with 0.1 °C resolution, for example 22.3.
- `disabled: true` means the system is switched off in the SBM Fizikus app. A switched-off system reports `communicationActiveRelayModule: false`, while its thermostats still report fresh values. The integration does not read that flag, see [Availability and logging](#availability-and-logging).
- Timestamps look like `2026-10-10 12:45:11.578277` (no offset, UTC) or `2026-10-11 12:45:26.411281+00:00`.
- 401 and 403 mean the token was rejected.
- Numbers come as integers or floats (`19`, `23.2`); unused meter channels are `-1`.
- An outdoor value that SBM does not know is `-1`. The integration reads an outdoor humidity of `-1` as unknown, and an outdoor temperature of `-1` only when the humidity is `-1` too, because -1 °C is a real winter temperature.

## Architecture

| Module | Role |
|---|---|
| `api.py` | The SBM client. It does not depend on Home Assistant. It uses Home Assistant's shared `aiohttp` session, a 30 s request timeout and an `asyncio.Lock` around the login. |
| `coordinator.py` | `SbmFizikusCoordinator` polls the apartment; `SbmFizikusOutdoorCoordinator` polls the outdoor weather; `SbmFizikusData` is the runtime data of a config entry. |
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
- A login that SBM answers but refuses (401/403, or a JSON object without a token) raises `ConfigEntryAuthFailed`, which starts the reauthentication flow. An answer that is not valid JSON is an invalid response, so a broken answer does not start a reauthentication.
- The reauthentication flow asks only for the password and checks it by fetching the apartment data of the entry. It also gives the new hash to the other entries with the same username (the other apartments of the account) and reloads them, which closes their own reauthentication flows.
- Other HTTP errors (including login route errors such as 404), network errors and timeouts raise `UpdateFailed` (poll) or `HomeAssistantError` (actions); the next poll tries again.
- Every exception shown to users uses `translation_domain` and `translation_key`, with the texts in the `exceptions` section of the translation files (quality rule `exception-translations`).

### Apartments and config entries

- Every apartment is a config entry of its own. Config entry `unique_id` = `apartment_id` (string).
- The flow logs in and lists the apartments of every building, leaving out those already set up. One left: it is set up at once. More: an `apartment` step offers them in a dropdown (label: the apartment name, or its id without one). The entry title is the apartment name.
- No buildings or apartments: abort with `no_apartments`. Every apartment already set up: abort with `all_apartments_configured`. An apartment set up by another flow while this one waits for the choice: abort with `already_configured`.
- Not chosen: one entry per account with the apartments as config subentries. It would share the login and the reauthentication, but entries of one apartment would need a migration, and the subentry flows need more code than the shared reauthentication above.

### Polling

- `SbmFizikusCoordinator` calls `get_apartment_data` every 120 s, the bridge's default. It is stored in `ConfigEntry.runtime_data`.
- Setup calls `async_config_entry_first_refresh`, so a failed first poll makes the entry retry (`ConfigEntryNotReady`) and a refused login starts reauthentication, before any entity is created (quality rule `test-before-setup`).
- After a successful action the coordinator refreshes at once (`async_request_refresh`); there is no optimistic state.
- The base URL is a constant and the interval is fixed; there is no options flow.

### Devices

- One device per thermostat: identifier `(sbm_fizikus, "{apartment_id}_{thermostat_id}")`, named after the thermostat, or `Thermostat {id}` without a name (as in the bridge, not translated), manufacturer `SBM-Verting`, model `Fizikus thermostat`.
- One device per apartment: identifier `(sbm_fizikus, "{apartment_id}")`, name `SBM Fizikus` (the bridge's fixed name, `DiscoveryBuilder.cs`), manufacturer `SBM-Verting`, model `Fizikus`, `sw_version` = `fwVer`, set at every setup. The apartment name goes into the config entry title, not the device name. With more than one apartment, every apartment device is called `SBM Fizikus`, so the entity IDs of the later ones get a `_2` suffix; the fixed name keeps the bridge's entity IDs.
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
| `dew_point` | Dew point | `dewPointDegC` | temperature, measurement, °C | no |
| `temperature` | Temperature | `measuredTempDegC` | temperature, measurement, °C | no |
| `target_temperature` | Target temperature | `temperatureSetpointCommandDegC` while pending, otherwise `temperatureSetpointDegC` | temperature, measurement, °C | no |
| `system_mode` | System mode | `off` while the system is switched off, `heating` / `cooling` while active, otherwise `idle`; unknown while active with an unknown mode | enum (`heating`, `cooling`, `idle`, `off`), diagnostic | no |

`unique_id` = `{apartment_id}_{thermostat_id}_{key}`. `PARALLEL_UPDATES = 0`, because the coordinator polls. The bridge has all of them but the dew point, with the same names; its sensors are disabled by default too.

### Entity names and IDs

Home Assistant builds the entity ID of a new entity from its name in the language of the server when that language is in `NATIVE_ENTITY_IDS` (`homeassistant/generated/languages.py` in core), otherwise from English. Hungarian is in that list, so a translated name would give `sensor.nappali_paratartalom` instead of the bridge's `sensor.nappali_humidity` on a Hungarian server. Therefore:

- Every entity has a fixed English name, so no entity ID depends on the language, and the bridge's entities keep their IDs. The names stay English in every UI language.
- `translation_key` is used only for translated states, such as those of `system_mode`. A translated name would take precedence over the `name` of the entity description (only `_attr_name` takes precedence over a translation), so the translation files must not have a `name` for these keys.
- Device names come from SBM or are the fixed `SBM Fizikus` and `Thermostat {id}`.
- A test checks the entity IDs with the server language set to `en` and to `hu`.
- Not chosen: translated names with entity IDs set by the integration. Home Assistant discourages setting `entity_id`, and such IDs would not follow a renamed device when the entity IDs are regenerated.

### Switched-off system and the System switch

- `system_off` is `disabledCommand` while a switching is pending, otherwise `disabled`, like the app, so the switch, the climate entities and the system mode show a switching at once. Switching the system does not affect the availability of the entities.
- A `switch` on the apartment device, named `System`, `unique_id` `{apartment_id}_system`, enabled by default, sends `update_apartment_settings` like the app and then refreshes. `PARALLEL_UPDATES = 1`.
- The climate entities show `off` but do not switch the system: off for one room, for example from an automation for an open window, would switch off every room.
- The target temperature can still be set while the system is off.

### Outdoor sensors (on the apartment device)

| Key | Name | Value | Class | Enabled by default |
|---|---|---|---|---|
| `outdoor_temperature` | Outdoor temperature | `outdoorTemp` of the building | temperature, measurement, °C | no |
| `outdoor_humidity` | Outdoor humidity | `outdoorHum` of the building | humidity, measurement, % | no |

- The names of the bridge's apartment sensors, so the entity IDs are `sensor.sbm_fizikus_outdoor_temperature` and `sensor.sbm_fizikus_outdoor_humidity`. `unique_id` = `{apartment_id}_{key}`.
- `SbmFizikusOutdoorCoordinator` polls `get_building_access_rights` every 120 s. It has no first refresh at setup, and a coordinator polls only while entities listen to it. A disabled entity is not added, so it never listens; enabling one reloads the config entry, and disabling one removes its listener at once. The first outdoor sensor that is added requests a refresh in the background, so the values do not wait for the first interval and a slow SBM does not hold up the setup of the entry.
- Not chosen: the `async_contexts()` gating of some core integrations (for example `iron_os`, `jvc_projector`); the outdoor data has its own endpoint, so a separate coordinator does the same with the standard listener behaviour. Also not chosen: an option to turn the outdoor weather on; Home Assistant enables optional entities through the entity registry (quality rule `entity-disabled-by-default`).
- The building of the apartment is not stored. At the first poll, and when the building is no longer listed, the coordinator asks `get_apartment_access_rights` for each building until it finds the apartment. No building with the apartment is an `UpdateFailed` (`building_not_found`). The bridge uses the first building instead.
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
- The tests cover the API client (request bodies equal the bridge's and the app's, the hash, re-login on 401/403, token expiry, parsing), the config flow (success, errors with recovery, no apartments, choosing an apartment, already configured, reauthentication of all apartments of the account), the coordinators, the climate mapping, the sensors, the switch, the outdoor sensors, availability, diagnostics, entity IDs in English and Hungarian, new and stale thermostats.
- Coverage is 100 % for lines and branches, and CI enforces it. The quality scale has no tiers for custom integrations, but its rules up to Silver, and the Gold rules named here, are the target.
- The tests need the Python version of the Home Assistant release they run against, pinned in `.python-version`; `uv` manages the environment. Home Assistant does not run on Windows, so the README runs the tests in Docker or WSL there.
- CI runs `ruff`, the tests, `hassfest` and the HACS validation action.

## Repository

`github.com/davidsarkany/homeassistant-sbm-fizikus`, GPL-3.0. `README.md` is the user documentation in Hungarian, `README.en.md` the same in English; keep the two in step. `CHANGELOG.md` lists the changes.

## Open points

- The SBM Fizikus web app renews its token with `refresh_jw_token` (payload `refresh_token`); the integration logs in again instead.
- The web app switches the child lock with `update_thermostat_settings` (`child_lock`); the integration does not support it yet.
- The bridge's README does not point to the integration yet.
