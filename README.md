# Virtual fleet

Virtual fleet provides ability to simulate movement of multiple vehicles. Script will run one MQTT broker using VerneMQ and will launch a virtual vehicle, a module gateway, and an external server for each entry in the configuration JSON.

## Installation

```bash
git clone https://github.com/bringauto/virtual-fleet.git
cd virtual-fleet
pip install -r requirements.txt
```

## Command line arguments

* `--config=<string>` - path to the JSON configuration file. If not set, the default path `./config/virtual-fleet-config.json` is used.

## JSON configuration

The JSON configuration file should contain the following fields:

* `vernemq-docker-image` - MQTT broker docker image name/address
* `vernemq-docker-tag` - MQTT broker image tag
* `external-server-docker-image` - external server docker image name/address
* `external-server-docker-tag` - external server image tag
* `gateway-docker-image` - module gateway docker image name/address
* `gateway-docker-tag` - module gateway image tag
* `vehicle-docker-image` - virtual vehicle docker image name/address
* `vehicle-docker-tag` - virtual vehicle image tag
* `start-port` - starting port used for communication between virtual vehicle and it's module gateway, port will be iterated, so 8 virtual vehicles will use ports 42 000 - 42 007, in case that port is already used by other program it will skip it and try next one
* `vehicles` - array of vehicles to create
* `vehicles.name` - name of the vehicle, name must be unique and adhere to the regular expression ^[a-z0-9_]*$
* `vehicles.company` - name of company, that runs vehicle, name must adhere to the regular expression ^[a-z0-9_]*$

Example JSON configuration: [virtual-fleet-config.json](config/virtual-fleet-config.json)

## Usage

```bash
python3 VirtualFleetDocker.py --config=./config/virtual-fleet-config.json
```
