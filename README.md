# Optolink Switch/Splitter
Make your Viessmann heating locally available via MQTT and TCP/IP while keeping Optolink/ViCare App & more!

![System Architecture](https://github.com/philippoo66/optolink-splitter/assets/122479122/10185cc5-0eed-4bc3-a8d7-b385c4e73aaf)

**Use this software at your own risk.**

For latest developments always check the [Version-Log](https://github.com/philippoo66/optolink-splitter/wiki/990-Version-Log)

## 🎉 Announcements
- [**Version 1.11.4.0**](https://github.com/philippoo66/optolink-splitter/wiki/990-Version-Log#version-11140) **Schedulings clear text write using /set** Now possible to write schedulings (heating, hot water, DHW circulation) using clear text with /set feature:<br>
e.g. topic `vito/circulation_monday/set`, <br>
payload `8:00-9:00 *3, 12:00-13:00 *1, 19:30-22:00 *2`

- [**Version 1.11.0.0**](https://github.com/philippoo66/optolink-splitter/wiki/990-Version-Log#version-11100) **Poll cycle groups** implemented and the possibility to alter the cycles at run-time using action command `setpollcycle;<groupkey>;<value>`<br>
Since V1.10.1 the entire Optolink communikation with the heating device can get logged. Up from V1.10.5 a 'global re-start loop' got implemented to restart the splitter in case of problems.

- [**Version 1.10.0.0**](https://github.com/philippoo66/optolink-splitter/wiki/990-Version-Log#version-11000) **New Feature: User-Friendly MQTT /set Topics!** Write values using the same format they're published in! Example: Publish `vito/c1_temp_room_setpoint/set` with payload `21.5` instead of complex command syntax. Supports ON/OFF, boolean, and numeric values with automatic scaling. Thank you @manuboek!!


- Need **VS1 / KW protocol support**? It got implemented in the main tree since V1.8.0.0. Just set `vs1protocol = True`<br>
Still **TESTERS WANTED with KW device and Vitoconnnect!**

- minimal Optolink Adapter confirmed:

![grafik](https://github.com/user-attachments/assets/e2566c68-b461-403b-918e-fa5774c71b5d)



## 📌 Table of Contents
- [Introduction](#rocket-introduction)
- [Software Requirements](#file_folder-software-requirements)
- [Hardware Requirements](#desktop_computerhardware-requirements)
- [Installation](#hammer_and_wrench-installation)
- [Updating to a new Version](#updating-to-a-new-version)
- [Connecting Optolink & Vitoconnect](#electric_plug-connecting-optolink--vitoconnect)
- [Command Syntax: MQTT & TCP/IP](#receipt-command-syntax-mqtt--tcpip)
- [User-Friendly MQTT /set Topics](#pushpin-user-friendly-mqtt-set-topics)
- [Smart Home Integration (e.g. Home Assistant)](#house-smart-home-integration-eg-home-assistant)
- [Questions & Issues](#interrobang-questions--issues)
- [3D-Printable Case for Raspberry Pi & USB-TTL Adapter](#printer-3d-printable-case-for-raspberry-pi--usb-ttl-adapter)
- [Additional Images](#camera_flash-additional-images-mqtt-visualization)
- [Disclaimer](#memo-disclaimer)


## :rocket: Introduction

### Key Benefits 
- **Local Control and Cloud Capability** – Allow full local  control and datapoint access while retaining the ability of Viessmann Cloud and App access.
- **Viessmann Heating/Heat Pump Compatibility** – Works with Vitodens, Vitocal, Vitocrossal and most other Optolink featured devices.
- **Smart Home Ready** – Integrates with **Home Assistant**, **ioBroker**, **Node-RED** or any other system with MQTT Support.
- Supports **VS2/300 and VS1/KW protocols** now within a single branch.  
  - with VS1/KW devices/protocol probably Vitoconnect might not work. **TESTER WANTED with KW device and Vitoconnect!**  
  - For **VS1/KW with Vitoconnect**, the [viconn-listener](https://github.com/philippoo66/viconn-listener) is available (currently in beta).

### Videos
🎥 **Introduction Video (German):** [Watch on YouTube](https://youtu.be/95WIPFBxMsc)

📖 **Extended Setup Tutorial (German):** [Rustimation Blog](https://www.rustimation.eu/index.php/category/iot/viessmann-ohne-api/)

## :file_folder: Software Requirements
- Python (version >= 3.9):
  - pyserial: `pip install pyserial`
  - MQTT (version >= 2.0): `pip install paho-mqtt` 
- Virtual environment recommended ([Guide](https://github.com/philippoo66/optolink-splitter/wiki/510-error:-externally%E2%80%90managed%E2%80%90environment-%E2%80%90%E2%80%90-venv)).

## :desktop_computer:	Hardware Requirements
- Raspberry Pi or a suitable system.
- Viessmann Optolink-compatible heating (Vitodens, Vitocal, Vitocrossal, etc.).
- Optolink r/w head:
  - Original Viessmann model or self-made.
  - Volkszaehler-compatible versions may work if LED distance is adjusted ([8€ option](https://www.ebay.de/itm/285350331996)).
- To retain **ViCare App functionality**, use a USB-to-TTL adapter:
  - Recommended: CP2102 chip ([Example](https://www.google.com/search?q=cp2102+usb+ttl)).
  - Some newer Vitoconnect models may work with FTDI chips.
  - **Raspberry Pi UART voltage = 3.3V** → Set jumper accordingly!

## :hammer_and_wrench: Installation
### 1. Clone the Repository
```sh
git clone https://github.com/philippoo66/optolink-splitter.git
cd optolink-splitter
```

Instead of using the clone command (requiring git) you may download the files (usually as zip) and copy them into your self-created optolink-splitter folder.

### 2. Create Virtual Environment & Install Dependencies

Using a virtual environment is recommended to keep dependencies isolated and avoid conflicts with system-wide packages. More details can be found in [this guide](https://github.com/philippoo66/optolink-splitter/wiki/510-error:-externally%E2%80%90managed%E2%80%90environment-%E2%80%90%E2%80%90-venv).

#### 2.1. Create & activate the virtual environment:
```sh
python3 -m venv myvenv
source myvenv/bin/activate  # On Windows use: myvenv\Scripts\activate
```

#### 2.2. Install required dependencies:
```sh
pip install pyserial
pip install paho-mqtt  # Only if MQTT is used
```
*NOTE:* After installation, the environment must be activated before running the script.

### 3. Configure the Settings
On initial install
- copy `settings_ini.py.example` to `settings_ini.py`. Modify `settings_ini.py` according to your heating device. 
- copy `poll_list.py.example` to `poll_list.py` and modify it according to your desired datapoints.

If you're using **Home Assistant** consider to utilize `homeassistant_poll_list.py` for combined datapoints and entities definition (see [Wiki for details](https://github.com/philippoo66/optolink-splitter/wiki/211-Alternative-Home-Assistant-Integration))!

Regading datapoints refer  
- to [Wiki | Parameter Addresses](https://github.com/philippoo66/optolink-splitter/wiki/310-Parameter-Addresses), [poll_list samples](https://github.com/philippoo66/optolink-splitter/wiki/350-Poll-Configuration-Samples)
- to [Wiki | ViessData21](https://github.com/philippoo66/ViessData21?tab=readme-ov-file#dp_listen_2zip)

### 4. Run the Script
```sh
source myvenv/bin/activate  # Make sure to activate the virtual environment. On Windows use: venv\Scripts\activate
python3 optolinkvs2_switch.py
```
For automatic startup, set up a service. See the [Wiki Guide](https://github.com/philippoo66/optolink-splitter/wiki/120-optolinkvs2_switch-automatisch-starten).

## Updating to a new Version
If you want to update your installation to a new version, the recommended way is to
- make a backup copy of your current installation (folder)
- from the new version repo, clone **all files** into your original folder (replace existing files). If you clone-d the files initially, you may use `git pull` to get the new version.

It is not necessary anymore to add new settings to your settings_ini since there are default values used for every setting. Also your settings_ini.py, poll_list.py and homeassistant_poll_list.py will not get overwritten anymore since all those files got the extension .example in the repo since V1.9. 

Don't forget to restart the script / the service afterwards.

Even though I promise to note required changes carefully in the version log from now on, after an update it is always a good practice to start the splitter once in the console (`python optolinkvs2_switch.py`, remember to run your virtual environment) since in the console you get quite detailed error messages _if_ some would be wrong or missing.

## :electric_plug: Connecting Optolink & Vitoconnect
### Parallel use with Vitoconnect / ViCare App
- Ensure the **serial port is enabled** and **serial console is disabled** ([Guide](https://github.com/philippoo66/optolink-splitter/wiki/050-Prepare:-enable-serial-port,-disable-serial-console)).
- **CP2102 Interface:**
  - **Cross RX/TX** lines.
  - Set voltage jumper to **3.3V**.
  - Use `ttyAMA0` instead of `ttyS0` for Raspberry Pi 3+ ([Details](https://github.com/philippoo66/optolink-splitter/wiki/520-termios.error:-(22,-'Invalid-argument'))).

### Vitoconnect Interfaces
#### **Vitoconnect 100 OPTO1**
To ensure proper communication with the system, follow the power-on sequence exactly:
1. **Connect all wires and plugs** to the Raspberry Pi and Vitoconnect.
2. **Power on the Raspberry Pi**.
3. Run the script and wait for the prompt: `awaiting VS2...`.
4. **Power on Vitoconnect 100 OPTO1**.

#### **Vitoconnect OPTO2**
The startup sequence for this device is not critical, as it will automatically reconnect without issues.

## :receipt: Command Syntax: MQTT & TCP/IP
Optolink Splitter can connect to an **MQTT Broker** for sending commands and receiving responses. Alternatively, a direct **TCP/IP connection** (e.g. using PuTTY) can be established to interact with the application directly. For more details on the command syntax, see the [Wiki | Command Syntax Overview](https://github.com/philippoo66/optolink-splitter/wiki/010-Command-Syntax) or go directly to the section on [MQTT and TCP/IP requests](https://github.com/philippoo66/optolink-splitter/wiki/010-Command-Syntax#command-syntax-for-requests-via-mqtt-and-tcpip).


  - read ambient temperature, scaled with sign:
    - cmnd = read;0x0800;2;0.1;true
    - resp: 1;2048;8.2

  - read DeviceIdent as raw:
    - cmnd = read;0xf8;8
    - resp: 1;248;20CB1FC900000114

  - write hotwater temperature setpoint:
    - cmnd = write;0x6300;1;45
    - resp: 1;25344;45

**Note for TCP/IP Connections:**  
You may close the TCP session by sending `exit` as a string.

## :pushpin: User-Friendly MQTT /set Topics
In addition to the command syntax above, you can now write values using simple MQTT topics.
For every datapoint published to MQTT (e.g., `vito/hk1_normal_temperature`), you can write to it using a `/set` suffix:
```bash
# Reading (automatic, via poll_list)
Topic: vito/hk1_normal_temperature
Payload: 20.5

# Writing (simple and intuitive!)
Topic: vito/hk1_normal_temperature/set
Payload: 21.0
```

**Quick Examples:**
```bash
# Set room temperature (automatic scaling applied)
mosquitto_pub -t "vito/hk1_normal_temperature/set" -m "21.5"

# State on/off
mosquitto_pub -t "vito/hk1_partymode/set" -m "ON"
mosquitto_pub -t "vito/hk1_partymode/set" -m "OFF"

# Set hot water temperature
mosquitto_pub -t "vito/hotwater_temperature/set" -m "50"

# run circulation pump 
# 8:00-9:00 continuously (function code 3), 
# 12:00-13:00 5 minutes every half hour (function code 1)
# 19:30-22:00 5 minutes every 15 minutes (function code 2)
mosquitto_pub -h your-broker -t "Vito/dhw_circulation_monday/set" -m "8:00-9:00 *3, 12:00-13:00 *1, 19:30-22:00 *2"
```

MQTT Explorer example:

![grafik](https://private-user-images.githubusercontent.com/122479122/553739544-540e3710-d11b-4fe5-95b4-2bd234fac0f3.png?jwt=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiJnaXRodWIuY29tIiwiYXVkIjoicmF3LmdpdGh1YnVzZXJjb250ZW50LmNvbSIsImtleSI6ImtleTUiLCJleHAiOjE3NzUyMDI1NjMsIm5iZiI6MTc3NTIwMjI2MywicGF0aCI6Ii8xMjI0NzkxMjIvNTUzNzM5NTQ0LTU0MGUzNzEwLWQxMWItNGZlNS05NWI0LTJiZDIzNGZhYzBmMy5wbmc_WC1BbXotQWxnb3JpdGhtPUFXUzQtSE1BQy1TSEEyNTYmWC1BbXotQ3JlZGVudGlhbD1BS0lBVkNPRFlMU0E1M1BRSzRaQSUyRjIwMjYwNDAzJTJGdXMtZWFzdC0xJTJGczMlMkZhd3M0X3JlcXVlc3QmWC1BbXotRGF0ZT0yMDI2MDQwM1QwNzQ0MjNaJlgtQW16LUV4cGlyZXM9MzAwJlgtQW16LVNpZ25hdHVyZT0wMWM1MDZjMTQ3ZTEzZjM4N2Q3NzJmZTI1Y2UxZGZmYjhiNWMyZjY5Y2M2Mzg2NjA3MDJlYzE5ZmZkZTRmNWVlJlgtQW16LVNpZ25lZEhlYWRlcnM9aG9zdCJ9.U6X2Ct3OeDpWx38iLpsqv1PbUDC_RTM-ys-nvS5QJRM)

For complete documentation with more examples, see **[the wiki page on /set topics](https://github.com/philippoo66/optolink-splitter/wiki/012-MQTT--set-Topics-%E2%80%90-User%E2%80%90Friendly-Write-Feature)**.

## :house: Smart Home Integration (e.g. Home Assistant)
Optolink Splitter seamlessly integrates into your smart home setup via **MQTT**, allowing you to monitor and control your Viessmann heating system using platforms like **Home Assistant**, **ioBroker**, or **Node-RED**. All available heating system data can be visualized in dashboards, automated with custom rules, and integrated into broader smart home routines. With Optolink Splitter’s command-sending capability, you can locally adjust heating modes, temperature setpoints, or pump states directly from your favorite smart home system.

For detailed **Home Assistant integration instructions**, see the [Wiki | Home Assistant Integration of Optolink‐Splitter](https://github.com/philippoo66/optolink-splitter/wiki/210-Home-Assistant-Integration-of-Optolink%E2%80%90Splitter).  

Below are examples of how this integration looks in different smart home environments:

### Heating System Overview in Home Assistant (Built with a Picture Entity Card)
![grafik](https://github.com/user-attachments/assets/28901428-5cec-4135-aada-795302d58811)

[Details](https://github.com/philippoo66/optolink-splitter/discussions/67)

### Another Heating System Overview in Home Assistant & Command Buttons
![0b87f133-3285-4cb5-871c-87c66598d42d](https://github.com/user-attachments/assets/596c2f3d-24c3-406a-854b-4679ce0643d7)

## :interrobang: Questions & Issues
- Discussions & contact: [GitHub Discussions](https://github.com/philippoo66/optolink-splitter/discussions)
- Bug reports: [GitHub Issues](https://github.com/philippoo66/optolink-splitter/issues)

## :printer: 3D-Printable Case for Raspberry Pi & USB-TTL Adapter
A compact and practical 3D-printable case designed for Raspberry Pi 2 & 3 including a CP2102 USB-TTL adapter mount.  A version for **Raspberry Pi 4** may be available in the future. Special thanks to [Kristian](https://github.com/kristian)!  

[Raspberry Pi 2/3 Case with CP2102 UART Board Holder](https://www.printables.com/model/1144565-raspberry-pi-3-b-sleeve-case-w-cp2102-holder-wall)  

## :camera_flash: Additional Images (MQTT, Visualization)
### Datapoints in settings_ini.py & MQTT Explorer monitoring
![grafik](https://github.com/philippoo66/optolink-splitter/assets/122479122/82618777-af8b-492d-8669-e755a1172d80)
 
### Data visualisation
![grafik](https://github.com/user-attachments/assets/fee2151f-7d99-45a0-a85a-897b54085289)

## :memo: Disclaimer
This software is **not affiliated with, endorsed by, or associated with Viessmann** in any way. The terms Vitoconnect, Optolink, Vitocal, ViCare, etc. refer to Viessmann products and technologies. All product and brand names mentioned belong to their respective owners.<br>
It is provided **as-is**, without any warranties or guarantees. The authors assume no liability for any issues arising from its use. 



## Local Raspberry Pi robustness changes

The local fork keeps the existing VS1/VS2, Vitoconnect, MQTT and TCP interfaces. No additional runtime services are required. Use a stable serial path such as `/dev/serial/by-id/...` for a USB adapter; do not automatically select another device when the configured adapter is missing.

Optional settings in `settings_ini.py` (defaults in `settings_ini.py.example`):

- `usb_reconnect_forever = True`: retry a disconnected serial adapter with capped exponential backoff; `restart_delay` and `restart_max_delay` control the delay.
- `poll_read_retries = 1`: retry transient read errors only, never writes; 0..3.
- `poll_stale_after = 0`: derive the freshness deadline from the configured polling interval and cycle group, or specify a positive number of seconds. Once-only datapoints do not expire by time but are invalidated on reconnect.
- `set_write_allowlist = None`: preserve legacy access; use a set of explicitly writable datapoint names (or an empty set to disable /set). Use `cmnd_write_allowed = False` to disable generic raw writes and `write_limits` to constrain numeric values.
- `tcp_legacy_idle = 0.1`: legacy unframed commands use an idle gap. Use `None` to require reliable newline/CR/NUL-delimited messages. TCP should be bound to a trusted interface.

MQTT keeps the original data topics and combined `<base>/LWT`. The additional `<base>/status/optolink` reports protocol readiness while MQTT is connected; `stats` reports both connection states. Each polling datapoint publishes retained `<base>/quality/<name>` (online/offline) and `<base>/quality/<name>/last_success` (Unix timestamp or null). Re-run the existing Home Assistant discovery publisher for new per-datapoint availability on supported single-state entities. Existing discovery configurations remain usable. The combined LWT remains authoritative after a broker disconnect because MQTT permits only one Last Will per client.

Hardware-free checks: `python -m compileall -q .` and `python -m unittest discover -s tests -v`. Validate the serial protocol and actual Raspberry Pi hardware before production deployment.


## Advanced branch: local Control Center

The optional advanced modules run inside the splitter, without new runtime packages. They do not add automatic heating control. The dashboard is read-only and defaults to `http://127.0.0.1:8080/` on the Pi. For secure remote access use `ssh -L 8080:127.0.0.1:8080 pi@<pi-ip>`, then open `http://localhost:8080/` on your computer. To enable LAN access explicitly, set `web_host` to the Pi's LAN address and provide the service environment variable `OPTOLINK_WEB_PASSWORD` (minimum 12 characters; username `optolink`). HTTP Basic authentication is not encrypted; use only a trusted isolated LAN or an existing TLS reverse proxy. Never expose this port publicly. No web write endpoints exist. The HTTP API is `GET /api/status`.

All settings are in the `advanced` dictionary in `settings_ini.py.example`. Restart the service after changing these settings. The existing poll list and MQTT topics are preserved. The scheduler takes ownership only of explicitly configured override names with positive legacy cycle groups. Other points follow legacy cycles; errors on managed points cause cooldown and repeated device errors are marked suspected unsupported, not deleted. Existing same-address byte/bit coalescing remains for legacy polls. Bus-load estimation is for override jobs only, not total physical bus utilization. No speculative reads spanning unverified memory addresses are made.

To enable heating statistics map the actual poll-list names in `heating_names` using roles `burner` (0/1), `modulation` (percent), `boiler`, `boiler_target`, `flow`, `return` (temperatures in °C). Choose short enough read intervals; missing or old observations are not extrapolated. The starts today and rolling counts are observation-based since service start, not the controller's lifetime counters. The local history is bounded and RAM-only. The unchanged-value rule is a warning, not evidence of a broken sensor. Set plausibility ranges only for documented values/units.

The device identification reads four bytes at 0x00f8 and reports raw hex; model/firmware labels require a verified user-provided mapping. `firmware_datapoint` can specify a verified read-only address and length; it reports raw hex. Profiles use the exact fingerprint as a key; example shape (placeholders, NOT real device values):

```python
# Replace with actual verified fingerprint, name, address and raw value limits.
# Do not copy arbitrary addresses into a production profile.
# advanced["device_profiles"] = {
#     "00000000": {"name": "verified model", "firmware": "verified version",
#                  "expected_datapoints": ["exact poll name"],
#                  "writable": {"1234": {"length": 1, "raw_limits": (0, 0), "signed": False}}}
# }
```

`read_only` blocks MQTT/TCP writes, including opaque raw/request messages (Vitoconnect pass-through is intentionally unaffected). `verified_writes` is opt-in for readback-compatible writes: pre-read, suppress unchanged writes, rate limit, issue once, read back, compare, audit UUID. With verification or profile enforcement enabled opaque commands are rejected. A matching profile restricts explicit writes to its raw bounds; use `require_profile_for_writes` to fail closed on unknown devices. Existing `cmnd_write_allowed`, `set_write_allowlist` and `write_limits` still apply. No automatic retry/rollback of writes. A verification error means the outcome is uncertain, not that the write was undone.

Advanced telemetry is published under `<base>/advanced/{system,communication,heating,queues,device}` as JSON, with numeric subtopics and `<base>/advanced/errors`. Optional `mqtt_discovery` creates diagnostic sensors. Data freshness is still provided by the robustness branch; HTTP shows invalid values explicitly. A 100-entry error ring and 100-entry write audit are kept in RAM. The serial I/O counts refer to calls/bytes; transaction counters measure secondary/poll requests and forwarded responses, not physical frame counts. Verify actual device compatibility on the Raspberry Pi before deployment.


### Extensible datapoint catalogs

Copy additional Vitosoft TSV exports into `profiles/` and restart the splitter. Each list is discovered by its Model column and appears in the web Data Point Explorer, even without a corresponding JSON profile. The first six TSV columns must be Model, Parameter, Address, Conversion, ReadWrite, ByteLength. Original files are retained unmodified; the explorer shows the reliably aligned metadata. Multiple definitions at one address are deliberately retained. R/W in a catalog describes source metadata, NOT permission to write; no catalog entry automatically adds polls or enables writes.

For automatic read-only role mapping, add a JSON manifest named after the profile (see `profiles/VScotHO1_72.json`). It defines the two-byte device ID, inclusive two-byte version range, exact existing poll-list names, addresses, lengths, factors, optional filters and signedness. Overlapping matches are rejected. Explicit advanced.heating_names and advanced.burner_states override automatic mappings. Catalog profiles never populate the write allowlist. Return temperature is deliberately not inferred from the flue temperature or an unconfirmed calculated value. A verified return sensor can be mapped explicitly. The internal start counter counts attempts; observed flame transitions are counted separately. The supplied profile references the local catalog [profiles/vitosoft_VScotHO1_72.tsv](profiles/vitosoft_VScotHO1_72.tsv); 0x55DE=0x0B was observed during firing, 0=off is documented but still requires display confirmation. Only existing compatible active poll entries are mapped; no new heater addresses are polled by the catalog except the firmware index read for the matching HO1 profile.
