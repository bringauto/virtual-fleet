# Virtual fleet

README IS OUTDATED

Virtual fleet provides ability to simulate car movement in bringauto Fleet. Script will run one
pair of VirtualVehicle and BADaemon for each entry in json and simulate its movement for Fleet.


## Docker version
Script [VirtualFleetDocker.py](VirtualFleetDocker.py) uses docker images to run every vehicle. Example settings are present in [json file](virtual_fleet_docker.json)

### Cmd line arguments
* `--json_path=<string>` - path to json configuration file

### JSON configuration
* `daemon-docker-image` - daemon docker image name/address
* `daemon-docker-tag` - docker image tag for daemon
* `vehicle-docker-image` - virtual vehicle docker image name/address
* `vehicle-docker-tag` - virtual vehicle image tag for daemon
* `start-port` - starting port used for communication between vehicle and daemon, port will be iterated, so 8 virtual vehicles will use ports 42 000 - 42 007
* `mqtt-address` - ip address of mqtt broker
* `mqtt-port` - port of mqtt broker
* `daemon-ip` - address, where is ba-daemon listening
* `vehicles` - array of vehicles to create
* `vehicles.name` - name of the vehicle, used for mqtt topics, name must be unique and adhere to the regular expression ^[a-z0-9_]*$
* `bringauto/default/<name>/<daemon|industrial_portal>`
* `vehicles.docker-map-path` - path to map inside vehicle docker container used for simulation
* `vehicles.route` - name of route used for simulation, route must exist in given map
* `vehicles.stop-wait-seconds` - how many seconds should vehicle wait in stop
* `vehicles.message-period-millisecond` - minimal period of status message
