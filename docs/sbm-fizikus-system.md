# How the SBM Fizikus system works

This document describes the original SBM Fizikus system as far as it is visible from outside: the cloud API and how the official web app uses it. It is the reference for the integration's API client.

Sources, as of 2026-11-04:

- The web app at <https://app.sbmfizikus.com/>: a React single-page app, bundle `static/js/main.1eb509a5.js`. Read as text, not run.
- Live captures by the user: a `get_apartment_data` answer, and the `update_apartment_settings` call with its answer.
- The anonymized fixtures of the MQTT bridge, which come from the same apartment on 2026-10-10.

What the app does is a fact of the bundle. What a field means is marked as an inference where the bundle does not say it.

## Parts of the system

- **Building**: has a heat center (central heating and cooling), an address, outdoor weather and a building-level switch and child lock. The building decides whether the system heats or cools.
- **Apartment**: the SBM Fizikus device in the apartment, with a relay module that opens the valves of the rooms. It can be switched off on its own. It reports water temperatures and flow, and has meter fields.
- **Thermostat**: one per room. It measures temperature and humidity, holds the target temperature and a child lock, and reports whether it calls for heating or cooling.
- **Cloud API**: one HTTPS endpoint, `https://ps9et7hsp3.execute-api.eu-central-1.amazonaws.com/frontend` (Amazon API Gateway, Frankfurt).
- **Web app**: an installable web app (PWA) with web push notifications. The SBM Fizikus mobile app is not covered here.

## Transport

Every call is a `PUT` to the endpoint with a JSON body:

```json
{"operation": "<name>", "payload": {"...": "...", "jwt_token": "<token>"}}
```

- The app adds `jwt_token` to the payload of every call after the login. There is no `Authorization` header.
- An answer with a status that is not 2xx is an error; the app shows its text.
- The login answer can carry an `error` field instead of a token.

## Login and tokens

### `get_jw_token`

Payload: `username_or_email`, `pwhash` (the lowercase hex SHA-256 of the UTF-8 password; the app computes it in the browser), and optionally `login_level`.

The answer is an object `{token, rights, expiration, refresh_token, refresh_token_expiration}`, or the same values as an array in that order. `token: null` means wrong credentials.

A refused login, captured live on 2026-11-07, is not a 401 or 403 and has no `token`: SBM answers HTTP 500 with an `error` that names the reason.

| Login | Status | Answer |
|---|---|---|
| Unknown username | 500 | `{"error": "user not found"}` |
| Known username, wrong password | 500 | `{"error": "invalid password"}` |

SBM's own failures may come with status 500 too, so only these errors mean wrong credentials.

- **Roles**: when `rights` lists more than one role and no `login_level` was sent, the app lets the user pick one and logs in again with it. Seen values: `ap_owner` (the owner of an apartment, in the user's token), `ap_resident` (a sub-user added by the owner).
- **The token** is a JWT. Its claims: `username`, `account_id`, `exp`, `type` (`access`), `login_level`.
- **`expiration`** looks like `2026-11-05 17:02:55.123456`, without an offset. The app parses it with `Date.parse` after replacing the space with `T`, which JavaScript reads as local time. The integration reads it as UTC; both refresh well before the end, and a rejected token leads to a new login anyway.

### `refresh_jw_token`

Payload: `refresh_token` only (no `jwt_token`). The answer has a new `token`, `expiration`, `refresh_token` and `refresh_token_expiration`. The app calls it before a call when the token has expired. If it fails, the app logs out ("A munkamenet lejárt").

The integration does not use it: it logs in again with the stored hash, which works as long as the password is the same.

### Other account calls

| Operation | Payload | Purpose |
|---|---|---|
| `update_client_info` | `domain`, `user_agent` | Sent after the login and when the app starts. |
| `change_password` | `new_pwhash` | Changes the password. |
| `initiate_password_reset` | `email` | Sends a reset email; no token. |

## Choosing a building and an apartment

| Operation | Payload | Answer |
|---|---|---|
| `get_building_access_rights` | (none) | Rows `[id, country, postalCode, city, street, outdoorTemperature, outdoorHumidity, name]`. `-1` means the outdoor value is not known. |
| `get_apartment_access_rights` | `building_id` (string) | Rows `[buildingId, apartmentId, name, online]`. |
| `get_building_image` | `building_id` | `image_base64`, the background picture of the building. |

The app sorts the buildings by city and street, and the apartments by the letter and number in their name. With one building and one apartment it selects them without asking.

## Building data: `get_building_data`

Payload: `building_id` (int). The values are under `data`:

| Field | Meaning in the app |
|---|---|
| `disabled` | The system is switched off for the whole building. The app shows "Épület szinten a rendszer kikapcsolva" (System is disabled at building level), treats every apartment as switched off, and greys out the apartment's on/off switch. |
| `childLock`, `childLockTemperatureDegC` | A child lock for the whole building; the app shows its temperature. |
| `weatherCode` | The weather icon: 0 sunny (clear at night), 1 partly cloudy, 2 cloudy, 3 rain, 4 heavy rain, 5 snow, 6 windy. |

## Apartment data: `get_apartment_data`

Payload: `apartment_id`. The app sends it as an int on the dashboard and as a string on the settings page, so both work. The app loads it when a page opens; no timer that reloads it was found in the bundle.

### Fields the app uses

| Field | Meaning in the app |
|---|---|
| `operationMode` | `0` heating, `1` cooling. Other values show no mode. |
| `disabled` | The apartment's system is switched off ("Rendszer kikapcsolva"). |
| `disabledCommand` | A switching that SBM has not confirmed yet. When it is not `null`, the app shows it instead of `disabled`. |
| `communicationActiveThermostats` | The connection indicator of the dashboard ("Kapcsolat rendben" / "Nincs kapcsolat"). |
| `dehumidificationEquipment` | `0`: the apartment has no dehumidifier, and the app hides its switch. |
| `airDryingMaxLevel` | The dehumidifier is enabled when it is not `0` and not `null`. |
| `forwardWaterTemperature1DegC`, `returningWaterTemperature1DegC`, `flowRateLiterPerHour1` | The only values of the "Statisztika" page ("Hőmérsékleti értékek"): the water from the heat center, the water going back, and the flow through the apartment. |
| `monthly` | A list of monthly records (`interval`, `heating_quantity`, `used_heating_quantity`, `cooling_quantity`, `used_cooling_quantity`, `hotwater_heating_quantity`, `used_hotwater_heating_quantity`, `hotwater_usage`, `used_hotwater`, `coldwater_usage`, `used_coldwater`). The app has code for it but does not show it, and the user's answer has no `monthly`. |

Per thermostat (`thermostats[]`):

| Field | Meaning in the app |
|---|---|
| `ID`, `name` | The room. The name can be edited in the app. |
| `temperatureSetpointDegC` | The target temperature. |
| `temperatureSetpointCommandDegC` | A target temperature that SBM has not confirmed yet. When it is set, the app shows it instead. |
| `measuredTempDegC`, `measuredHumPerc` | The measured values. |
| `dewPointDegC` | The dew point. In the user's answer it is the same for every room. |
| `active` | The room calls for heating or cooling. The app shows the heating or cooling icon only while the system is on. |
| `condensationRiskLevel` | Sets the style of the room card: `2` "dewPointDisabled", `0` "dewPointWarning"; the app uses `-1` while the system is off. The texts are "Hűtés letiltva – páralecsapódás várható" (cooling disabled, condensation expected) and "Szellőztess" (ventilate). Which level is which text is not clear from the bundle; in the user's answer every room has `0` in normal conditions. |
| `childLock`, `childLockCommand` | The child lock, and a change that SBM has not confirmed yet (shown instead when not `null`). |

The `…Command` fields follow one pattern: they hold a value that the app sent and the device has not taken yet. SBM keeps the confirmed value in the field without `Command`.

### Fields the app does not use

`communicationActiveRelayModule`, `relaysOpen`, `lastStateUpdate` (apartment and thermostats), `fwVer`, `thermostatFwVer`, `thermostatFwVerLabel`, `batteryLevel`, `wifiSigalStrength`, `coolingDisabledManually`, `dewPointOffsetDegC`, `configUpdatedByWebapp`, the second water channel (`…2…`), and every meter field (`heatingHeatQuantity1KWh`, `coolingHeatQuantity1KWh`, `heatingCoolingVolumeMeter1M3`, `hotWater…`, `coldWater…`, `lastMeterSynchronisation`).

What the live data shows about them (comparison with the fixture of the same apartment 25 days earlier):

- `communicationActiveRelayModule` was `false` while the system was switched off, and the thermostats still reported fresh values. In a live test on 2026-11-07 it stayed `true` while the system was switched off for about a minute, so it does not follow the switch reliably.
- The heating energy rose by 83 kWh and the heating/cooling water volume by 26.5 m³. The cooling energy, the hot water and the cold water values did not change, and `lastMeterSynchronisation` is the string `"None"`.
- Inference: the energy values are a register divided by 0.86 (the factor from megacalories to kWh): `3566.08488… × 0.86 = 3066.833`, and the same holds for the cooling and hot water energy. The hot water energy (1 656 156 kWh for 624.8 m³) cannot be right, so not every register is in the unit its name says.
- `-1` marks a channel or value that is not connected (`…2…` fields, outdoor values).

## Changing settings

### `update_thermostat_settings`

Payload: `thermostat_id` and one or more of:

| Field | Used for |
|---|---|
| `temp_setpoint_degC` | The target temperature. The room card steps by 0.1 °C. "Hőmérséklet egységesítés" (quick settings) sends it to every thermostat one by one. |
| `new_name` | Renaming the room. |
| `child_lock` (bool) | The child lock of one room, from the room card. The settings page switches every room and sends the current `temp_setpoint_degC` with it. |

The answer to a target temperature has `message: "Thermostat settings updated successfully."` (the app checks this text) and `thermostat_id`.

### `update_apartment_settings`

Payload: `apartment_id` (a string, as the app sends it) and one of:

| Field | Used for |
|---|---|
| `disabled` (bool) | Switching the apartment's system off (`true`) or on (`false`). Not possible while the building is switched off. |
| `air_drying_max_level` | The dehumidifier: `1` enabled, `0` disabled. |

The answer, captured by the user, is the same for on and off: HTTP 200 and `{"message": "Apartment settings updated successfully.", "apartment_id": "123"}`, with the id of the apartment.

A live test on 2026-11-07: right after the call, `get_apartment_data` reports the new value in `disabledCommand` while `disabled` keeps the old one. The device confirmed switching off after about 80 s and switching on after about 47 s; then `disabled` has the new value and `disabledCommand` is `null`. `data.disabled` of `get_building_data` stayed `false` all the time: it is the switch of the whole building, not of the apartment.

## Users, messages and notifications

| Operation | Payload | Purpose |
|---|---|---|
| `get_users_by_location` | `apartment_id` | The users of the apartment; the app lists those with `access_type` `ap_resident`. |
| `register_user` | `username`, `email`, `pwhash`, `password`, `role` (`6`), `valid_until` (optional), `active` | Creates a sub-user. |
| `check_email_is_valid` | | Checks an email address before creating a user. |
| `add_apartment_access_right` | `user_id`, `apartment_id`, `rights` (`0`) | Gives a user access to the apartment. |
| `send_apartment_access_email` | `user_id`, `apartment_id` | Emails the access to an existing user. |
| `set_user_active` | `user_id`, `active` | Enables or disables a sub-user. |
| `delete_apartment_access_right` | `user_id`, `apartment_id` | Removes a user's access. |
| `get_messages` | `apartment_ids`, `building_ids` | Messages with `id`, `is_read`, `created_at`; the app shows the last 20 of 90 days. |
| `mark_message_as_read` | `message_id` | |
| `notification_subscribe` | `subscription_obj` (a JSON string of the web push subscription; `{}` to unsubscribe) | Web push notifications. |

## What it means for the integration

Already used: the login, the building and apartment lists, `get_building_data` with `disabled`, `get_apartment_data`, the target temperature, the child lock, and `update_apartment_settings` with `disabled` (the System switch).

Worth knowing for later work:

- **Building-level switch**: `get_building_data` `disabled` switches every apartment off, and the apartment's own `disabled` can still be `false` then. The integration reads it every 10 minutes and shows the system as off then. The answer (live capture, 2026-11-07) has `message`, `building_id` and `data`, with the address, `disabled`, `childLock`, the outdoor weather (`externalTemperatureDegC`, `externalTemperatureAvgDegC`, `externalHumidityPerc`), `heatingCircuits` and `apartmentInfo`.
- **Pending commands**: right after a change, `…Command` fields hold the new value until the device confirms it. Like the app, the integration shows the pending target temperature (`temperatureSetpointCommandDegC`) and the pending switching (`disabledCommand`) instead of the confirmed values. The integration shows the pending child lock (`childLockCommand`) the same way.
- **Child lock**: per room with `child_lock` in `update_thermostat_settings`; the state is `childLock` (or `childLockCommand`). A lock that was never set is `null` (live capture, 2026-11-07). A live test on 2026-11-07: the answer is `{"message": "Thermostat settings updated successfully.", "thermostat_id": <id>}`; the new value is pending in `childLockCommand` until the thermostat confirms it, after about 60 s for locking and 30 s for unlocking. After unlocking, `childLock` is `false`, no longer `null`.
- **Dehumidifier**: `air_drying_max_level` in `update_apartment_settings`, only where `dehumidificationEquipment` is not `0`.
- **Water values**: the forward and return temperature and the flow are what the app shows; the meters are not shown and partly implausible.
- **`refresh_jw_token`** exists, but logging in again with the stored hash is enough for the integration.
