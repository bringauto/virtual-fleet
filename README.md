# Virtual fleet

The virtual fleet provides the ability to simulate the movement of multiple vehicles. The script will run one MQTT broker using VerneMQ and will launch a virtual vehicle, a module gateway, and an external server for each entry in the configuration JSON

## Installation

```bash
git clone https://github.com/bringauto/virtual-fleet.git
cd virtual-fleet
pip install -r requirements.txt
```

## Command line arguments

* `--config=<string>` - path to the JSON configuration file. If not set, the default path `./config/virtual-fleet-config.json` is used.

## Configuration

### JSON Configuration of Virtual Fleet

The JSON configuration file should contain the following fields:

* **`vernemq-docker-image`**: MQTT broker docker image name/address
* **`vernemq-docker-tag`**: MQTT broker image tag
* **`external-server-docker-image`**: External server docker image name/address
* **`external-server-docker-tag`**: External server image tag
* **`gateway-docker-image`**: Module gateway docker image name/address
* **`gateway-docker-tag`**: Module gateway image tag
* **`vehicle-docker-image`**: Virtual vehicle docker image name/address
* **`vehicle-docker-tag`**: Virtual vehicle image tag
* **`start-port`**: Starting port used for communication between virtual vehicles and their module gateway. The port will be iterated, so 8 virtual vehicles will use ports 42000 - 42007. If a port is already used by another program, it will skip it and try the next one.
* **`stop-running-containers`**: If set to `true`, all running containers used by the virtual fleet will be stopped and removed. If set to `false`, the script will run alongside the already running containers, which may cause vehicles to exhibit undefined behavior. Default is `true`.
* **`vehicles`**: Array of vehicles to create
  * **`vehicles.name`**: Name of the vehicle. The name must be unique and adhere to the regular expression `^[a-z0-9_]*$`
  * **`vehicles.company`**: Name of the company that runs the vehicle. The name must adhere to the regular expression `^[a-z0-9_]*$`

Example JSON configuration: [config/virtual-fleet-config.json](config/virtual-fleet-config.json)

### Configuration of Individual Components

| Component         | Configuration Directory    | Repository Link                                                               |
|-------------------|----------------------------|-------------------------------------------------------------------------------|
| External Server   | `config/external-server`   | [External Server Repository](https://github.com/bringauto/external-server)    |
| Module Gateway    | `config/module-gateway`    | [Module Gateway Repository](https://github.com/bringauto/module-gateway)      |
| Virtual Vehicle   | `config/virtual-vehicle`   | [Virtual Vehicle Repository](https://github.com/bringauto/virtual-vehicle)    |
| MQTT Broker       | `config/vernemq`           | [VerneMQ Repository](https://github.com/bringauto/vernemq)                    |

## Usage

```bash
python3 VirtualFleetDocker.py --config=./config/virtual-fleet-config.json
```

Do not run multiple virtual fleets on the same machine. Since they will share the same MQTT broker, this can lead to conflicts in car names and operational issues.
