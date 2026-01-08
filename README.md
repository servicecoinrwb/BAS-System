Sequence of Operations
RTU Control Logic Description

Close
1. Occupancy & Scheduling
The unit determines occupancy based on the assigned weekly schedule. Holiday exceptions override the weekly schedule to "Unoccupied".

Occupied Mode: Supply Fan runs continuously to provide ventilation. Temperature setpoints are tighter (e.g., 74°F Cool / 68°F Heat).
Unoccupied Mode: Supply Fan cycles on only when there is a call for heating or cooling. Temperature setpoints are relaxed (e.g., 85°F Cool / 60°F Heat) to save energy.
2. Cooling Mode
Cooling is enabled when the Zone Temperature rises above the active Cooling Setpoint plus the Deadband (default 2°F).

Economizer (Free Cooling)
If the Outdoor Air Temperature is below the Cooling Setpoint - Economizer Differential (default 5°F), the unit enters Economizer Mode.

Mechanical Cooling (Compressors) are LOCKED OUT.
Outside Air Damper modulates to 100% open.
Mechanical Cooling
If outdoor conditions are not suitable for economizing, the unit activates mechanical cooling stages (Y1).

3. Heating Mode
Heating (W1) is enabled when the Zone Temperature drops below the active Heating Setpoint minus the Deadband. The Supply Fan turns on (if not already running) and the heat source activates.

4. Demand Control Ventilation (DCV)
If a CO2 sensor is installed, the outside air damper will modulate to maintain air quality during Occupied periods.

Base Ventilation: Damper stays at Minimum Position (default 20%) when Occupied.
Active Control: If CO2 rises above the Target (800 PPM), the damper modulates open proportionally towards 100% to flush the space with fresh air.
5. Safeties & Alarms
Fan Failure: If the Fan Command is ON but the Fan Status switch remains OFF for >30 seconds, the unit shuts down and triggers a "FAN FAIL" alarm.
Freeze Protection: If Discharge Air Temp drops below 40°F, a Low Temp alarm is generated.
Emergency Stop: A global software or hardware kill-switch immediately disables all outputs.
