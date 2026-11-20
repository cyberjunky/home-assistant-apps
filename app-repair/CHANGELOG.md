# Changelog

## 0.1.0

**After updating:** nothing to do. Open **Repair** from the sidebar; the first scan runs by itself.

- First version: looks Home Assistant over every half hour and lists what is wrong, each finding with what it is, why the app thinks so, and what to do. Devices and entities that don't answer, integrations that failed to start or lost their login, faulty automations, things left behind, broken references in automations and dashboards, statistics problems, Home Assistant's own repairs, the log sorted by what each line is about, and the Supervisor's findings. Repairs where Home Assistant's API allows one: reload an integration, disable or remove an entity, remove a device, delete statistics, and a Clean up page that removes an entity or device with its history and statistics.
