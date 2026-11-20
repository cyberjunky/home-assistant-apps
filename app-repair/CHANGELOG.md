# Changelog

## 0.1.1

**After updating:** nothing to do.

- **Removing a device left the device behind:** its entities and their history were removed, and then Home Assistant refused to remove the device itself, for example a Zigbee device. Whether a device can be removed is now checked first; where it can't, the removal isn't offered and nothing is touched.
- **Zigbee devices are removed by ZHA itself,** from the Zigbee network and from Home Assistant, as ZHA's own Remove does. Other devices go the way Home Assistant's Delete button does; the command used before is going away in Home Assistant.
- **Pick which history to delete:** on Clean up, every entity the deep scan found as gone has a tick box, with a filter and tick-all-shown. Only the ticked ones are deleted.
- **A refused request is not a finding:** an "Error handling message" in the log was a request Home Assistant said no to, told to whoever sent it. It stays on the Log page.

## 0.1.0

**After updating:** nothing to do. Open **Repair** from the sidebar; the first scan runs by itself.

- First version: looks Home Assistant over every half hour and lists what is wrong, each finding with what it is, why the app thinks so, and what to do. Devices and entities that don't answer, integrations that failed to start or lost their login, faulty automations, things left behind, broken references in automations and dashboards, statistics problems, Home Assistant's own repairs, the log sorted by what each line is about, and the Supervisor's findings. Repairs where Home Assistant's API allows one: reload an integration, disable or remove an entity, remove a device, delete statistics, and a Clean up page that removes an entity or device with its history and statistics.
