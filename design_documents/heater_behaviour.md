# Heater Behaviour

## Settings

Settings are in general only able to change when the heater is in idle stage. 

## Modes

There are two general modes: heating and ventilation.

### Heating
The heater has two general heating modes: 

- by Temp
  - by internal (measured at air intake) 
  - by panel (communicated via uart from panel or homeassistant)
  - by external (if an external temp is available and connected to the heater itself)
  - settings:
    - the power level is adjusted automatically by the heater
    - temp settings can be changed while the heater is running 
    - the target temp source (internal, panel, external) cannot be changed when the heater is running
    - no other setting can be adjusted
- by Power 
  - levels 1 to 9
  - settings:
    - levels can be adjusted while the heater is running 
    - no other setting can be adjusted 

General:
- The heating mode cannot be changed when the heater is running!
- Fan speed is set automatically by all heating modes, it cannot be adjusted! 

### Ventilation

- the fan speed can be adjusted while the ventilation mode is running
- all other setting changes are irrelevant for this mode. 

## Conclusion

### Heating

To work around the limitations of the heating modes and the settings adjustable while running, the heating by temp mode should be concluded on the controller to a "by temp" mode. 

Instead of having basically 4 (Temp by internal, external panel & Power mode) modes, homeassistant should only use two (Temp by panel & Power mode) and handle the other 2 (Temp by external, internal) internally. 

The three modes (Temp by internal, external, panel) that are based on temperature should always use the "by panel" mode and homeassistant is sending the correct temp from the different sources on demand as the panel temperature. 

Leaving us with two modes:
- by Temp
  - Heater is started with "Temp by Panel"
  - Homeassistant offers a "Temp source" field to select 
    - internal
    - external
    - panel
  - Homeassistant sends the Temp of the selected source as Panel-Temp to the heater
- by Power
  - Homeassistant sends the correct power level
  - no further adjustmend needed

The integration should check if an external temp-sensor is attached to the heater, if not, the related entities should be disabled and the select field "Temp source" should not offer this option.