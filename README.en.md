# SBM Fizikus for Home Assistant

[Magyar](README.md) | **English**

A Home Assistant integration for SBM Fizikus (SBM-Verting) heating and cooling systems. It connects to the SBM Fizikus cloud with your SBM account, shows every thermostat of your apartment as a climate entity, and lets you set their target temperatures.

## Requirements

- Home Assistant 2026.11.0 or newer.
- An SBM Fizikus account: the username (or email address) and password you sign in to the SBM Fizikus app with.

## Installation

### HACS

[![Open your Home Assistant instance and open the SBM Fizikus repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=davidsarkany&repository=homeassistant-sbm-fizikus&category=integration)

1. Select the button above: it opens this repository in HACS in your Home Assistant. Or, in HACS, open the menu in the top right corner, choose **Custom repositories**, and add `https://github.com/davidsarkany/homeassistant-sbm-fizikus` with the type **Integration**.
2. Download **SBM Fizikus**.
3. Restart Home Assistant, and refresh the page in the browser.

### Manual

1. Download the latest release, or the source of the repository.
2. Copy the `custom_components/sbm_fizikus` folder into the `custom_components` folder of your Home Assistant configuration.
3. Restart Home Assistant.

## Setup

The integration can only be set up after it is downloaded and Home Assistant was restarted. Before that, the button below opens an error saying that the integration does not support configuration via the UI.

[![Open your Home Assistant instance and start setting up SBM Fizikus.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=sbm_fizikus)

1. Go to **Settings** > **Devices & services**, select **Add integration**, and choose **SBM Fizikus**.
2. Enter your SBM account:
   - **Username or email**: what you sign in to the SBM Fizikus app with.
   - **Password**: the password of the account. Home Assistant stores only a SHA-256 hash of it, which SBM accepts instead of the password. The password itself is never stored. For SBM the hash works like the password, and a weak password can be recovered from it, so keep your Home Assistant backups as safe as the password.
3. The integration signs in. When the account has more than one apartment that is not set up yet, choose the one to set up.

Every apartment is a separate entry with its own thermostats. To add another apartment of the account, add the integration again: it offers only the apartments that are not set up yet.

There are no other options. When SBM refuses the saved login later, for example after you changed the password, Home Assistant shows a notification and asks you to enter the password again. You can change the username there too, if you sign in to the SBM Fizikus app with another one now; the account must have access to the apartment. Entering it for one apartment updates the other apartments of the same account too.

## Entities

Every thermostat is a device named after its room in SBM, linked to an **SBM Fizikus** device for the apartment. The SBM Fizikus device shows the firmware version that SBM reports, a new version at the next poll.

The names of all entities stay in English in every language. Home Assistant builds the entity IDs of new entities from their names in the language of the server, so the entity IDs are the same on every server, like `climate.living_room` and `sensor.living_room_humidity`.

| Entity | Enabled by default | Description |
|---|---|---|
| Climate | yes | Measured temperature and humidity, target temperature, heating or cooling, and whether the thermostat is active. |
| Humidity | yes | Measured relative humidity. |
| Dew point | no | Dew point calculated by SBM, rounded to 0.1 °C. |
| Temperature | no | Measured temperature. |
| Target temperature | no | Target temperature. |
| System mode | no | Diagnostic: `off` while the system is switched off, `heating` or `cooling` while the thermostat is active, otherwise `idle`. |
| Child lock | no | Configuration switch: locks the buttons of the thermostat, like the room card of the app. |

The **SBM Fizikus** device of the apartment has the switch of the system, and the outdoor weather that SBM reports for the building:

| Entity | Enabled by default | Description |
|---|---|---|
| System | yes | Switches the SBM Fizikus system of the apartment on or off, like the SBM Fizikus app. |
| Outdoor temperature | no | Outdoor temperature of the building. |
| Outdoor humidity | no | Outdoor relative humidity of the building. |
| Forward water temperature | no | Temperature of the water coming from the heat center. |
| Return water temperature | no | Temperature of the water going back. |
| Flow rate | no | Water flowing through the apartment, in l/h. |

- The **target temperature** can be set between 5 and 35 °C. SBM keeps it with 0.1 °C resolution: the card steps by 0.5 °C, and other values (for example from an automation) are rounded to 0.1 °C.
- **Heating or cooling** is set centrally for the whole building, so the HVAC mode can only be read. Choosing the other mode has no effect, so scenes set their target temperature also when they were saved in the other mode. While the SBM Fizikus system is switched off, the mode is `off`; switch the system on and off with the **System** switch. When the system is switched off for the whole building (in the app: "Épület szinten a rendszer kikapcsolva"), the mode is `off` too, and the **System** switch is unavailable, as the apartment cannot be switched then, like in the app. The climate entities do not switch it themselves, because that would switch every room, for example when an automation turns off one room for an open window. When SBM reports a mode the integration does not know, the state is `unknown`.
- The **outdoor sensors** need an extra request to SBM, so SBM is asked for the outdoor weather only while at least one of them is enabled. Enabling one reloads the integration after about 30 seconds. They do not depend on the thermostats: they stay available when SBM reports the thermostats as disconnected, and the thermostats stay available when the outdoor weather cannot be fetched.
- The entities are **unavailable** when SBM cannot be reached, when SBM reports the thermostats as disconnected, like the connection indicator of the app, or when a thermostat is missing from the answer of SBM.

## How the data is updated

The integration polls SBM every 2 minutes. After a new target temperature is set or the system is switched, it polls at once. SBM reports the new value as pending right away, so it shows up at once, while the thermostat or the device confirms it within 1–2 minutes. Thermostats added to the apartment later show up at the next poll, without a reload.

Every 10 minutes the integration also asks whether the system is switched off for the whole building. For that, after a start or reload it looks up once which building the apartment is in. While an outdoor sensor is enabled, every poll also asks SBM for the buildings of the account, which carry the outdoor weather.

## Example

Lower the target temperature of the living room at night:

```yaml
automation:
  - alias: "Living room: night temperature"
    triggers:
      - trigger: time
        at: "22:30:00"
    actions:
      - action: climate.set_temperature
        target:
          entity_id: climate.living_room
        data:
          temperature: 21
```

## Known limitations

- Every apartment has its own login and polling, also when they belong to the same account.
- With more than one apartment, the devices of the apartments can have the same names: every apartment has an SBM Fizikus device, and rooms are often called the same. Home Assistant then adds `_2` to the entity IDs of the apartment that is set up later. Rename the devices or the entities to tell them apart.
- Heating or cooling cannot be changed from Home Assistant. Choosing the other mode shows no error, nothing happens.
- A scene saved while the system was switched off (in `off` mode) fails while the system is on, and does not set its target temperature either: Home Assistant itself refuses `off` then. Save the scene again while the system is on.
- The integration sees a system switched off for the whole building within up to 10 minutes.
- Meters, energy and volume counters, the building-level child lock and the manual cooling disable are not available. Of the water values only the first channel is: SBM reports the second channel as not connected.

## Troubleshooting

- **Invalid username or password**: sign in to the SBM Fizikus app with the same username and password to check them.
- **Entities are unavailable**: check in the SBM Fizikus app whether the system is online. The log of Home Assistant says when SBM could not be reached, and when SBM reports the thermostats as disconnected, and also when these problems end.
- **Diagnostics**: on the page of the integration, the menu of the entry has **Download diagnostics**. It contains the latest answer of SBM, with the username and the password hash removed. Add it to bug reports.
- **Debug logs**: enable **Debug logging** on the page of the integration, or add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.sbm_fizikus: debug
  ```

  The log then shows every request to SBM with its answer, status and duration, the parsed state, and the cause of every failed call. The username, the password hash, the token and the address of the building stay out of it, but the names of the apartment and the thermostats are included.

A thermostat that SBM no longer reports can be deleted on its device page.

## Removal

1. Go to **Settings** > **Devices & services** > **SBM Fizikus**, open the menu of the entry, and select **Delete**.
2. To remove the files too, remove the integration in HACS (or delete `custom_components/sbm_fizikus`), and restart Home Assistant.

## Development

The tests use [uv](https://docs.astral.sh/uv/) and the Python version in `.python-version`:

```shell
uv sync
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest --cov --cov-report=term-missing
```

The tests use `pytest-homeassistant`, the test package of Home Assistant core. Home Assistant does not support Windows, so on Windows run them in WSL, or in Docker from PowerShell:

```shell
docker run --rm -it -v "${PWD}:/work" -w /work -e UV_PROJECT_ENVIRONMENT=/opt/venv ghcr.io/astral-sh/uv:python3.14-bookworm-slim sh -c "uv sync && uv run pytest --cov --cov-report=term-missing"
```

`pytest-homeassistant` pins the Home Assistant release it belongs to. Dependabot opens a monthly pull request to upgrade it; CI also runs the tests with the minimum Home Assistant release in `hacs.json`, and a weekly workflow with the newest beta.

### HACS repository metadata

HACS validation requires a description and topics in the repository's **About** settings on GitHub. Keep these settings when moving or recreating the repository:

- **Description:** Home Assistant custom integration for SBM Fizikus thermostats
- **Topics:** `home-assistant`, `custom-component`, `hacs`

These are GitHub repository settings, separate from `hacs.json` and Git commits. A validation failure caused by missing About metadata does not by itself indicate an installation problem.

## License

[GPL-3.0](LICENSE)
