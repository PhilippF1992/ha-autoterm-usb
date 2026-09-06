# AUTOTERM / Planar Diesel Air Heater — Fault/Error Code Reference

**Target device:** AUTOTERM Air 4D
**Applicable range:** AUTOTERM AIR 2D / 4D / 8D / 9D and Planar 2D / 44D (shared fault-code table)
**Source manual:** Autoterm24 *AIR 2D–9D / Planar 2D–44D* installation & operation manual,
plus corroborating Autoterm/Planar Repair Manual and Comfort Control / PU-16-22-27 panel docs.

---

## How to read these codes

- **The official scheme uses TWO-DIGIT NUMERIC codes** (`01`, `02`, ... `33`, `34`), listed
  in "Table 2 – Fault Code / Description / Cause / Recommended Solution" of the manual.
- **`E01`/`E02` "E-codes" are NOT the manufacturer's native scheme.** They come from some
  third-party dealer pages that renumber faults. This reference uses the **official numeric codes**.
- **PU-5 simple panels** signal faults by a *number of red LED blinks*, which does **not** always
  equal the numeric code (e.g. code 13 = 2 blinks, code 31 = 14 blinks). Panels like Comfort
  Control / PU-16/22/27/28 show the numeric code or a text message directly.
- Over serial (our integration target), the fault code is expected to appear as a byte in the
  status (`0x0F`) response payload. **The mapping of raw status byte → this code table must be
  confirmed empirically** against the running heater; it has not yet been validated for our unit.

### Confidence legend
- ✅ Well-corroborated across ≥2 official-derived sources
- ⚠️ Partially corroborated (wording/scope varies by model or manual revision)
- ❓ Uncertain / single-source

---

## Fault Code Table

| Code | Title | Meaning | Cause & Remedy | Conf. |
|------|-------|---------|----------------|-------|
| **01** | Overheating of heat exchanger | Heat-exchanger overheat sensor detects excessive temperature (~>250 °C) and shuts down. | Blocked/restricted air inlet or outlet, blocked combustion-air/exhaust, soot, ice. Check heated-air in/out flow, combustion-air & exhaust for blockage/icing, fan integrity; clean exchanger; check/replace overheat sensor. | ✅ |
| **02** | Overheating at intake temperature sensor | Control-unit/intake sensor reads too high (~>55 °C) and shuts down. | Control unit not cooled during the mandatory purge, or poor ventilation. Ensure intake/outlet unobstructed and control unit ventilated; allow full purge/cool-down; replace control unit if it repeats. | ✅ |
| **03** | Flame failure during operation (flameout) | Flame lost while running. | Air in fuel line, insufficient fuel, faulty pump, faulty flame indicator. Check/bleed fuel supply; test pump; inspect/replace flame indicator. *(Some revisions treat two failed starts as code 13.)* | ⚠️ |
| **04** | Failure to ignite / start fault | No stable flame during start sequence. | Empty/low tank, air in line, blocked line/filter/kinked hose, faulty pump, wrong pump mounting. Check fuel level/line/filter/connections; bleed air; verify pump install. | ⚠️ |
| **05** | Faulty temperature sensor / flame indicator | HX temperature sensor (2D) or flame sensor detected faulty. | Short to casing or open circuit in wiring. Check sensor & wiring; repair or replace sensor/flame indicator. | ✅ |
| **06** | Faulty temperature sensor in control unit | Control-unit's integrated temperature sensor failed (not separately replaceable). | Internal sensor failure (overheat/moisture/defect). Replace the control unit. | ✅ |
| **07** | Overheat sensor — open circuit | Open circuit in the overheat/HX sensor circuit. | Defective sensor or oxidised/loose connector, broken wire. Check circuit for open/loose/corroded connectors; clean/repair; replace sensor if defective. | ✅ |
| **08** | Start failure (cross-ref code 29) | Reported as general "does not start" / points to fuel-supply fault. | No fuel / air in lines / fuel supply fault. See code 29: check fuel level, filter, line, pump; bleed air. | ⚠️ |
| **09** | Faulty glow plug | Glow plug / ignition element fault. | Short/open circuit, worn or contaminated plug, or control-unit fault. Check wiring & resistance; clean/replace glow plug; replace control unit if needed. | ✅ |
| **10** | Glow plug circuit fault | Fault in the glow-plug drive circuit (vs the plug itself). | Wiring/connector fault or control-unit driver fault. Inspect wiring/connectors; test continuity; replace plug or control unit. | ⚠️ |
| **11** | Flame indicator / intake sensor fault | Flame indicator (or upper intake temp sensor, model-dependent) faulty. | Sensor malfunction or wiring open/short. Check sensor & wiring; on integrated-sensor models replace control unit. | ⚠️ |
| **12** | Temperature sensor / flame indicator fault (variant) | On some models duplicates code 05. | Same as code 05. | ⚠️ |
| **13** | Does not start — two auto start attempts failed | Two automatic start attempts both failed. *(PU-5 = 2 blinks.)* | No fuel, air in line, clogged filter/line, blocked exhaust/combustion-air intake, low voltage, pump fault. Check fuel & lines/filter; bleed; check exhaust & intake; check voltage; test pump. | ✅ |
| **16** | Undervoltage (heater blocked) | Supply below permitted minimum; shuts down to protect electronics/motor. | Weak/discharged battery; undersized/long/corroded wiring causing voltage drop. Measure voltage at heater terminals (~10.5–15 V for 12 V units); check battery, charging, cables, grounds. | ✅ |
| **17** | Overvoltage | Supply above permitted maximum (>~16 V on 12 V, >~30 V on 24 V); shuts down. | Faulty regulator/alternator or wrong supply voltage. Measure voltage; check regulator/charging & battery; bring supply within spec. | ✅ |
| **20** | Control-unit fault | General control-unit fault / "Service!" condition. | Control-unit internal fault. Diagnose per repair manual; replace control unit if confirmed. | ⚠️ |
| **27** | Motor does not rotate (fan/rotor blocked) | Combustion-air fan / blower motor not rotating. | Mechanical blockage, debris, seized bearing, or faulty motor. Check fan turns freely & air path clear; remove obstruction; replace fan/motor if defective. | ✅ |
| **28** | Motor overspeed / incorrect fan RPM | Blower running outside required speed range. | Motor fault, speed-feedback (Hall/tacho) fault, or mechanical drag. Inspect motor & feedback; check for debris; replace motor if out of spec. | ⚠️ |
| **29** | Flame goes out / fuel supply fault | Flameout due to fuel-supply problem. | Air in fuel system, insufficient fuel, faulty pump, faulty flame indicator. Check fuel level/lines/filter/connections; bleed; test/replace pump; inspect flame indicator. | ✅ |
| **30** | Does not start — no communication | No communication between panel/controller and control unit. | Damaged/disconnected harness, oxidised connectors, broken data (white) wire, faulty controller or control unit. Check connectors & data wire; clean oxidation; test controller & harness; replace control unit if controller OK. | ✅ |
| **31** | Overheating of hot-air outlet sensor | Hot-air (outlet) temperature sensor signals overheat and shuts down. *(PU-5 = 14 blinks.)* | Restricted heated-air flow (blocked outlet/ducting) or outlet sensor fault. Check in/out airflow; check hot-air temperature sensor; replace if faulty. | ✅ |
| **32** | Temperature sensor malfunction (won't start) | Heater refuses to start due to a faulty temperature sensor (may show as 32 or 11). | Faulty temperature sensor/wiring. Diagnose sensors & wiring; repair/replace; heater won't ignite until resolved. | ✅ |
| **33** | Heater locked (lockout) | Heater blocked after repeated critical faults; normal start prevented; panel shows **33**. | Multiple consecutive failures (repeated overheat/ignition faults). Eliminate the underlying fault first, then perform the manual **unlock procedure** (see manual). | ✅ |
| **34** | Communication / connection fault (variant) | Reported on some panels as a comms/connection error similar to code 30. | Harness/connector/data-wire fault. Check harness, connectors, and data wire; test controller & control unit. | ❓ |

---

## Notes for the integration

- **Do not auto-restart on a fault.** Report the fault code and require an explicit user
  acknowledgement. Repeated automatic restarts on faults like 04/13/29 risk fuel flooding.
- **Code 33 (lockout) is special:** it will not clear by a normal start command; it needs the
  documented unlock procedure. The integration should surface this distinctly.
- **Voltage faults (16/17)** are environmental (battery/charging) rather than heater defects —
  useful to correlate with the Victron/Daly data already on this Pi.
- **Byte mapping TODO:** confirm which status-payload byte carries the fault code, and whether
  the on-wire value equals the numeric code above or is an internal enum, by capturing a real
  fault (or by cross-referencing the schroeder-robert / prclm reference implementations).

## Sources & confidence

- Primary: Autoterm24 AIR 2D–9D / Planar 2D–44D installation/operation manual.
- Corroborating: Autoterm/Planar Repair Manual (`air-repair-manual-en`), Comfort Control /
  PU-16-22-27 panel manuals, PF Jones & compactliving Planar fault-code tables, servitek.no sheet.
- Entries marked ⚠️/❓ vary by model or manual revision and should be verified against your
  specific Air 4D firmware before being treated as authoritative.