# OpenHasp - OpenHasp Device Integration

![OpenHasp Icon](static/OpenHasp.png)

MQTT-based integration with OpenHasp devices (HASP - Home Assistant Smart Panel) for managing touchscreen panels.

## Description

The `OpenHasp` module provides integration with OpenHasp devices for the osysHome platform. It enables control and monitoring of HASP touchscreen panels via MQTT protocol.

## Main Features

- ✅ **MQTT Integration**: MQTT-based device communication
- ✅ **Panel Management**: Manage HASP panels
- ✅ **Page Control**: Control panel pages and content
- ✅ **Property Linking**: Link panel elements to object properties
- ✅ **Template Support**: Jinja2 template support for dynamic content
- ✅ **Search Integration**: Search devices and panels

## Admin Panel

The module provides an admin interface for:
- Viewing HASP devices
- Configuring panel settings
- Managing panel pages
- Linking elements to properties

## Configuration

- **MQTT Broker**: MQTT broker connection settings
- **Topic**: MQTT topic prefix for devices
- **Device Settings**: Panel configuration and pages

## Usage

### Adding HASP Device

1. Navigate to OpenHasp module
2. Click "Add Device"
3. Configure MQTT settings
4. Set up panel pages
5. Link elements to object properties

## Technical Details

- **Protocol**: MQTT
- **Device Type**: OpenHasp/HASP panels
- **Templates**: Jinja2 template engine
- **Page Management**: Dynamic page reloading

## Version

Current version: **1.0**

## Category

Devices

## Actions

The module provides the following actions:
- `cycle` - Background MQTT communication
- `search` - Search devices and panels

## Requirements

- Flask
- paho-mqtt
- SQLAlchemy
- osysHome core system

## Author

osysHome Team

## License

See the main osysHome project license

